"""스냅샷 만들기와 점검 (SPEC 8.2).

1. 쓰기 잠금 안에서 vault/(.git 포함) 와 server/(가상환경 제외) 를 staging 에 복사하고 잠금을 푼다. 이때가 captured_at.
2. snapshots/<UTC시각>-<무작위>.tar.gz 로 묶고 age 공개키로 암호화한다. DB, runtime, 모델 캐시, 자격증명은 넣지 않는다.
3. 올린 뒤 원격의 크기와 해시를 로컬 암호문과 대조한다. 일치하면 verified.
4. 실패하면 암호문을 runtime/backup/pending/ 에 두고 다음 점검 때 같은 파일, 같은 이름으로 다시 올린다.
5. staging 을 지운다.
"""

from __future__ import annotations

import secrets as _secrets
import shutil
import tarfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from ..config import BackupConfig
from ..lock import write_lock
from ..logging_setup import get_logger
from ..paths import MemoryPaths
from ..timeutil import now_local
from .providers import Encryptor, ProviderError, Uploader, sha256_file
from .schedule import should_backup
from .state import BackupState, RemoteUsage, Snapshot, load_state, save_state

SERVER_EXCLUDES = {".venv", "__pycache__", ".pytest_cache", "node_modules", ".mypy_cache", ".ruff_cache"}
SNAPSHOT_PREFIX = "snapshots"


