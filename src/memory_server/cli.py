"""`memory` CLI. 지금은 `init`과 `serve`만 있다."""

from __future__ import annotations

import argparse
import sys

from .paths import MemoryHomeError, get_paths


def _cmd_init(_: argparse.Namespace) -> int:
    from .init_cmd import init_memory_home

    paths = get_paths()
    result = init_memory_home(paths)
    out = sys.stderr  # stdout은 MCP 전용이므로 CLI 안내도 stderr로 낸다.
    print(f"MEMORY_HOME: {paths.home}", file=out)
    print(f"디렉터리 생성: {len(result.created_dirs)}개", file=out)
    for p in result.created_files:
        print(f"파일 생성: {p}", file=out)
    if result.git_initialized:
        print(f"vault Git 저장소 초기화: {paths.vault}", file=out)
    if result.initial_commit:
        print(f"커밋: {result.initial_commit[:12]}", file=out)
    else:
        print("커밋할 변경 없음", file=out)
    return 0


def _cmd_doctor(_: argparse.Namespace) -> int:
    from .doctor import run_doctor
    from .logging_setup import setup_logging

    paths = get_paths()
    setup_logging(paths.server_log if paths.runtime.exists() else None)
    report = run_doctor(paths, mode="full")
    out = sys.stderr
    print(report.summary(), file=out)
    for rel in report.restored:
        print(f"복원: {rel}", file=out)
    for nid in report.recovered:
        print(f"recovered: {nid}", file=out)
    for nid in report.external_edits:
        print(f"외부 수정: {nid}", file=out)
    for fix in report.supersede_fixes:
        print(f"정정 보정: {fix['old']} -> {fix['new']}", file=out)
    for b in report.broken_notes:
        print(f"깨진 노트: {b['path']} ({b['reason']})", file=out)
    for w in report.warnings:
        print(f"경고: {w}", file=out)
    if report.commit:
        print(f"커밋: {report.commit[:12]}", file=out)
    if report.fatal:
        print(report.fatal, file=out)
        return 1
    return 0


def _cmd_serve(_: argparse.Namespace) -> int:
    from .server import run

    run()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="memory", description="개인 AI 기억 시스템 CLI")
    sub = parser.add_subparsers(dest="command", required=True)
    p_init = sub.add_parser("init", help="MEMORY_HOME 아래 폴더와 vault Git 저장소를 만든다")
    p_init.set_defaults(func=_cmd_init)
    p_doctor = sub.add_parser("doctor", help="vault 전체 검사와 복구를 실행한다")
    p_doctor.set_defaults(func=_cmd_doctor)
    p_serve = sub.add_parser("serve", help="MCP stdio 서버를 실행한다")
    p_serve.set_defaults(func=_cmd_serve)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except MemoryHomeError as e:
        print(f"오류: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
