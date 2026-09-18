"""doctor (SPEC 5 표).

1단계에는 DB 가 없으므로 "알려진 상태" 의 기준은 vault 의 Git HEAD 다.
`git status` 로 삭제·수정·미추적 파일을 찾고 표대로 처리한다. 색인은 2단계에서 indexer 훅으로 붙는다.

| 발견한 것 | 처리 |
| DB에 없는 노트 (미추적 노트) | (2단계: 색인) created 이벤트가 없으면 recovered 이벤트를 남기고 커밋한다. |
| 이벤트 로그 마지막 줄이 잘림 | 조각을 runtime/recovery/ 로 옮기고 온전한 줄까지만 쓴다. |
| 커밋되지 않은 서버 변경 | 커밋한다. |
| 노트 본문이 외부에서 바뀜 | (2단계: 재색인) external_edit 이벤트를 남기고 커밋한다. |
| 노트나 _system/ 파일이 외부에서 삭제됨 | Git 에서 복원하고 restored 이벤트를 남긴다. 삭제를 커밋하지 않는다. |
| frontmatter 가 깨짐 | 보고만 한다. 자동으로 고치지 않는다. |
| .git 이 없음 | 쓰기를 멈추고 백업 복원을 안내한다. |
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from . import gitutil, mutations
from .events import append_event, iter_events
from .lock import write_lock
from .logging_setup import get_logger
from .naming import ID_RE
from .notes import AMEND_HEADING, NoteFormatError, parse_note, split_frontmatter
from .paths import MemoryPaths
from .timeutil import now_local

NO_GIT_MESSAGE = (
    "vault/.git 이 없다. 쓰기를 멈춘다. 로컬 이력이 사라진 상태이므로 RESTORE.md 의 백업 복원 절차를 따른다. "
    "vault 를 새로 init 하지 말 것."
)

Indexer = Callable[[Path], bool]  # 노트 하나를 색인한다. 성공하면 True.


@dataclass
class DoctorReport:
    ran_at: str = ""
    mode: str = "full"
    fatal: str | None = None
    restored: list[str] = field(default_factory=list)  # 복원한 vault 상대 경로
    recovered: list[str] = field(default_factory=list)  # recovered 이벤트를 남긴 노트 ID
    external_edits: list[str] = field(default_factory=list)  # external_edit 이벤트를 남긴 노트 ID
    committed_paths: list[str] = field(default_factory=list)
    commit: str | None = None
    truncated_logs: list[str] = field(default_factory=list)  # 조각을 옮긴 이벤트 파일
    supersede_fixes: list[dict[str, str]] = field(default_factory=list)
    broken_notes: list[dict[str, str]] = field(default_factory=list)  # {"path", "reason"}
    indexed: list[str] = field(default_factory=list)
    index_failed: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.fatal is None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def summary(self) -> str:
        if self.fatal:
            return f"doctor 실패: {self.fatal}"
        parts = [
            f"복원 {len(self.restored)}",
            f"recovered {len(self.recovered)}",
            f"외부수정 {len(self.external_edits)}",
            f"커밋 {len(self.committed_paths)}",
            f"로그조각 {len(self.truncated_logs)}",
            f"정정보정 {len(self.supersede_fixes)}",
            f"깨진노트 {len(self.broken_notes)}",
            f"경고 {len(self.warnings)}",
        ]
        return "doctor 완료: " + ", ".join(parts)


def _rel(paths: MemoryPaths, p: Path) -> str:
    return str(p.relative_to(paths.vault))


def _note_id_from_path(rel: str) -> str | None:
    name = Path(rel).name
    if not name.endswith(".md"):
        return None
    stem = name[:-3]
    candidate = stem.rsplit(" ", 1)[-1]
    return candidate if ID_RE.match(candidate) else None


def _is_note(rel: str) -> bool:
    return rel.startswith("memories/") and rel.endswith(".md") and not Path(rel).name.startswith(".")


def _is_protected(rel: str) -> bool:
    """삭제되면 복원해야 하는 파일: 노트, _system/ 아래 전부, .gitignore, core.md."""
    return _is_note(rel) or rel.startswith("_system/") or rel in (".gitignore", "core.md")


def _git_status(paths: MemoryPaths) -> dict[str, list[str]]:
    """{'deleted': [...], 'modified': [...], 'untracked': [...]} (vault 상대 경로)."""
    proc = gitutil.run_git(paths.vault, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    out: dict[str, list[str]] = {"deleted": [], "modified": [], "untracked": []}
    entries = proc.stdout.split("\0")
    i = 0
    while i < len(entries):
        entry = entries[i]
        i += 1
        if not entry:
            continue
        code, rel = entry[:2], entry[3:]
        if "R" in code or "C" in code:  # 이름 바꿈: 다음 항목이 원래 경로
            i += 1
        x, y = code[0], code[1]
        if "D" in (x, y):
            out["deleted"].append(rel)
        elif code == "??":
            out["untracked"].append(rel)
        elif "M" in (x, y) or "A" in (x, y) or "T" in (x, y):
            out["modified"].append(rel)
    return out


def _known_event_ids(paths: MemoryPaths, types: set[str]) -> set[str]:
    ids: set[str] = set()
    if paths.events_dir.exists():
        for f in sorted(paths.events_dir.glob("events-*.jsonl")):
            for ev in iter_events(f):
                if ev.type in types:
                    ids.update(ev.ids)
    return ids


def _is_allowed_server_change(old: str, new: str) -> bool:
    """HEAD 대비 변경이 SPEC 3.3 의 허용된 세 가지(덧붙임 추가, status, superseded_by) 안인지."""
    if new == old:
        return True
    # 1) 덧붙임만 추가됨
    if new.startswith(old):
        suffix = new[len(old):]
        lines = [ln for ln in suffix.split("\n") if ln.strip()]
        try:
            _, rest_old = split_frontmatter(old)
        except NoteFormatError:
            return False
        had_heading = any(ln.rstrip() == AMEND_HEADING for ln in rest_old.split("\n"))
        if not had_heading:
            if not lines or lines[0].rstrip() != AMEND_HEADING:
                return False
            lines = lines[1:]
        return bool(lines) and all(mutations_amend_line(ln) for ln in lines)
    # 2) frontmatter 의 status / superseded_by 줄만 다름
    try:
        fm_old, body_old = split_frontmatter(old)
        fm_new, body_new = split_frontmatter(new)
    except NoteFormatError:
        return False
    if body_old != body_new:
        return False
    a_lines, b_lines = fm_old.split("\n"), fm_new.split("\n")
    if len(a_lines) != len(b_lines):
        return False
    for a, b in zip(a_lines, b_lines, strict=True):
        if a == b:
            continue
        if a.startswith("status: ") and b.startswith("status: "):
            continue
        if a.startswith("superseded_by: ") and b.startswith("superseded_by: "):
            continue
        return False
    return True


def mutations_amend_line(line: str) -> bool:
    import re

    return bool(re.match(r"^- \d{4}-\d{2}-\d{2}: .+$", line.rstrip()))


def _repair_event_logs(paths: MemoryPaths, report: DoctorReport, now: datetime) -> list[Path]:
    """마지막 줄이 잘린 이벤트 파일을 고친다. 고친 파일 경로를 돌려준다."""
    changed: list[Path] = []
    if not paths.events_dir.exists():
        return changed
    for f in sorted(paths.events_dir.glob("events-*.jsonl")):
        raw = f.read_bytes()
        if not raw:
            continue
        good_end = raw.rfind(b"\n") + 1  # 마지막 개행까지가 온전한 후보
        tail = raw[good_end:]
        # 온전해 보이는 마지막 줄도 JSON 인지 확인한다
        while good_end > 0:
            prev = raw.rfind(b"\n", 0, good_end - 1) + 1
            line = raw[prev:good_end]
            try:
                json.loads(line.decode("utf-8"))
                break
            except (UnicodeDecodeError, json.JSONDecodeError):
                tail = line + tail
                good_end = prev
        if not tail:
            continue
        paths.recovery.mkdir(parents=True, exist_ok=True)
        frag = paths.recovery / f"{f.name}.{now.strftime('%Y%m%dT%H%M%S')}.fragment"
        frag.write_bytes(tail)
        fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=f.parent)
        with os.fdopen(fd, "wb") as out:
            out.write(raw[:good_end])
            out.flush()
        os.rename(tmp, f)
        report.truncated_logs.append(_rel(paths, f))
        report.warnings.append(f"이벤트 로그 {f.name} 의 잘린 조각을 {frag.name} 로 옮겼다.")
        changed.append(f)
    return changed


def _reconstruct_input_hash(text: str) -> str | None:
    """노트에서 remember 입력 해시를 다시 만든다. recovered 노트의 10분 중복 방지에 쓴다."""
    from .store import RememberInput, _collapse_ws  # 순환 import 방지

    try:
        note = parse_note(text)
    except NoteFormatError:
        return None
    fm = note.frontmatter
    from .notes import normalize_text

    inp = RememberInput(
        title=_collapse_ws(normalize_text(note.title)),
        body=normalize_text(note.body).strip(),
        tags=list(fm.tags),
        type=fm.type,
        source=fm.source,
        fact_key=fm.fact_key,
        links=list(fm.links),
        supersedes=list(fm.supersedes),
        reason=None,
    )
    return inp.input_hash()


def run_doctor(
    paths: MemoryPaths,
    *,
    mode: str = "full",
    indexer: Indexer | None = None,
    pending_paths: Callable[[], list[Path]] | None = None,
    now: Callable[[], datetime] = now_local,
) -> DoctorReport:
    log = get_logger()
    report = DoctorReport(ran_at=now().isoformat(timespec="seconds"), mode=mode)

    if not paths.vault.exists() or not gitutil.is_repo(paths.vault):
        report.fatal = "VAULT_NOT_GIT: " + NO_GIT_MESSAGE
        log.error("doctor fatal=VAULT_NOT_GIT")
        _persist(paths, report)
        return report

    with write_lock(paths.lock_file):
        ts = now()
        to_commit: list[Path] = []

        # 1) 이벤트 로그 꼬리 복구
        to_commit += _repair_event_logs(paths, report, ts)

        status = _git_status(paths)

        # 2) 외부 삭제 → Git 에서 복원. 삭제는 커밋하지 않는다.
        for rel in status["deleted"]:
            if not _is_protected(rel):
                report.warnings.append(f"삭제된 파일을 그대로 둔다 (보호 대상 아님): {rel}")
                continue
            gitutil.run_git(paths.vault, "checkout", "--", rel)
            nid = _note_id_from_path(rel) if _is_note(rel) else None
            to_commit.append(append_event(paths, "restored", [nid] if nid else [], {"path": rel}, ts=ts))
            report.restored.append(rel)

        known_created = _known_event_ids(paths, {"created", "recovered"})

        # 3) 미추적 노트 → created/recovered 이벤트 확인 뒤 커밋 (2단계: 색인)
        for rel in status["untracked"]:
            path = paths.vault / rel
            if _is_note(rel):
                nid = _note_id_from_path(rel)
                text = path.read_text(encoding="utf-8", errors="replace")
                broken = _check_note(path, rel, nid, report)
                if nid and nid not in known_created:
                    data: dict[str, Any] = {"path": rel}
                    h = None if broken else _reconstruct_input_hash(text)
                    if h:
                        data["input_hash"] = h
                    to_commit.append(append_event(paths, "recovered", [nid], data, ts=ts))
                    report.recovered.append(nid)
                    known_created.add(nid)
                if not broken:
                    _index(indexer, path, nid or rel, report)
                to_commit.append(path)
            elif rel.startswith("_system/") or rel == "core.md":
                to_commit.append(path)
            elif Path(rel).name.startswith(".tmp-"):
                report.warnings.append(f"임시 파일이 남아 있다 (건드리지 않음): {rel}")
            else:
                report.warnings.append(f"미추적 파일을 그대로 둔다: {rel}")

        # 4) 수정된 파일
        for rel in status["modified"]:
            path = paths.vault / rel
            if _is_note(rel):
                nid = _note_id_from_path(rel)
                new = path.read_text(encoding="utf-8", errors="replace")
                old = gitutil.run_git(paths.vault, "show", f"HEAD:{rel}").stdout
                broken = _check_note(path, rel, nid, report)
                if _is_allowed_server_change(old, new):
                    pass  # 커밋되지 않은 서버 변경
                else:
                    to_commit.append(append_event(paths, "external_edit", [nid] if nid else [], {"path": rel}, ts=ts))
                    if nid:
                        report.external_edits.append(nid)
                if not broken:
                    _index(indexer, path, nid or rel, report)
                to_commit.append(path)
            else:
                to_commit.append(path)  # _system/config.toml, 이벤트 로그 등: 커밋한다

        # 5) 정정 일관성: supersedes 가 가리키는 노트가 아직 active/archived 면 마저 superseded 로 바꾼다.
        to_commit += _fix_supersedes(paths, report)

        # 6) 깨진 노트 목록 (추적 중인 것도 포함)
        _scan_broken(paths, report)

        # 6b) DB 에 없거나 해시가 다른 노트 색인 (SPEC 5 표 "DB에 없는 노트", "다시 색인")
        if pending_paths is not None:
            for path in pending_paths():
                rel = _rel(paths, path)
                if rel in {b["path"] for b in report.broken_notes}:
                    continue
                label = _note_id_from_path(rel) or rel
                if label in report.indexed or label in report.index_failed:
                    continue
                _index(indexer, path, label, report)

        # 7) 한 번에 커밋
        uniq: list[Path] = []
        seen: set[Path] = set()
        for p in to_commit:
            if p not in seen and p.exists():
                seen.add(p)
                uniq.append(p)
        if uniq:
            try:
                sha = gitutil.commit_paths(paths.vault, uniq, f"doctor: {report.summary()}")
                if sha:
                    report.commit = sha
                    report.committed_paths = [_rel(paths, p) for p in uniq]
            except gitutil.GitError as e:
                report.warnings.append(f"doctor 커밋 실패: {e}")

    log.info("doctor mode=%s restored=%d recovered=%d external=%d committed=%d broken=%d",
             mode, len(report.restored), len(report.recovered), len(report.external_edits),
             len(report.committed_paths), len(report.broken_notes))
    _persist(paths, report)
    return report


def _check_note(path: Path, rel: str, nid: str | None, report: DoctorReport) -> bool:
    """깨졌으면 report 에 넣고 True."""
    try:
        note = parse_note(path.read_text(encoding="utf-8"))
    except (NoteFormatError, UnicodeDecodeError) as e:
        report.broken_notes.append({"path": rel, "reason": str(e)})
        return True
    if nid and note.id != nid:
        report.broken_notes.append({"path": rel, "reason": f"파일명 ID {nid} 와 frontmatter id {note.id} 가 다르다"})
        return True
    return False


def _scan_broken(paths: MemoryPaths, report: DoctorReport) -> None:
    listed = {b["path"] for b in report.broken_notes}
    if not paths.memories.exists():
        return
    for path in sorted(paths.memories.rglob("*.md")):
        if path.name.startswith("."):
            continue
        rel = _rel(paths, path)
        if rel in listed:
            continue
        _check_note(path, rel, _note_id_from_path(rel), report)


def _fix_supersedes(paths: MemoryPaths, report: DoctorReport) -> list[Path]:
    changed: list[Path] = []
    if not paths.memories.exists():
        return changed
    by_id: dict[str, tuple[Path, Any]] = {}
    for path in sorted(paths.memories.rglob("*.md")):
        if path.name.startswith("."):
            continue
        try:
            note = parse_note(path.read_text(encoding="utf-8"))
        except (NoteFormatError, UnicodeDecodeError):
            continue
        by_id[note.id] = (path, note)
    for new_id, (_, new_note) in by_id.items():
        for old_id in new_note.frontmatter.supersedes:
            if old_id not in by_id:
                report.warnings.append(f"{new_id} 의 supersedes 대상 {old_id} 가 없다.")
                continue
            old_path, old_note = by_id[old_id]
            if old_note.frontmatter.status != "superseded":
                mutations.set_status(old_path, "superseded", superseded_by=new_id)
                changed.append(old_path)
                report.supersede_fixes.append({"old": old_id, "new": new_id})
    # fact_key 중복 경고
    keys: dict[str, list[str]] = {}
    for nid, (_, note) in by_id.items():
        fm = note.frontmatter
        if fm.type == "fact" and fm.status == "active" and fm.fact_key:
            keys.setdefault(fm.fact_key, []).append(nid)
    for key, ids in keys.items():
        if len(ids) > 1:
            report.warnings.append(f"fact_key '{key}' 의 active 노트가 {len(ids)}개다: {', '.join(ids)}")
    return changed


def _index(indexer: Indexer | None, path: Path, label: str, report: DoctorReport) -> None:
    if indexer is None:
        return
    if label in report.indexed or label in report.index_failed:
        return
    try:
        ok = indexer(path)
    except Exception:  # noqa: BLE001 - 색인 실패가 doctor 를 멈추면 안 된다
        ok = False
    (report.indexed if ok else report.index_failed).append(label)


def indexer_from_app(app, *, light: bool) -> tuple[Indexer | None, Callable[[], list[Path]] | None]:
    """App 에서 doctor 용 훅을 만든다. light 모드는 모델을 올리지 않는다: 이미 올라와 있을 때만 색인한다."""
    if app.indexer is None:
        return None, None
    if light and not app.embedder.loaded():
        return None, None

    def index_one(path: Path) -> bool:
        from .index.embedder import EmbeddingUnavailable

        try:
            app.indexer.index_path(path)
            return True
        except EmbeddingUnavailable:
            return False

    return index_one, app.indexer.pending


def _persist(paths: MemoryPaths, report: DoctorReport) -> None:
    try:
        paths.runtime.mkdir(parents=True, exist_ok=True)
        (paths.runtime / "doctor_last.json").write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    except OSError:
        pass


def load_last_report(paths: MemoryPaths) -> dict[str, Any] | None:
    f = paths.runtime / "doctor_last.json"
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
