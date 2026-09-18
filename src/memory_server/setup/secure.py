"""비밀값 다루기: 화면에 안 보이는 입력, 키체인 쓰기(argv 노출 없이), 안전 삭제."""

from __future__ import annotations

import getpass
import os
import subprocess
import sys
from pathlib import Path


def ask_secret(prompt: str) -> str:
    while True:
        v = getpass.getpass(prompt + " (입력이 화면에 안 보인다): ").strip()
        if v:
            return v
        print("비어 있다. 다시.", file=sys.stderr)


def ask(prompt: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    v = input(f"{prompt}{suffix}: ").strip()
    return v or (default or "")


def confirm(prompt: str, default_yes: bool = False) -> bool:
    v = input(f"{prompt} {'[Y/n]' if default_yes else '[y/N]'}: ").strip().lower()
    if not v:
        return default_yes
    return v in ("y", "yes", "ㅇ")


def keychain_set(service: str, value: str, account: str = "memory") -> None:
    """`security -i` 로 stdin 에서 명령을 읽게 해 값이 프로세스 목록에 보이지 않게 한다."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    cmd = f'add-generic-password -a "{account}" -s "{service}" -w "{escaped}" -U\n'
    p = subprocess.run(["security", "-i"], input=cmd.encode("utf-8"), capture_output=True)
    if p.returncode != 0:
        raise RuntimeError(f"키체인 저장 실패 (service={service}, exit {p.returncode})")


def keychain_get(service: str) -> str | None:
    p = subprocess.run(["security", "find-generic-password", "-w", "-s", service], capture_output=True, text=True)
    return p.stdout.strip() if p.returncode == 0 and p.stdout.strip() else None


def write_private_file(path: Path, content: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)


def shred(path: Path) -> None:
    """덮어쓴 뒤 지운다. APFS 에서는 완전 삭제가 보장되지 않지만 없는 것보다 낫다."""
    try:
        if path.exists():
            size = path.stat().st_size
            with open(path, "r+b") as f:
                f.write(b"\0" * size)
                f.flush()
                os.fsync(f.fileno())
            path.unlink()
    except OSError:
        pass
