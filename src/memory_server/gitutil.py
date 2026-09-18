"""vault의 로컬 Git 저장소 도우미. 원격은 연결하지 않는다."""

from __future__ import annotations

import subprocess
from pathlib import Path


class GitError(RuntimeError):
    pass


def run_git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )
    if check and proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} 실패: {proc.stderr.strip()}")
    return proc


def is_repo(repo: Path) -> bool:
    return (repo / ".git").is_dir()


def get_config(repo: Path, key: str) -> str | None:
    proc = run_git(repo, "config", "--get", key, check=False)
    return proc.stdout.strip() if proc.returncode == 0 else None


def head_exists(repo: Path) -> bool:
    return run_git(repo, "rev-parse", "--verify", "-q", "HEAD", check=False).returncode == 0


def commit_paths(repo: Path, paths: list[Path], message: str) -> str | None:
    """바뀐 경로만 add하고 커밋한다 (SPEC 5). `git add -A`는 쓰지 않는다.

    스테이징할 변경이 없으면 None을 돌려준다.
    """
    rel = [str(p.resolve().relative_to(repo.resolve())) for p in paths]
    if not rel:
        return None
    run_git(repo, "add", "--", *rel)
    staged = run_git(repo, "diff", "--cached", "--quiet", check=False)
    if staged.returncode == 0:
        return None
    run_git(repo, "commit", "-q", "-m", message)
    return run_git(repo, "rev-parse", "HEAD").stdout.strip()
