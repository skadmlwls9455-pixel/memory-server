"""쓰기 잠금 (SPEC 5).

모든 쓰기 도구와 백업 CLI는 MEMORY_HOME/.lock 하나에 flock을 건다.
"""

from __future__ import annotations

import fcntl
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class LockTimeout(TimeoutError):
    """정해진 시간 안에 잠금을 얻지 못했을 때."""


@contextmanager
def write_lock(lock_file: Path, timeout: float | None = 30.0, poll: float = 0.05) -> Iterator[None]:
    """.lock 파일에 배타적 flock을 건다.

    timeout=None이면 얻을 때까지 기다린다. 0이면 한 번만 시도한다.
    """
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_file, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        if timeout is None:
            fcntl.flock(fd, fcntl.LOCK_EX)
        else:
            deadline = time.monotonic() + timeout
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise LockTimeout(f"쓰기 잠금을 {timeout}초 안에 얻지 못했다: {lock_file}") from None
                    time.sleep(poll)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
