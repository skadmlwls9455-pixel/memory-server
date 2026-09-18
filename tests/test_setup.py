"""setup 마법사의 순수 부분: B2 API 클라이언트(가짜 전송), Desktop 설정 병합, plist, config 편집, age 출력 파싱."""

import json

import pytest

from memory_server.setup.b2api import LIFECYCLE_RULE, UPLOAD_KEY_CAPS, B2Client, B2Error, check_upload_key
from memory_server.setup.desktop import merged_desktop_config, rclone_env, render_plist, write_desktop_config
from memory_server.setup.wizard import parse_age_keygen, set_config_value


class FakeB2:
    def __init__(self, caps=None):
        self.caps = caps or ["listBuckets", "writeBuckets", "listKeys", "writeKeys", "writeBucketRetentions", "deleteKeys"]
        self.buckets = {}
        self.keys = {}
        self.calls = []

    def __call__(self, url, headers, body):
        op = url.rsplit("/", 1)[-1]
        payload = json.loads(body) if body else {}
        self.calls.append(op)
        if op == "b2_authorize_account":
            assert headers["Authorization"].startswith("Basic ")
            return {"accountId": "acct", "authorizationToken": "tok", "apiInfo": {"storageApi": {"apiUrl": "https://api", "capabilities": self.caps}}}
        assert headers["Authorization"] == "tok"
        if op == "b2_list_buckets":
            return {"buckets": [b for b in self.buckets.values() if b["bucketName"] == payload.get("bucketName")]}
        if op == "b2_create_bucket":
            b = {"bucketId": f"id-{payload['bucketName']}", "bucketName": payload["bucketName"], "lifecycleRules": payload.get("lifecycleRules", []),
                 "fileLockConfiguration": {"value": {"isFileLockEnabled": payload.get("fileLockEnabled", False), "defaultRetention": {"mode": None}}}}
            self.buckets[b["bucketId"]] = b
            return b
        if op == "b2_update_bucket":
            b = self.buckets[payload["bucketId"]]
            b["fileLockConfiguration"]["value"]["defaultRetention"] = payload["defaultRetention"]
            b["lifecycleRules"] = payload["lifecycleRules"]
            return b
        if op == "b2_create_key":
            k = {"applicationKeyId": f"k{len(self.keys)}", "applicationKey": "SECRET", "keyName": payload["keyName"], "capabilities": payload["capabilities"], "bucketId": payload.get("bucketId")}
            self.keys[k["applicationKeyId"]] = k
            return k
        if op == "b2_list_keys":
            return {"keys": list(self.keys.values())}
        if op == "b2_delete_key":
            self.keys.pop(payload["applicationKeyId"])
            return {}
        raise AssertionError(op)


def test_b2_flow_bucket_retention_keys():
    fake = FakeB2()
    c = B2Client(transport=fake)
    c.authorize("kid", "key")
    b = c.create_locked_bucket("memory-vault-abc")
    assert b.file_lock_enabled and b.lifecycle_rules == [LIFECYCLE_RULE]
    b = c.ensure_retention_and_lifecycle(b)
    assert b.retention_mode == "compliance" and b.retention_days == 30
    # 다시 만들면 그대로
    assert c.create_locked_bucket("memory-vault-abc").id == b.id
    up = c.create_key("upload", b.id, UPLOAD_KEY_CAPS)
    assert up.secret == "SECRET" and "deleteFiles" not in up.capabilities
    assert check_upload_key(c.list_keys(), up.key_id, b.id) == []
    bad = c.create_key("del", b.id, UPLOAD_KEY_CAPS + ["deleteFiles"])
    assert any("deleteFiles" in p for p in check_upload_key(c.list_keys(), bad.key_id, b.id))
    other = c.create_key("wide", None, UPLOAD_KEY_CAPS)
    assert any("한정" in p for p in check_upload_key(c.list_keys(), other.key_id, b.id))
    c.delete_key(bad.key_id)
    assert bad.key_id not in fake.keys
    assert check_upload_key(c.list_keys(), "nope", b.id) == ["업로드 키를 계정에서 찾을 수 없다"]


def test_b2_requires_capabilities():
    c = B2Client(transport=FakeB2(caps=["listBuckets"]))
    with pytest.raises(B2Error):
        c.authorize("k", "s")


