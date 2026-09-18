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


@pytest.fixture
def initialized(paths: MemoryPaths) -> MemoryPaths:
    init_memory_home(paths)
    return paths
