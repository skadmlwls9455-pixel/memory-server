import os
import subprocess
import sys
import textwrap
import time

import pytest

from memory_server.lock import LockTimeout, write_lock


def test_lock_creates_file_and_releases(paths):
    assert not paths.lock_file.exists()
    with write_lock(paths.lock_file, timeout=1):
        assert paths.lock_file.exists()
    # 풀린 뒤에는 바로 다시 얻을 수 있다.
    with write_lock(paths.lock_file, timeout=0):
        pass


def test_second_holder_times_out_while_held(initialized):
    with write_lock(initialized.lock_file, timeout=1):
        start = time.monotonic()
        with pytest.raises(LockTimeout):
            with write_lock(initialized.lock_file, timeout=0.3):
                pass
        assert time.monotonic() - start >= 0.3


def test_lock_released_on_exception(initialized):
    with pytest.raises(RuntimeError):
        with write_lock(initialized.lock_file, timeout=1):
            raise RuntimeError("boom")
    with write_lock(initialized.lock_file, timeout=0):
        pass


def test_lock_shared_across_processes(initialized):
    """다른 프로세스(백업 CLI 역할)가 잡고 있으면 이쪽은 기다린다."""
    script = textwrap.dedent(
        """
        import sys, time
        from pathlib import Path
        from memory_server.lock import write_lock
        with write_lock(Path(sys.argv[1]), timeout=1):
            print("held", flush=True)
            time.sleep(float(sys.argv[2]))
        """
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", script, str(initialized.lock_file), "1.0"],
        stdout=subprocess.PIPE,
        text=True,
        env=os.environ,
    )
    try:
        assert proc.stdout.readline().strip() == "held"
        with pytest.raises(LockTimeout):
            with write_lock(initialized.lock_file, timeout=0.2):
                pass
        start = time.monotonic()
        with write_lock(initialized.lock_file, timeout=5):
            waited = time.monotonic() - start
        assert waited > 0.3  # 상대가 놓을 때까지 실제로 기다렸다
    finally:
        proc.wait(timeout=5)