def test_b2_refuses_unlocked_existing_bucket():
    fake = FakeB2()
    c = B2Client(transport=fake)
    c.authorize("k", "s")
    fake.buckets["x"] = {"bucketId": "x", "bucketName": "old", "lifecycleRules": [], "fileLockConfiguration": {"value": {"isFileLockEnabled": False}}}
    with pytest.raises(B2Error):
        c.create_locked_bucket("old")


def test_desktop_config_merge_keeps_other_servers(tmp_path):
    p = tmp_path / "claude_desktop_config.json"
    p.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}, "theme": "dark"}), encoding="utf-8")
    backup = write_desktop_config(p, tmp_path / "uv", tmp_path / "srv", tmp_path / "home")
    cfg = json.loads(p.read_text(encoding="utf-8"))
    assert cfg["theme"] == "dark" and cfg["mcpServers"]["other"] == {"command": "x"}
    m = cfg["mcpServers"]["memory"]
    assert m["args"] == ["run", "--directory", str(tmp_path / "srv"), "memory", "serve"]
    assert m["env"] == {"MEMORY_HOME": str(tmp_path / "home")}
    assert backup and backup.exists()
    assert merged_desktop_config(None, tmp_path / "uv", tmp_path / "srv", tmp_path / "home")["mcpServers"]["memory"]["command"] == str(tmp_path / "uv")


def test_render_plist_and_rclone_env(tmp_path):
    from pathlib import Path

    tpl = (Path(__file__).resolve().parents[1] / "deploy" / "com.memory.backup-check.plist").read_text(encoding="utf-8")
    out = render_plist(tpl, Path("/Users/u"), Path("/Users/u/memory-test"), Path("/Users/u/src"), Path("/Users/u/.local/bin/uv"))
    assert "__" not in out.replace("__PATH__", "")  # 자리표시자 없음
    assert "<string>/Users/u/memory-test</string>" in out and "<integer>3600</integer>" in out
    env = rclone_env("b2:bucket", "id", "key")
    assert env == {"RCLONE_CONFIG_B2_TYPE": "b2", "RCLONE_CONFIG_B2_ACCOUNT": "id", "RCLONE_CONFIG_B2_KEY": "key"}


def test_set_config_value_preserves_comments(initialized):
    before = initialized.config_toml.read_text(encoding="utf-8")
    set_config_value(initialized, "backup", "age_recipient", "age1abc")
    set_config_value(initialized, "backup", "rclone_remote", "b2:my-bucket")
    set_config_value(initialized, "backup", "age_recipient", "age1xyz")  # 덮어쓰기
    from memory_server.config import load_config

    cfg = load_config(initialized.config_toml)
    assert cfg.backup.age_recipient == "age1xyz" and cfg.backup.rclone_remote == "b2:my-bucket"
    after = initialized.config_toml.read_text(encoding="utf-8")
    assert after.count("age_recipient") == before.count("age_recipient")  # 줄 수 유지
    assert "# age 공개키" in after  # 주석 보존
    assert cfg.embedding.model_id == "fake/ngram-hash"  # 다른 절 그대로


def test_set_config_value_adds_missing_section(initialized):
    text = initialized.config_toml.read_text(encoding="utf-8").split("[backup]")[0]
    initialized.config_toml.write_text(text, encoding="utf-8")
    set_config_value(initialized, "backup", "rclone_remote", "b2:x")
    from memory_server.config import load_config

    assert load_config(initialized.config_toml).backup.rclone_remote == "b2:x"


def test_parse_age_keygen():
    out = "# created: 2026-09-18T10:00:00+09:00\n# public key: age1ql3z7hjy54pw3hyww5ayyfg7zqgvc7w3j2elw8zmrj2kg5sfn9aqmcac8p\nAGE-SECRET-KEY-1QYQSZQGPQYQSZQGPQYQSZQGPQYQSZQGPQYQSZQGPQYQSZQGPQYQS3PTVJA\n"
    pub, priv = parse_age_keygen(out)
    assert pub.startswith("age1") and priv.startswith("AGE-SECRET-KEY-1")


def test_cli_setup_refuses_on_linux(memory_home, capsys):
    import platform

    from memory_server.cli import main

    if platform.system() == "Darwin":
        pytest.skip("맥에서는 실제로 실행된다")
    assert main(["setup", "check"]) == 1
    assert "맥북" in capsys.readouterr().err
