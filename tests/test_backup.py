"""T11 과 백업 절차 (SPEC 8.2). age, rclone 은 가짜다."""

import io
import tarfile
from datetime import timedelta

import pytest

from memory_server.app import build_app
from memory_server.backup.providers import FakeEncryptor, FakeUploader, ProviderError, sha256_file
from memory_server.backup.restore import UnsafeArchive, restore_snapshot, safe_extract
from memory_server.backup.schedule import should_backup, staleness_warning
from memory_server.backup.snapshot import BackupService
from memory_server.backup.state import load_state
from memory_server.config import BackupConfig
from memory_server.stats import collect_warnings, events_since
from tests.helpers import Clock

CFG = BackupConfig(age_recipient="age1testrecipient", rclone_remote="fake:bucket")


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def app(initialized, clock):
    a = build_app(initialized, now=clock)
    yield a
    a.close()


@pytest.fixture
def server_dir(tmp_path):
    d = tmp_path / "server"
    (d / "src").mkdir(parents=True)
    (d / "src" / "a.py").write_text("print(1)\n")
    (d / ".venv").mkdir()
    (d / ".venv" / "big.bin").write_bytes(b"x" * 1000)
    (d / "RESTORE.md").write_text("복원 절차\n")
    return d


def _svc(app, uploader=None, enc=None, server_dir=None):
    return BackupService(
        app.paths, CFG, enc or FakeEncryptor(), uploader if uploader is not None else FakeUploader(),
        server_dir=server_dir, now=app.now, events_since=lambda since: events_since(app, since),
    )


def test_backup_roundtrip_and_contents(app, server_dir):
    app.store.remember("백업될 기억", "본문")
    (app.paths.db_dir / "junk.bin").write_bytes(b"x")
    up = FakeUploader()
    svc = _svc(app, up, server_dir=server_dir)
    r = svc.run_backup()
    assert r.verified and r.uploaded and r.name.startswith("snapshots/") and r.name.endswith(".tar.gz.age")
    assert r.captured_at == app.now().isoformat(timespec="seconds")
    st = load_state(app.paths)
    assert st.last_verified.name == r.name and st.last_verified.sha256 == r.sha256 and st.pending == []
    assert st.remote_usage.bytes == r.size
    # staging 과 평문은 남지 않는다
    assert not list(app.paths.backup.glob("staging-*")) and not list(svc.pending_dir.glob("*"))
    # 내용: vault(.git 포함) 와 server(가상환경 제외). DB, runtime 없음
    data = up.objects[r.name]
    payload = data.split(b"\n", 2)[2]
    names = tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz").getnames()
    assert any(n.startswith("vault/.git/") for n in names)
    assert any("백업될 기억" in n for n in names)
    assert "server/src/a.py" in names and "server/RESTORE.md" in names
    assert not any(".venv" in n for n in names)
    assert not any(n.startswith("db") or n.startswith("runtime") or "memory.db" in n for n in names)


def test_t11_upload_failure_then_retry_same_name(app):
    app.store.remember("x", "b")
    up = FakeUploader(fail_uploads=1)
    svc = _svc(app, up)
    r = svc.run_backup()
    assert not r.verified and r.errors
    st = load_state(app.paths)
    assert st.last_verified is None and len(st.pending) == 1
    pending_file = svc.pending_dir / r.name.split("/")[-1]
    assert pending_file.exists() and sha256_file(pending_file) == r.sha256
    # 다음 점검: 같은 파일, 같은 이름
    retried, fresh = svc.check()
    assert retried.retried == [r.name] and up.upload_calls == [r.name, r.name]
    st = load_state(app.paths)
    assert st.last_verified.name == r.name and st.pending == [] and not pending_file.exists()
    assert fresh.skipped_reason  # 방금 verified 라 새 백업은 안 만든다


def test_t11_verify_mismatch_keeps_pending(app):
    app.store.remember("x", "b")
    up = FakeUploader(corrupt_next=True)
    svc = _svc(app, up)
    r = svc.run_backup()
    assert r.uploaded and not r.verified and "해시" in r.errors[0]
    assert load_state(app.paths).pending[0].name == r.name


