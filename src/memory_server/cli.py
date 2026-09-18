"""`memory` CLI: init, doctor, rebuild, backup, backup-check, restore, restore-drill-done, stats, serve.

stdout 은 MCP 전용이라 CLI 안내도 전부 stderr 로 낸다.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .paths import MemoryHomeError, get_paths

def _say(msg: str) -> None:
    print(msg, file=sys.stderr)


def _cmd_init(_: argparse.Namespace) -> int:
    from .init_cmd import init_memory_home

    paths = get_paths()
    result = init_memory_home(paths)
    _say(f"MEMORY_HOME: {paths.home}")
    _say(f"디렉터리 생성: {len(result.created_dirs)}개")
    for p in result.created_files:
        _say(f"파일 생성: {p}")
    if result.git_initialized:
        _say(f"vault Git 저장소 초기화: {paths.vault}")
    _say(f"커밋: {result.initial_commit[:12]}" if result.initial_commit else "커밋할 변경 없음")
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    from .doctor import indexer_from_app, run_doctor
    from .logging_setup import setup_logging

    paths = get_paths()
    setup_logging(paths.server_log if paths.runtime.exists() else None)
    indexer = pending = None
    if not args.no_index and paths.config_toml.exists():
        try:
            from .app import build_app

            app = build_app(paths)
            indexer, pending = indexer_from_app(app, light=False)
        except Exception as e:  # noqa: BLE001
            _say(f"경고: 색인기를 준비하지 못했다 ({type(e).__name__}). 색인 없이 검사한다.")
    report = run_doctor(paths, mode="full", indexer=indexer, pending_paths=pending)
    _say(report.summary())
    for rel in report.restored:
        _say(f"복원: {rel}")
    for nid in report.recovered:
        _say(f"recovered: {nid}")
    for nid in report.external_edits:
        _say(f"외부 수정: {nid}")
    for fix in report.supersede_fixes:
        _say(f"정정 보정: {fix['old']} -> {fix['new']}")
    if report.indexed:
        _say(f"색인: {len(report.indexed)}개")
    for nid in report.index_failed:
        _say(f"색인 실패: {nid}")
    for b in report.broken_notes:
        _say(f"깨진 노트: {b['path']} ({b['reason']})")
    for w in report.warnings:
        _say(f"경고: {w}")
    if report.commit:
        _say(f"커밋: {report.commit[:12]}")
    if report.fatal:
        _say(report.fatal)
        return 1
    return 0


def _cmd_rebuild(_: argparse.Namespace) -> int:
    from .config import load_config
    from .index import make_embedder
    from .logging_setup import setup_logging
    from .rebuild import RebuildError, rebuild

    paths = get_paths()
    setup_logging(paths.server_log)
    config = load_config(paths.config_toml)
    try:
        report = rebuild(paths, config, make_embedder(config.embedding))
    except RebuildError as e:
        _say(f"rebuild 실패: {e}")
        return 1
    _say(f"rebuild 완료: 색인 {report.indexed}개, 건너뜀 {len(report.skipped)}개, used 이벤트 {report.used_events}개 → {report.db_path}")
    for s in report.skipped:
        _say(f"건너뜀(깨진 노트): {s}")
    return 0


def _backup_service(paths, config):
    import sys as _sys

    from .backup.providers import AgeCLI, EnvSecrets, MacKeychain, RcloneCLI
    from .backup.snapshot import BackupService
    from .stats import events_since as _events_since

    secrets = MacKeychain() if _sys.platform == "darwin" else EnvSecrets()
    uploader = RcloneCLI(config.backup.rclone_remote, secrets, config.backup.keychain_service_key_id, config.backup.keychain_service_key)
    server_dir = Path(__file__).resolve().parents[2]

    class _Counter:
        def __call__(self, since):
            from .app import build_app

            try:
                app = build_app(paths, embedder=None)
            except Exception:  # noqa: BLE001
                return 1
            try:
                return _events_since(app, since)
            finally:
                app.close()

    return BackupService(paths, config.backup, AgeCLI(), uploader, server_dir=server_dir, events_since=_Counter())


def _print_backup_result(label: str, r) -> None:
    if r.skipped_reason:
        _say(f"{label}: 건너뜀 ({r.skipped_reason})")
        return
    if r.name:
        _say(f"{label}: {r.name} captured_at={r.captured_at} size={r.size} verified={r.verified}")
    for n in r.retried:
        _say(f"{label}: 재업로드 verified {n}")
    for e in r.errors:
        _say(f"{label}: 오류 {e}")


def _cmd_backup(_: argparse.Namespace) -> int:
    from .config import load_config
    from .logging_setup import setup_logging

    paths = get_paths()
    setup_logging(paths.server_log)
    config = load_config(paths.config_toml)
    if not config.backup.rclone_remote or not config.backup.age_recipient:
        _say("백업이 설정되지 않았다. config.toml 의 [backup] rclone_remote 와 age_recipient 를 채운다 (SETUP.md).")
        return 1
    r = _backup_service(paths, config).run_backup(force=True)
    _print_backup_result("backup", r)
    return 0 if r.verified else 1


def _cmd_backup_check(_: argparse.Namespace) -> int:
    from .config import load_config
    from .logging_setup import setup_logging

    paths = get_paths()
    setup_logging(paths.server_log)
    config = load_config(paths.config_toml)
    if not config.backup.rclone_remote or not config.backup.age_recipient:
        _say("백업이 설정되지 않았다 (SETUP.md).")
        return 1
    retried, fresh = _backup_service(paths, config).check()
    _print_backup_result("retry", retried)
    _print_backup_result("check", fresh)
    return 0 if not (retried.errors or fresh.errors) else 1


def _cmd_restore(args: argparse.Namespace) -> int:
    from .backup.providers import AgeCLI, ProviderError
    from .backup.restore import UnsafeArchive, restore_snapshot

    try:
        report = restore_snapshot(Path(args.ciphertext), Path(args.into), Path(args.identity), AgeCLI(), expected_sha256=args.sha256)
    except (UnsafeArchive, ProviderError) as e:
        _say(f"복원 실패: {e}")
        return 1
    _say(f"복원 완료: 파일 {report.files}개, 디렉터리 {report.dirs}개 → {args.into} ({', '.join(report.top_level)})")
    _say("다음: MEMORY_HOME 을 이 디렉터리로 두고 server/ 에서 `uv sync`, 그다음 `memory rebuild`. 시험 질의 뒤 `memory restore-drill-done`.")
    return 0


def _cmd_restore_drill_done(_: argparse.Namespace) -> int:
    from .backup.snapshot import BackupService
    from .backup.providers import FakeEncryptor, FakeUploader
    from .config import load_config

    paths = get_paths()
    config = load_config(paths.config_toml)
    svc = BackupService(paths, config.backup, FakeEncryptor(), FakeUploader())  # 기록만 한다
    _say(f"복원 리허설 날짜 기록: {svc.record_restore_drill()}")
    return 0


def _cmd_stats(_: argparse.Namespace) -> int:
    from .app import build_app
    from .stats import collect_stats

    paths = get_paths()
    app = build_app(paths)
    try:
        _say(json.dumps(collect_stats(app), ensure_ascii=False, indent=2))
    finally:
        app.close()
    return 0


def _cmd_setup(args: argparse.Namespace) -> int:
    from .setup.wizard import ALL_STEPS, Wizard

    memory_home = Path(args.memory_home or os.environ.get("MEMORY_HOME") or (Path.home() / "memory-test")).expanduser()
    server_dir = Path(args.server_dir).resolve() if args.server_dir else Path(__file__).resolve().parents[2]
    w = Wizard(memory_home=memory_home, server_dir=server_dir, allow_non_mac=args.allow_non_mac)
    if args.uv:
        w.uv = Path(args.uv)
    steps = args.steps or ALL_STEPS
    return w.run(steps)


def _cmd_serve(_: argparse.Namespace) -> int:
    from .server import run

    run()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="memory", description="개인 AI 기억 시스템 CLI")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="MEMORY_HOME 아래 폴더와 vault Git 저장소를 만든다").set_defaults(func=_cmd_init)
    p = sub.add_parser("doctor", help="vault 전체 검사·복구·색인")
    p.add_argument("--no-index", action="store_true", help="색인 없이 검사만 한다 (모델을 올리지 않는다)")
    p.set_defaults(func=_cmd_doctor)
    sub.add_parser("rebuild", help="노트와 used 이벤트로 DB 를 다시 만든다").set_defaults(func=_cmd_rebuild)
    sub.add_parser("backup", help="스냅샷을 만들어 올린다").set_defaults(func=_cmd_backup)
    sub.add_parser("backup-check", help="대기 재시도 후 주기 규칙에 따라 백업한다 (launchd 용)").set_defaults(func=_cmd_backup_check)
    p = sub.add_parser("restore", help="암호문을 빈 디렉터리에 안전하게 푼다")
    p.add_argument("ciphertext")
    p.add_argument("--into", required=True)
    p.add_argument("--identity", required=True, help="age 개인키 파일 (복원 뒤 지운다)")
    p.add_argument("--sha256", default=None)
    p.set_defaults(func=_cmd_restore)
    sub.add_parser("restore-drill-done", help="복원 리허설 날짜를 기록한다").set_defaults(func=_cmd_restore_drill_done)
    sub.add_parser("stats", help="stats 를 출력한다").set_defaults(func=_cmd_stats)
    p = sub.add_parser("setup", help="맥북 설치 마법사: check init desktop age b2 backup launchd t09 t08 drill [golive]")
    p.add_argument("steps", nargs="*", help="실행할 단계. 비우면 golive 를 뺀 전부")
    p.add_argument("--memory-home", default=None, help="기본: $MEMORY_HOME, 없으면 $HOME/memory-test")
    p.add_argument("--server-dir", default=None)
    p.add_argument("--uv", default=None)
    p.add_argument("--allow-non-mac", action="store_true", help=argparse.SUPPRESS)
    p.set_defaults(func=_cmd_setup)
    sub.add_parser("serve", help="MCP stdio 서버를 실행한다").set_defaults(func=_cmd_serve)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except MemoryHomeError as e:
        _say(f"오류: {e}")
        return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
