import json
import subprocess
from pathlib import Path

from memory_server import gitutil
from memory_server.config import load_config, load_tag_rules
from memory_server.init_cmd import init_memory_home


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


def test_init_creates_structure(paths):
    result = init_memory_home(paths)
    for d in (
        paths.vault,
        paths.memories,
        paths.system,
        paths.events_dir,
        paths.db_dir,
        paths.recovery,
        paths.backup,
    ):
        assert d.is_dir(), d
    assert paths.config_toml.is_file()
    assert paths.tag_rules_yaml.is_file()
    assert paths.core_json.is_file()
    assert paths.vault_gitignore.is_file()
    assert paths.lock_file.is_file()
    assert result.git_initialized is True
    assert result.initial_commit is not None


def test_init_files_are_loadable(initialized):
    load_config(initialized.config_toml)
    load_tag_rules(initialized.tag_rules_yaml)
    assert json.loads(initialized.core_json.read_text(encoding="utf-8")) == {"entries": []}


def test_vault_is_git_repo_with_precomposeunicode(initialized):
    assert gitutil.is_repo(initialized.vault)
    assert _git(initialized.vault, "config", "--get", "core.precomposeunicode") == "true"
    assert _git(initialized.vault, "config", "--get", "core.quotePath") == "false"
    # 원격은 없어야 한다 (SPEC 8.1).
    assert _git(initialized.vault, "remote") == ""


def test_gitignore_excludes_obsidian(initialized):
    text = initialized.vault_gitignore.read_text(encoding="utf-8")
    assert ".obsidian/" in text.splitlines()


def test_initial_commit_tracks_only_system_files(initialized):
    files = set(_git(initialized.vault, "ls-files").splitlines())
    assert files == {".gitignore", "_system/config.toml", "_system/tag_rules.yaml", "_system/core.json"}
    assert _git(initialized.vault, "status", "--porcelain") == ""


def test_db_and_runtime_outside_vault(initialized):
    assert initialized.vault not in initialized.db_dir.parents
    assert initialized.vault not in initialized.runtime.parents


def test_init_is_idempotent_and_never_overwrites(initialized):
    # 사용자가 설정을 고친 뒤 다시 init 해도 그대로여야 한다.
    initialized.config_toml.write_text("[recall]\nmin_similarity = 0.6\n", encoding="utf-8")
    head_before = _git(initialized.vault, "rev-parse", "HEAD")
    result = init_memory_home(initialized)
    assert result.created_dirs == []
    assert result.created_files == []
    assert result.git_initialized is False
    assert initialized.config_toml.read_text(encoding="utf-8") == "[recall]\nmin_similarity = 0.6\n"
    # 사용자 편집을 init이 몰래 커밋하지는 않는다.
    assert _git(initialized.vault, "rev-parse", "HEAD") == head_before
    assert result.initial_commit is None


def test_init_sets_local_identity_when_global_missing(initialized):
    assert _git(initialized.vault, "config", "--get", "user.name")
    assert _git(initialized.vault, "config", "--get", "user.email")


def test_commit_paths_only_adds_given_paths(initialized):
    a = initialized.system / "a.txt"
    b = initialized.system / "b.txt"
    a.write_text("a", encoding="utf-8")
    b.write_text("b", encoding="utf-8")
    sha = gitutil.commit_paths(initialized.vault, [a], "only a")
    assert sha
    tracked = set(_git(initialized.vault, "ls-files").splitlines())
    assert "_system/a.txt" in tracked
    assert "_system/b.txt" not in tracked
    assert gitutil.commit_paths(initialized.vault, [a], "nothing") is None