def test_t11_reupload_of_old_snapshot_does_not_refresh_freshness(app, clock):
    app.store.remember("x", "b")
    up = FakeUploader()
    svc = _svc(app, up)
    old = svc.run_backup()
    clock.advance(days=2)
    app.store.remember("y", "b")
    new = svc.run_backup()
    assert load_state(app.paths).last_verified.name == new.name
    # 옛 암호문을 대기열에 다시 넣고 재업로드해도 최신성은 그대로
    st = load_state(app.paths)
    from memory_server.backup.state import Snapshot, save_state

    old_snap = Snapshot(name=old.name, captured_at=old.captured_at, size=old.size, sha256=old.sha256)
    (svc.pending_dir).mkdir(exist_ok=True)
    (svc.pending_dir / old.name.split("/")[-1]).write_bytes(up.objects[old.name])
    st.pending.append(old_snap)
    save_state(app.paths, st)
    clock.advance(hours=1)
    retried = svc.retry_pending()
    assert retried.retried == [old.name]
    assert load_state(app.paths).last_verified.name == new.name


def test_t11_48h_gap_warning(app, clock):
    assert staleness_warning(None, CFG, clock()).startswith("BACKUP_NOT_CONFIGURED")
    app.store.remember("x", "b")
    svc = _svc(app)
    svc.run_backup()
    assert staleness_warning(load_state(app.paths), CFG, clock()) is None
    clock.advance(hours=47)
    assert staleness_warning(load_state(app.paths), CFG, clock()) is None
    clock.advance(hours=2)
    w = staleness_warning(load_state(app.paths), CFG, clock())
    assert w and w.startswith("BACKUP_STALE")
    # get_core / stats 앞에 붙는다
    assert any(x.startswith("BACKUP_STALE") for x in collect_warnings(app))


def test_schedule_rule(app, clock):
    app.store.remember("x", "b")
    svc = _svc(app)
    svc.run_backup()
    st = load_state(app.paths)
    assert should_backup(st, CFG, clock() + timedelta(hours=23), 5)[0] is False
    assert should_backup(st, CFG, clock() + timedelta(hours=25), 0)[0] is False  # 새 이벤트 없음
    assert should_backup(st, CFG, clock() + timedelta(hours=25), 1)[0] is True
    assert should_backup(None, CFG, clock(), 0)[0] is True
    # check(): 25시간 뒤 새 이벤트가 있으면 새 스냅샷
    clock.advance(hours=25)
    app.store.remember("y", "b")
    _, fresh = svc.check()
    assert fresh.verified and fresh.name != st.last_verified.name


def test_backup_without_recipient_refuses(app):
    svc = BackupService(app.paths, BackupConfig(), FakeEncryptor(), FakeUploader(), now=app.now)
    r = svc.run_backup()
    assert not r.verified and "age_recipient" in r.errors[0]


def test_encrypt_failure_cleans_staging(app):
    app.store.remember("x", "b")
    svc = _svc(app, enc=FakeEncryptor(fail=True))
    r = svc.run_backup()
    assert r.errors and not list(app.paths.backup.glob("staging-*"))


def test_backup_holds_write_lock_during_copy(app):
    """복사 중에는 잠금을 잡고, 끝나면 푼다."""
    from memory_server.lock import write_lock

    app.store.remember("x", "b")
    svc = _svc(app)
    seen = {}
    orig = svc._copy_sources

    def spy(staging):
        try:
            with write_lock(app.paths.lock_file, timeout=0):
                seen["locked_during_copy"] = False
        except Exception:
            seen["locked_during_copy"] = True
        orig(staging)

    svc._copy_sources = spy
    svc.run_backup()
    assert seen["locked_during_copy"] is True
    with write_lock(app.paths.lock_file, timeout=0):
        pass


def test_restore_drill_recorded_in_stats(app):
    svc = _svc(app)
    d = svc.record_restore_drill()
    from memory_server.stats import collect_stats

    assert collect_stats(app)["backup"]["last_restore_drill"] == d == app.now().date().isoformat()


# ----------------------------------------------------------------------------- 복원


