"""주기와 경고 (SPEC 8.2).

- 마지막 verified 스냅샷의 captured_at 이 min_interval_hours 보다 오래됐고 그 뒤에 새 이벤트가 있으면 백업한다.
- 최신성은 업로드 시각이 아니라 captured_at 으로 계산한다. 48시간 넘게 verified 가 없으면 경고.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from ..config import BackupConfig
from .state import BackupState


def hours_since_verified(state: BackupState | None, now: datetime) -> float | None:
    if state is None or state.last_verified is None:
        return None
    captured = datetime.fromisoformat(state.last_verified.captured_at)
    return (now - captured).total_seconds() / 3600.0


def should_backup(state: BackupState | None, cfg: BackupConfig, now: datetime, events_since_captured: int) -> tuple[bool, str]:
    """(백업할지, 이유)."""
    h = hours_since_verified(state, now)
    if h is None:
        return True, "verified 스냅샷이 없다"
    if h < cfg.min_interval_hours:
        return False, f"마지막 스냅샷이 {h:.1f}시간 전이라 아직 이르다 (기준 {cfg.min_interval_hours}시간)"
    if events_since_captured <= 0:
        return False, "마지막 스냅샷 뒤로 새 이벤트가 없다"
    return True, f"마지막 스냅샷이 {h:.1f}시간 전이고 새 이벤트가 {events_since_captured}개 있다"


def staleness_warning(state: BackupState | None, cfg: BackupConfig, now: datetime) -> str | None:
    if state is None:
        return "BACKUP_NOT_CONFIGURED: 백업이 한 번도 실행되지 않았다. SETUP.md 의 백업 준비 절차를 따른다."
    h = hours_since_verified(state, now)
    if h is None:
        return "BACKUP_STALE: verified 스냅샷이 아직 없다."
    if h > cfg.stale_warning_hours:
        return f"BACKUP_STALE: 마지막 verified 스냅샷이 {h / 24:.1f}일 전이다 (captured_at 기준, 기준 {cfg.stale_warning_hours}시간)."
    return None


def usage_warning(state: BackupState | None, cfg: BackupConfig) -> str | None:
    if state is None or state.remote_usage is None:
        return None
    gb = state.remote_usage.bytes / (1024 ** 3)
    if gb > cfg.usage_warning_gb:
        return f"BACKUP_USAGE: 원격 사용량이 {gb:.1f}GB 로 경고 기준 {cfg.usage_warning_gb}GB 를 넘었다. 백업은 계속한다."
    return None


def next_check_delay() -> timedelta:
    return timedelta(hours=1)
