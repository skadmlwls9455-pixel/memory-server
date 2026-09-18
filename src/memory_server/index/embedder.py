"""임베딩 (SPEC 2, 3.5).

- 모델은 첫 임베딩 때 올린다. 로딩 실패는 EmbeddingUnavailable 로 알리고, 저장 실패로 이어지지 않는다.
- E5 계열은 검색어에 `query: `, 문서에 `passage: ` 접두어를 붙인다.
- 모델 최대 토큰을 넘는 문서는 TRUNCATED 로 표시한다.
- FakeEmbedder 는 테스트와 rebuild 검증용 결정적 임베딩이다. 모델을 받지 않는다.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from typing import Protocol, Sequence

from ..config import EmbeddingConfig
from ..logging_setup import get_logger


class EmbeddingUnavailable(RuntimeError):
    """모델을 올리지 못했거나 임베딩에 실패했다. 재시도 가능."""


@dataclass
class EmbedResult:
    vectors: list[list[float]]
    truncated: list[bool]


class Embedder(Protocol):
    model_id: str
    dimension: int

    @property
    def revision(self) -> str: ...

    def embed_documents(self, texts: Sequence[str]) -> EmbedResult: ...

    def embed_query(self, text: str) -> list[float]: ...

    def loaded(self) -> bool: ...


def is_e5(model_id: str) -> bool:
    return "e5" in model_id.lower()


def with_prefix(model_id: str, text: str, kind: str) -> str:
    if is_e5(model_id):
        return ("query: " if kind == "query" else "passage: ") + text
    return text


class SentenceTransformerEmbedder:
    """sentence-transformers 로컬 실행. 지연 로딩."""

    def __init__(self, cfg: EmbeddingConfig) -> None:
        self.model_id = cfg.model_id
        self.dimension = cfg.dimension
        self.device_pref = cfg.device
        self._model = None
        self._revision = ""
        self.log = get_logger()

    def loaded(self) -> bool:
        return self._model is not None

    @property
    def revision(self) -> str:
        return self._revision

    def _pick_device(self) -> str:
        if self.device_pref and self.device_pref != "auto":
            return self.device_pref
        try:
            import torch

            if torch.backends.mps.is_available():
                return "mps"
            if torch.cuda.is_available():
                return "cuda"
        except Exception:  # noqa: BLE001
            pass
        return "cpu"

    def _load(self) -> None:
        if self._model is not None:
            return
        try:
            from sentence_transformers import SentenceTransformer

            device = self._pick_device()
            model = SentenceTransformer(self.model_id, device=device)
        except Exception as e:  # noqa: BLE001 - 어떤 실패든 저장을 막으면 안 된다
            raise EmbeddingUnavailable(f"임베딩 모델을 올리지 못했다 ({type(e).__name__}).") from e
        dim = model.get_sentence_embedding_dimension()
        if dim != self.dimension:
            raise EmbeddingUnavailable(f"모델 차원 {dim} 이 설정 dimension {self.dimension} 과 다르다.")
        self._model = model
        self._revision = _model_revision(model, self.model_id)
        self.log.info("embedder loaded model=%s device=%s dim=%d", self.model_id, device, dim)

    def _max_tokens(self) -> int:
        try:
            return int(self._model.max_seq_length)  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            return 512

    def _token_len(self, text: str) -> int:
        try:
            return len(self._model.tokenizer(text, add_special_tokens=True, truncation=False)["input_ids"])  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            return 0

    def embed_documents(self, texts: Sequence[str]) -> EmbedResult:
        self._load()
        prefixed = [with_prefix(self.model_id, t, "passage") for t in texts]
        limit = self._max_tokens()
        truncated = [self._token_len(t) > limit for t in prefixed]
        try:
            vecs = self._model.encode(prefixed, normalize_embeddings=True, convert_to_numpy=True, batch_size=8)  # type: ignore[union-attr]
        except Exception as e:  # noqa: BLE001
            raise EmbeddingUnavailable(f"임베딩 실패 ({type(e).__name__}).") from e
        return EmbedResult(vectors=[[float(x) for x in v] for v in vecs], truncated=truncated)

    def embed_query(self, text: str) -> list[float]:
        self._load()
        try:
            v = self._model.encode([with_prefix(self.model_id, text, "query")], normalize_embeddings=True, convert_to_numpy=True)[0]  # type: ignore[union-attr]
        except Exception as e:  # noqa: BLE001
            raise EmbeddingUnavailable(f"임베딩 실패 ({type(e).__name__}).") from e
        return [float(x) for x in v]


def _model_revision(model, model_id: str) -> str:
    """가능하면 허브 스냅샷 해시를, 아니면 모델 설정 해시를 리비전으로 쓴다."""
    try:
        from huggingface_hub import try_to_load_from_cache

        path = try_to_load_from_cache(model_id, "config.json")
        if isinstance(path, str):
            m = re.search(r"/snapshots/([0-9a-f]{7,40})/", path)
            if m:
                return m.group(1)
    except Exception:  # noqa: BLE001
        pass
    try:
        cfg = model[0].auto_model.config.to_json_string()
        return hashlib.sha256(cfg.encode("utf-8")).hexdigest()[:12]
    except Exception:  # noqa: BLE001
        return "unknown"


class FakeEmbedder:
    """결정적 가짜 임베딩. 문자 2-gram 해시 기반이라 비슷한 문장은 비슷한 벡터를 얻는다.

    테스트와 rebuild 검증 전용. 실제 회상 품질과 무관하다.
    """

    model_id = "fake/ngram-hash"

    def __init__(self, dimension: int = 64, max_chars: int = 2000, fail: bool = False) -> None:
        self.dimension = dimension
        self.max_chars = max_chars
        self.fail = fail
        self._loaded = False

    def loaded(self) -> bool:
        return self._loaded

    @property
    def revision(self) -> str:
        return "v1"

    def _vec(self, text: str) -> list[float]:
        if self.fail:
            raise EmbeddingUnavailable("가짜 임베더가 실패하도록 설정됐다.")
        self._loaded = True
        v = [0.0] * self.dimension
        t = re.sub(r"\s+", " ", text.lower())
        grams = [t[i:i + 2] for i in range(len(t) - 1)] or [t]
        for g in grams:
            h = int(hashlib.md5(g.encode("utf-8")).hexdigest(), 16)
            v[h % self.dimension] += 1.0 if (h >> 8) % 2 else -1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]

    def embed_documents(self, texts: Sequence[str]) -> EmbedResult:
        return EmbedResult(vectors=[self._vec(t) for t in texts], truncated=[len(t) > self.max_chars for t in texts])

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)


def make_embedder(cfg: EmbeddingConfig) -> Embedder:
    if cfg.model_id.startswith("fake/"):
        return FakeEmbedder(dimension=cfg.dimension)
    return SentenceTransformerEmbedder(cfg)
