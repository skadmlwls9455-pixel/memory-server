"""MEMORY_HOME 기반 경로 (SPEC 3.1).

모든 경로는 환경변수 MEMORY_HOME에서 나온다. 기본 위치를 코드에 넣지 않는다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ENV_VAR = "MEMORY_HOME"


class MemoryHomeError(RuntimeError):
    """MEMORY_HOME이 없거나 쓸 수 없는 값일 때."""


@dataclass(frozen=True)
class MemoryPaths:
    home: Path

    # vault: 원본. 로컬 Git 저장소.
    @property
    def vault(self) -> Path:
        return self.home / "vault"

    @property
    def memories(self) -> Path:
        return self.vault / "memories"

    @property
    def core_md(self) -> Path:
        return self.vault / "core.md"

    @property
    def system(self) -> Path:
        return self.vault / "_system"

    @property
    def core_json(self) -> Path:
        return self.system / "core.json"

    @property
    def events_dir(self) -> Path:
        return self.system / "events"

    @property
    def config_toml(self) -> Path:
        return self.system / "config.toml"

    @property
    def tag_rules_yaml(self) -> Path:
        return self.system / "tag_rules.yaml"

    @property
    def vault_gitignore(self) -> Path:
        return self.vault / ".gitignore"

    @property
    def vault_git(self) -> Path:
        return self.vault / ".git"

    # 캐시와 런타임
    @property
    def db_dir(self) -> Path:
        return self.home / "db"

    @property
    def db_file(self) -> Path:
        return self.db_dir / "memory.db"

    @property
    def runtime(self) -> Path:
        return self.home / "runtime"

    @property
    def recovery(self) -> Path:
        return self.runtime / "recovery"

    @property
    def backup(self) -> Path:
        return self.runtime / "backup"

    @property
    def server_log(self) -> Path:
        return self.runtime / "server.log"

    @property
    def lock_file(self) -> Path:
        return self.home / ".lock"

    def events_file(self, year: int, month: int) -> Path:
        return self.events_dir / f"events-{year:04d}-{month:02d}.jsonl"

    def memories_dir_for(self, year: int, month: int) -> Path:
        return self.memories / f"{year:04d}" / f"{month:02d}"

    def all_dirs(self) -> list[Path]:
        """init이 만들어야 하는 디렉터리 전부."""
        return [
            self.home,
            self.vault,
            self.memories,
            self.system,
            self.events_dir,
            self.db_dir,
            self.runtime,
            self.recovery,
            self.backup,
        ]

    def ensure_inside_vault(self, path: Path) -> Path:
        """경로가 vault 안에 있는지 확인한다. 심볼릭 링크를 풀어서 비교한다 (SPEC 3.2)."""
        resolved = path.resolve()
        vault = self.vault.resolve()
        if resolved != vault and vault not in resolved.parents:
            raise MemoryHomeError(f"vault 밖 경로에 쓸 수 없다: {path}")
        return resolved


def get_paths(env: dict[str, str] | None = None) -> MemoryPaths:
    """환경변수에서 MemoryPaths를 만든다. 없으면 MemoryHomeError."""
    env = os.environ if env is None else env
    raw = env.get(ENV_VAR, "").strip()
    if not raw:
        raise MemoryHomeError(f"환경변수 {ENV_VAR}이 설정되어 있지 않다.")
    home = Path(raw).expanduser()
    if not home.is_absolute():
        raise MemoryHomeError(f"{ENV_VAR}은 절대 경로여야 한다: {raw}")
    return MemoryPaths(home=home)