def _tar_with(tmp_path, members):
    p = tmp_path / "t.tar.gz"
    with tarfile.open(p, "w:gz") as tf:
        for name, kind in members:
            if kind == "file":
                info = tarfile.TarInfo(name)
                info.size = 1
                tf.addfile(info, io.BytesIO(b"x"))
            elif kind == "dir":
                info = tarfile.TarInfo(name)
                info.type = tarfile.DIRTYPE
                tf.addfile(info)
            elif kind == "sym":
                info = tarfile.TarInfo(name)
                info.type = tarfile.SYMTYPE
                info.linkname = "/etc/passwd"
                tf.addfile(info)
            elif kind == "hard":
                info = tarfile.TarInfo(name)
                info.type = tarfile.LNKTYPE
                info.linkname = "vault/a"
                tf.addfile(info)
    return p


@pytest.mark.parametrize(
    "members",
    [
        [("/abs/file", "file")],
        [("vault/../../escape", "file")],
        [("../escape", "file")],
        [("vault/link", "sym")],
        [("vault/a", "file"), ("vault/hard", "hard")],
    ],
)
def test_safe_extract_rejects(tmp_path, members):
    archive = _tar_with(tmp_path, members)
    dest = tmp_path / "out"
    with pytest.raises(UnsafeArchive):
        safe_extract(archive, dest)
    assert not dest.exists() or not any(dest.rglob("*"))  # 아무것도 쓰지 않았다


def test_safe_extract_ok(tmp_path):
    archive = _tar_with(tmp_path, [("vault", "dir"), ("vault/a.md", "file"), ("server/x.py", "file")])
    rep = safe_extract(archive, tmp_path / "out")
    assert rep.files == 2 and (tmp_path / "out" / "vault" / "a.md").read_bytes() == b"x"
    assert rep.top_level == ["server", "vault"]


def test_t10_shape_restore_from_ciphertext_and_rebuild(app, server_dir, tmp_path, clock):
    """빈 디렉터리에서 암호문만으로 복원 → rebuild → 검색. 가짜 age 로 절차만 검증한다 (실제는 T10)."""
    a = app.store.remember("복원될 기억", "이 기억은 복원 뒤에도 검색돼야 한다", tags=["주제/테스트"])
    up = FakeUploader()
    svc = _svc(app, up, server_dir=server_dir)
    r = svc.run_backup()
    app.close()

    cipher = tmp_path / "dl.age"
    up.download(r.name, cipher)
    ident = tmp_path / "identity.txt"
    ident.write_text("FAKE-IDENTITY\n")
    dest = tmp_path / "restored"
    # 해시 불일치 거부
    with pytest.raises(ProviderError):
        restore_snapshot(cipher, dest, ident, FakeEncryptor(), expected_sha256="0" * 64)
    # 잘못된 키 거부
    bad = tmp_path / "bad.txt"
    bad.write_text("WRONG\n")
    with pytest.raises(ProviderError):
        restore_snapshot(cipher, dest, bad, FakeEncryptor(), expected_sha256=r.sha256)
    rep = restore_snapshot(cipher, dest, ident, FakeEncryptor(), expected_sha256=r.sha256)
    assert rep.top_level == ["server", "vault"]
    assert (dest / "vault" / ".git").is_dir() and (dest / "server" / "RESTORE.md").exists()
    # 비어 있지 않은 대상 거부
    with pytest.raises(UnsafeArchive):
        restore_snapshot(cipher, dest, ident, FakeEncryptor())

    # 복원된 vault 로 rebuild 와 검색
    from memory_server.paths import MemoryPaths
    from memory_server.rebuild import rebuild

    new_paths = MemoryPaths(home=dest)
    import subprocess

    assert subprocess.run(["git", "-C", str(new_paths.vault), "log", "--oneline"], capture_output=True, text=True).stdout.count("\n") >= 2
    rebuild(new_paths, app.config, app.embedder, now=clock)
    app2 = build_app(new_paths, now=clock)
    try:
        res = app2.recaller.recall("복원될 기억")
        assert [x.id for x in res.results] == [a.id]
    finally:
        app2.close()
