"""config.toml과 tag_rules.yaml 로딩 (SPEC 2, 4, 7).

config.toml에는 비밀값을 넣지 않는다. 없는 키는 기본값으로 채운다.
"""

from __future__ import annotations

import tomllib
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

TAG_CATEGORIES = ("주제", "사람", "프로젝트", "상태", "시기")
EXPANDABLE_TAG_CATEGORIES = ("주제", "사람", "프로젝트")

DEFAULT_CONFIG_TOML = """\
# 개인 AI 기억 시스템 설정. 비밀값은 넣지 않는다.

[embedding]
model_id = "BAAI/bge-m3"
dimension = 1024
# 경량 대안: model_id = "intfloat/multilingual-e5-small", dimension = 384
# device: "auto" 면 mps > cuda > cpu 순서로 고른다.
device = "auto"
# 서버 시작 직후 백그라운드에서 모델을 올린다. 첫 recall 대기를 없앤다. 메모리를 아끼려면 false.
preload = true

[recall]
# T05 (2026-09-18, data/eval, scripts/run_t05.py) 로 조정한 값.
#   BAAI/bge-m3: 0.45 (가능 구간 0.435~0.50. 평가 정답 유사도 최소 0.499, 무관 1위 최대 0.423.
#                구간 아래쪽을 고른 이유: 이름 없는 짧은 질의("회사 어디 다녀?")가 0.45 근처로 떨어진다)
#   intfloat/multilingual-e5-small: 0.83 (가능 구간 0.82~0.84)
# 모델을 바꾸면 run_t05.py 를 다시 돌려 이 값을 바꾸고, 그 전까지 relevance_calibrated = false 로 둔다.
min_similarity = 0.45
relevance_calibrated = true
epsilon = 0.02
similar_exists_threshold = 0.92

[heat]
initial = 0.5
boost = 0.3
half_life_days = 30
reheat_window_hours = 6

[write]
dedupe_window_minutes = 10

[backup]
min_interval_hours = 24
stale_warning_hours = 48
usage_warning_gb = 8
# age 공개키 (age1...). 개인키는 절대 여기 두지 않는다.
age_recipient = ""
# rclone 원격과 경로. 예: "b2:my-memory-bucket". 자격증명은 macOS 키체인에 두고 아래 서비스 이름으로 읽는다.
rclone_remote = ""
keychain_service_key_id = "memory-b2-key-id"
keychain_service_key = "memory-b2-key"
"""

DEFAULT_TAG_RULES_YAML = """\
# 태그는 "분류/이름" 두 단계다. 분류는 아래 다섯 가지뿐이다.
# values가 비어 있으면 그 분류의 이름은 자유다. 채우면 그 목록만 허용한다.
categories:
  주제:
    values: []
  사람:
    values: []
  프로젝트:
    values: []
  상태:
    values: []
  시기:
    values: []
"""


class ConfigError(ValueError):
    """설정 파일이 없거나 형식이 틀렸을 때."""


@dataclass(frozen=True)
class EmbeddingConfig:
    model_id: str = "BAAI/bge-m3"
    dimension: int = 1024
    device: str = "auto"
    preload: bool = True


@dataclass(frozen=True)
class RecallConfig:
    min_similarity: float = 0.45
    relevance_calibrated: bool = True
    epsilon: float = 0.02
    similar_exists_threshold: float = 0.92


@dataclass(frozen=True)
class HeatConfig:
    initial: float = 0.5
    boost: float = 0.3
    half_life_days: float = 30.0
    reheat_window_hours: float = 6.0


@dataclass(frozen=True)
class WriteConfig:
    dedupe_window_minutes: float = 10.0


@dataclass(frozen=True)
class BackupConfig:
    min_interval_hours: float = 24.0
    stale_warning_hours: float = 48.0
    usage_warning_gb: float = 8.0
    age_recipient: str = ""
    rclone_remote: str = ""
    keychain_service_key_id: str = "memory-b2-key-id"
    keychain_service_key: str = "memory-b2-key"


