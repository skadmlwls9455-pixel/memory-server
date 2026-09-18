from __future__ import annotations

from pathlib import Path

import pytest

from memory_server.init_cmd import init_memory_home
from memory_server.paths import ENV_VAR, MemoryPaths, get_paths


@pytest.fixture
def memory_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """테스트 전용 MEMORY_HOME. 임시 디렉터리만 쓴다."""
    home = tmp_path / "memory"
    monkeypatch.setenv(ENV_VAR, str(home))
    # 전역 git 설정에 기대지 않는다.
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "no-global-gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    return home


@pytest.fixture
def paths(memory_home: Path) -> MemoryPaths:
    return get_paths()


FAKE_EMBEDDING_TOML = """\
[embedding]
model_id = "fake/ngram-hash"
dimension = 64
"""


def use_fake_embedder(paths: MemoryPaths) -> None:
    """테스트는 모델을 받지 않는다. 결정적 가짜 임베더로 바꾼다 (실제 모델 시험은 MEMORY_REAL_MODEL=1)."""
    text = paths.config_toml.read_text(encoding="utf-8")
    paths.config_toml.write_text(FAKE_EMBEDDING_TOML + text.split("[recall]", 1)[1].join(["[recall]", ""]) if False else _swap_embedding(text), encoding="utf-8")
    import subprocess

    subprocess.run(["git", "-C", str(paths.vault), "commit", "-q", "-am", "test: fake embedder"], check=True, capture_output=True)


def _swap_embedding(text: str) -> str:
    head, _, rest = text.partition("[embedding]")
    _, _, after = rest.partition("[recall]")
    return head + FAKE_EMBEDDING_TOML + "\n[recall]" + after


@pytest.fixture
def initialized(paths: MemoryPaths) -> MemoryPaths:
    init_memory_home(paths)
    use_fake_embedder(paths)
    return paths


@pytest.fixture
def real_model_enabled() -> bool:
    import os

    return os.environ.get("MEMORY_REAL_MODEL") == "1"
