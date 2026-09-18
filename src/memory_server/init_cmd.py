"""`memory init`: 폴더 구조와 vault Git 저장소 초기화 (SPEC 3.1, 3.2).

이미 있는 파일은 덮어쓰지 않는다. 여러 번 실행해도 안전하다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from . import gitutil
from .config import DEFAULT_CONFIG_TOML, DEFAULT_TAG_RULES_YAML
from .paths import MemoryPaths

VAULT_GITIGNORE = """\
# Obsidian 작업 파일은 이력에 넣지 않는다.
.obsidian/
.trash/
.DS_Store
"""

DEFAULT_CORE_JSON = {"entries": []}

# 개인 기억 저장소는 원격이 없고 한 사람만 쓴다. 커밋 작성자는 고정한다.
GIT_USER_NAME = "memory-server"
GIT_USER_EMAIL = "memory-server@localhost"


@dataclass
class InitResult:
    created_dirs: list[Path] = field(default_factory=list)
    created_files: list[Path] = field(default_factory=list)
    git_initialized: bool = False
    initial_commit: str | None = None


def _write_if_missing(path: Path, content: str, result: InitResult) -> None:
    if path.exists():
        return
    path.write_text(content, encoding="utf-8")
    result.created_files.append(path)


def init_memory_home(paths: MemoryPaths) -> InitResult:
    result = InitResult()

    for d in paths.all_dirs():
        if not d.exists():
            d.mkdir(parents=True)
            result.created_dirs.append(d)

    _write_if_missing(paths.vault_gitignore, VAULT_GITIGNORE, result)
    _write_if_missing(paths.config_toml, DEFAULT_CONFIG_TOML, result)
    _write_if_missing(paths.tag_rules_yaml, DEFAULT_TAG_RULES_YAML, result)
    _write_if_missing(
        paths.core_json,
        json.dumps(DEFAULT_CORE_JSON, ensure_ascii=False, indent=2) + "\n",
        result,
    )
    if not paths.lock_file.exists():
        paths.lock_file.touch(mode=0o600)
        result.created_files.append(paths.lock_file)

    vault = paths.vault
    if not gitutil.is_repo(vault):
        gitutil.run_git(vault, "init", "-q")
        result.git_initialized = True
    # 한글 파일명의 NFC/NFD 차이로 생기는 가짜 변경을 막는다 (SPEC 3.2).
    gitutil.run_git(vault, "config", "core.precomposeunicode", "true")
    # 한글 파일명이 git 출력에서 8진수로 깨져 보이지 않게 한다.
    gitutil.run_git(vault, "config", "core.quotePath", "false")
    if gitutil.get_config(vault, "user.name") is None:
        gitutil.run_git(vault, "config", "user.name", GIT_USER_NAME)
    if gitutil.get_config(vault, "user.email") is None:
        gitutil.run_git(vault, "config", "user.email", GIT_USER_EMAIL)

    # 이번 실행이 만든 vault 안 파일만 커밋한다. 사용자가 고친 파일은 doctor가 다룬다 (SPEC 5).
    vault_resolved = vault.resolve()
    to_commit = [p for p in result.created_files if vault_resolved in p.resolve().parents]
    result.initial_commit = gitutil.commit_paths(vault, to_commit, "init: vault 구조와 설정")
    return result