@dataclass(frozen=True)
class Config:
    embedding: EmbeddingConfig = field(default_factory=EmbeddingConfig)
    recall: RecallConfig = field(default_factory=RecallConfig)
    heat: HeatConfig = field(default_factory=HeatConfig)
    write: WriteConfig = field(default_factory=WriteConfig)
    backup: BackupConfig = field(default_factory=BackupConfig)


def _section(data: dict[str, Any], name: str, cls: type) -> Any:
    raw = data.get(name, {})
    if not isinstance(raw, dict):
        raise ConfigError(f"config.toml [{name}] 은 테이블이어야 한다.")
    allowed = set(cls.__dataclass_fields__)
    unknown = set(raw) - allowed
    if unknown:
        raise ConfigError(f"config.toml [{name}] 에 모르는 키가 있다: {sorted(unknown)}")
    try:
        return cls(**raw)
    except TypeError as e:  # pragma: no cover - dataclass가 잡아 주는 형식 오류
        raise ConfigError(f"config.toml [{name}] 형식 오류: {e}") from e


def parse_config(text: str) -> Config:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"config.toml 파싱 실패: {e}") from e
    cfg = Config(
        embedding=_section(data, "embedding", EmbeddingConfig),
        recall=_section(data, "recall", RecallConfig),
        heat=_section(data, "heat", HeatConfig),
        write=_section(data, "write", WriteConfig),
        backup=_section(data, "backup", BackupConfig),
    )
    if cfg.embedding.dimension <= 0:
        raise ConfigError("embedding.dimension 은 양수여야 한다.")
    if not 0.0 <= cfg.recall.min_similarity <= 1.0:
        raise ConfigError("recall.min_similarity 는 0~1 사이여야 한다.")
    if cfg.heat.half_life_days <= 0:
        raise ConfigError("heat.half_life_days 는 양수여야 한다.")
    return cfg


def load_config(path: Path) -> Config:
    if not path.exists():
        raise ConfigError(f"설정 파일이 없다: {path}. `memory init`을 먼저 실행한다.")
    return parse_config(path.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class TagRules:
    # 분류 -> 허용 이름 집합. 빈 집합이면 이름은 자유.
    categories: dict[str, frozenset[str]]

    def is_valid(self, tag: str) -> bool:
        tag = unicodedata.normalize("NFC", tag.strip())
        if tag.count("/") != 1:
            return False
        category, name = tag.split("/")
        if not category or not name:
            return False
        if category not in self.categories:
            return False
        allowed = self.categories[category]
        return not allowed or name in allowed


def parse_tag_rules(text: str) -> TagRules:
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"tag_rules.yaml 파싱 실패: {e}") from e
    raw = data.get("categories") if isinstance(data, dict) else None
    if not isinstance(raw, dict):
        raise ConfigError("tag_rules.yaml 에 categories 매핑이 있어야 한다.")
    categories: dict[str, frozenset[str]] = {}
    for name, body in raw.items():
        name = unicodedata.normalize("NFC", str(name))
        if name not in TAG_CATEGORIES:
            raise ConfigError(f"tag_rules.yaml 에 허용되지 않은 분류가 있다: {name}")
        values = (body or {}).get("values", []) if isinstance(body, dict) else []
        if not isinstance(values, list):
            raise ConfigError(f"tag_rules.yaml {name}.values 는 목록이어야 한다.")
        categories[name] = frozenset(unicodedata.normalize("NFC", str(v)) for v in values)
    missing = set(TAG_CATEGORIES) - set(categories)
    if missing:
        raise ConfigError(f"tag_rules.yaml 에 빠진 분류가 있다: {sorted(missing)}")
    return TagRules(categories=categories)


def load_tag_rules(path: Path) -> TagRules:
    if not path.exists():
        raise ConfigError(f"태그 규칙 파일이 없다: {path}. `memory init`을 먼저 실행한다.")
    return parse_tag_rules(path.read_text(encoding="utf-8"))
