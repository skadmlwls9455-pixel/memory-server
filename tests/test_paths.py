from pathlib import Path

import pytest

from memory_server.paths import ENV_VAR, MemoryHomeError, get_paths


def test_missing_env_raises(monkeypatch):
    monkeypatch.delenv(ENV_VAR, raising=False)
    with pytest.raises(MemoryHomeError):
        get_paths()


def test_empty_env_raises(monkeypatch):
    monkeypatch.setenv(ENV_VAR, "   ")
    with pytest.raises(MemoryHomeError):
        get_paths()


def test_relative_env_raises(monkeypatch):
    monkeypatch.setenv(ENV_VAR, "relative/dir")
    with pytest.raises(MemoryHomeError):
        get_paths()


def test_paths_derive_from_home(memory_home: Path):
    p = get_paths()
    assert p.home == memory_home
    assert p.vault == memory_home / "vault"
    assert p.memories == memory_home / "vault" / "memories"
    assert p.core_md == memory_home / "vault" / "core.md"
    assert p.core_json == memory_home / "vault" / "_system" / "core.json"
    assert p.config_toml == memory_home / "vault" / "_system" / "config.toml"
    assert p.tag_rules_yaml == memory_home / "vault" / "_system" / "tag_rules.yaml"
    assert p.events_file(2026, 9) == memory_home / "vault" / "_system" / "events" / "events-2026-09.jsonl"
    assert p.memories_dir_for(2026, 9) == memory_home / "vault" / "memories" / "2026" / "09"
    assert p.db_file == memory_home / "db" / "memory.db"
    assert p.recovery == memory_home / "runtime" / "recovery"
    assert p.backup == memory_home / "runtime" / "backup"
    assert p.server_log == memory_home / "runtime" / "server.log"
    assert p.lock_file == memory_home / ".lock"


def test_no_hardcoded_home_in_source():
    src = Path(__file__).resolve().parents[1] / "src"
    offenders = [p for p in src.rglob("*.py") if "~/memory" in p.read_text(encoding="utf-8")]
    assert offenders == []


def test_ensure_inside_vault_accepts_vault_children(initialized):
    target = initialized.memories / "2026" / "09"
    target.mkdir(parents=True)
    assert initialized.ensure_inside_vault(target) == target.resolve()


def test_ensure_inside_vault_rejects_outside(initialized, tmp_path: Path):
    with pytest.raises(MemoryHomeError):
        initialized.ensure_inside_vault(tmp_path / "elsewhere")


def test_ensure_inside_vault_rejects_symlink_escape(initialized, tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    link = initialized.memories / "escape"
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(MemoryHomeError):
        initialized.ensure_inside_vault(link / "note.md")
