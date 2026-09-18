import subprocess
import sys

from memory_server.cli import main


def test_cli_init_writes_nothing_to_stdout(memory_home, capsys):
    assert main(["init"]) == 0
    out, err = capsys.readouterr()
    assert out == ""
    assert "MEMORY_HOME" in err
    assert (memory_home / "vault" / ".git").is_dir()


def test_cli_init_twice(memory_home, capsys):
    assert main(["init"]) == 0
    assert main(["init"]) == 0
    out, err = capsys.readouterr()
    assert out == ""
    assert "커밋할 변경 없음" in err


def test_cli_without_memory_home_fails_cleanly(monkeypatch, tmp_path):
    monkeypatch.delenv("MEMORY_HOME", raising=False)
    proc = subprocess.run(
        [sys.executable, "-m", "memory_server.cli", "init"],
        capture_output=True,
        text=True,
        env={k: v for k, v in __import__("os").environ.items() if k != "MEMORY_HOME"},
    )
    assert proc.returncode == 2
    assert proc.stdout == ""
    assert "MEMORY_HOME" in proc.stderr
