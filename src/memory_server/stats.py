"""stats (SPEC 8.4)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .app import App
from .backup.state import BackupState, load_state
from .backup.schedule import staleness_warning, usage_warning
from .core import CoreService
from .doctor import load_last_report
from .events import iter_events


def events_since(app: App, since_iso: str | None) -> int:
    """마지막 verified 스냅샷 captured_at 이후 쌓인 이벤트 수 (아직 원격 보호 전인 양)."""
    if not app.paths.events_dir.exists():
        return 0
    since = datetime.fromisoformat(since_iso) if since_iso else None
    n = 0
    for f in sorted(app.paths.events_dir.glob("events-*.jsonl")):
        for ev in iter_events(f):
            if since is None:
                n += 1
                continue
            try:
                if datetime.fromisoformat(ev.ts) > since:
                    n += 1
            except ValueError:
                continue
    return n


def collect_warnings(app: App, state: BackupState | None = None) -> list[str]:
    """get_core 와 stats 앞에 붙는 경고: 백업 최신성, 색인 대기, 깨진 노트, DB 오류."""
    state = state if state is not None else load_state(app.paths)
    warnings: list[str] = []
    w = staleness_warning(state, app.config.backup, app.now())
    if w:
        warnings.append(w)
    w = usage_warning(state, app.config.backup)
    if w:
        warnings.append(w)
    if app.db_error:
        warnings.append(f"INDEX_UNAVAILABLE: DB 를 열지 못했다 ({app.db_error}).")
    else:
        pending = app.pending_index_count()
        if pending > 0:
            warnings.append(f"INDEX_PENDING: 색인되지 않은 노트가 {pending}개 있다.")
    last = load_last_report(app.paths)
    if last and last.get("broken_notes"):
        warnings.append(f"BROKEN_NOTES: frontmatter 가 깨진 노트가 {len(last['broken_notes'])}개 있어 회상에서 빠져 있다. 손으로 고쳐야 한다.")
    if last and last.get("fatal"):
        warnings.append(str(last["fatal"]))
    return warnings


def collect_stats(app: App) -> dict[str, Any]:
    state = load_state(app.paths)
    core = CoreService(app.paths, now=app.now).get_core()
    counts = app.db.counts_by_status() if app.db is not None else {"active": None, "archived": None, "superseded": None}
    if app.db is None:
        # DB 가 없으면 파일에서 센다
        counts = {"active": 0, "archived": 0, "superseded": 0}
        for _, fm in app.store.iter_frontmatters():
            counts[fm.status] = counts.get(fm.status, 0) + 1
    last_doctor = load_last_report(app.paths)
    last_verified = state.last_verified if state else None
    remote = state.remote_usage if state else None
    return {
        "warnings": collect_warnings(app, state),
        "notes": counts,
        "core": {"valid": len(core["entries"]), "review": len(core["review"])},
        "index": {
            "pending": app.pending_index_count(),
            "vectors": app.db.count_vectors() if app.db is not None else None,
            "model": app.config.embedding.model_id,
            "dimension": app.config.embedding.dimension,
            "model_loaded": app.embedder.loaded(),
            "db_error": app.db_error,
        },
        "relevance_calibrated": app.config.recall.relevance_calibrated,
        "min_similarity": app.config.recall.min_similarity,
        "doctor": {"ran_at": last_doctor.get("ran_at"), "mode": last_doctor.get("mode"), "broken_notes": len(last_doctor.get("broken_notes", [])), "warnings": len(last_doctor.get("warnings", [])), "fatal": last_doctor.get("fatal")} if last_doctor else "미실행",
        "backup": {
            "configured": bool(app.config.backup.rclone_remote and app.config.backup.age_recipient),
            "last_verified_captured_at": last_verified.captured_at if last_verified else "미설정",
            "last_verified_name": last_verified.name if last_verified else "미설정",
            "events_since_last_verified": events_since(app, last_verified.captured_at if last_verified else None),
            "pending_uploads": len(state.pending) if state else 0,
            "last_restore_drill": (state.last_restore_drill if state and state.last_restore_drill else "미설정"),
            "remote_usage_bytes": remote.bytes if remote else "미설정",
            "remote_usage_checked_at": remote.checked_at if remote else "미설정",
        },
    }