@dataclass
class BackupResult:
    name: str | None = None
    captured_at: str | None = None
    verified: bool = False
    uploaded: bool = False
    size: int = 0
    sha256: str | None = None
    skipped_reason: str | None = None
    retried: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class BackupService:
    def __init__(
        self,
        paths: MemoryPaths,
        cfg: BackupConfig,
        encryptor: Encryptor,
        uploader: Uploader,
        *,
        server_dir: Path | None = None,
        now: Callable[[], datetime] = now_local,
        events_since: Callable[[str | None], int] | None = None,
    ) -> None:
        self.paths = paths
        self.cfg = cfg
        self.encryptor = encryptor
        self.uploader = uploader
        self.server_dir = server_dir
        self.now = now
        self.events_since = events_since or (lambda _: 1)
        self.log = get_logger()

    # ------------------------------------------------------------------ 경로

    @property
    def pending_dir(self) -> Path:
        return self.paths.backup / "pending"

    def _staging_dir(self) -> Path:
        return self.paths.backup / f"staging-{_secrets.token_hex(4)}"

    # ------------------------------------------------------------------ 단계

    def _copy_sources(self, staging: Path) -> None:
        shutil.copytree(self.paths.vault, staging / "vault", symlinks=False)  # .git 포함
        if self.server_dir is not None and self.server_dir.exists():
            shutil.copytree(
                self.server_dir,
                staging / "server",
                symlinks=False,
                ignore=lambda d, names: [n for n in names if n in SERVER_EXCLUDES],
            )

    def _tar(self, staging: Path, dst: Path) -> None:
        with tarfile.open(dst, "w:gz") as tf:
            for top in ("vault", "server"):
                src = staging / top
                if src.exists():
                    tf.add(src, arcname=top, recursive=True)

    def _verify(self, name: str, local: Path) -> bool:
        obj = self.uploader.stat(name)
        if obj is None:
            return False
        if obj.size != local.stat().st_size:
            return False
        if obj.sha256:
            return obj.sha256 == sha256_file(local)
        if obj.sha1:
            import hashlib

            h = hashlib.sha1()
            with open(local, "rb") as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b""):
                    h.update(chunk)
            return obj.sha1 == h.hexdigest()
        return False

    def _upload_and_verify(self, snap: Snapshot, local: Path, state: BackupState, result: BackupResult) -> bool:
        try:
            self.uploader.upload(local, snap.name)
            result.uploaded = True
        except ProviderError as e:
            result.errors.append(f"업로드 실패: {e}")
            self.log.warning("backup upload failed")
            return False
        if not self._verify(snap.name, local):
            result.errors.append("원격 크기·해시가 로컬 암호문과 다르다")
            self.log.warning("backup verify failed")
            return False
        snap.uploaded_at = self.now().isoformat(timespec="seconds")
        snap.verified = True
        # 최신성은 captured_at 기준. 옛 스냅샷의 재업로드가 최신 것을 덮지 않는다.
        if state.last_verified is None or snap.captured_at > state.last_verified.captured_at:
            state.last_verified = snap
        state.history = (state.history + [snap])[-60:]
        state.pending = [p for p in state.pending if p.name != snap.name]
        try:
            local.unlink()
        except FileNotFoundError:
            pass
        return True

    # ------------------------------------------------------------------ 공개

    def run_backup(self, *, force: bool = True) -> BackupResult:
        """스냅샷 하나를 만들어 올린다. force=False 면 주기 규칙을 본다."""
        state = load_state(self.paths) or BackupState()
        result = BackupResult()
        if not self.cfg.age_recipient:
            result.errors.append("age_recipient 가 설정되지 않았다. SETUP.md 의 age 키 절차를 따른다.")
            return result
        if not force:
            captured = state.last_verified.captured_at if state.last_verified else None
            ok, why = should_backup(state, self.cfg, self.now(), self.events_since(captured))
            if not ok:
                result.skipped_reason = why
                return result

        staging = self._staging_dir()
        self.paths.backup.mkdir(parents=True, exist_ok=True)
        try:
            with write_lock(self.paths.lock_file, timeout=None):
                self._copy_sources(staging)
                captured_at = self.now()
            utc = captured_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            name = f"{SNAPSHOT_PREFIX}/{utc}-{_secrets.token_hex(3)}.tar.gz.age"
            self.pending_dir.mkdir(parents=True, exist_ok=True)
            plain = staging / "snapshot.tar.gz"
            cipher = self.pending_dir / Path(name).name
            self._tar(staging, plain)
            self.encryptor.encrypt(plain, cipher, self.cfg.age_recipient)
            plain.unlink()
        except ProviderError as e:
            result.errors.append(str(e))
            shutil.rmtree(staging, ignore_errors=True)
            state.last_error = str(e)
            save_state(self.paths, state)
            return result
        finally:
            shutil.rmtree(staging, ignore_errors=True)

        snap = Snapshot(name=name, captured_at=captured_at.isoformat(timespec="seconds"), size=cipher.stat().st_size, sha256=sha256_file(cipher))
        result.name, result.captured_at, result.size, result.sha256 = name, snap.captured_at, snap.size, snap.sha256
        if self._upload_and_verify(snap, cipher, state, result):
            result.verified = True
        else:
            state.pending.append(snap)  # 같은 파일, 같은 이름으로 다음 점검 때 재시도
        self._refresh_usage(state)
        state.last_check_at = self.now().isoformat(timespec="seconds")
        state.last_error = result.errors[-1] if result.errors else None
        save_state(self.paths, state)
        self.log.info("backup name=%s verified=%s", name, result.verified)
        return result

    def retry_pending(self) -> BackupResult:
        state = load_state(self.paths) or BackupState()
        result = BackupResult()
        for snap in list(state.pending):
            local = self.pending_dir / Path(snap.name).name
            if not local.exists():
                state.pending = [p for p in state.pending if p.name != snap.name]
                result.errors.append(f"대기 암호문이 사라졌다: {snap.name}")
                continue
            if sha256_file(local) != snap.sha256:
                result.errors.append(f"대기 암호문의 해시가 기록과 다르다: {snap.name}")
                continue
            if self._upload_and_verify(snap, local, state, result):
                result.retried.append(snap.name)
        state.last_check_at = self.now().isoformat(timespec="seconds")
        save_state(self.paths, state)
        return result

    def check(self) -> tuple[BackupResult, BackupResult]:
        """launchd 가 1시간마다 부르는 점검: 대기 재시도 → 주기 규칙에 따라 새 백업."""
        retried = self.retry_pending()
        fresh = self.run_backup(force=False)
        return retried, fresh

    def _refresh_usage(self, state: BackupState) -> None:
        try:
            b = self.uploader.usage_bytes()
        except ProviderError:
            b = None
        if b is not None:
            state.remote_usage = RemoteUsage(bytes=b, checked_at=self.now().isoformat(timespec="seconds"))

    def record_restore_drill(self) -> str:
        state = load_state(self.paths) or BackupState()
        state.last_restore_drill = self.now().date().isoformat()
        save_state(self.paths, state)
        return state.last_restore_drill
