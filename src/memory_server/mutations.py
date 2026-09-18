"""기존 노트에 허용된 변경 세 가지 (SPEC 3.3).

1. `## 덧붙임` 아래에 날짜가 붙은 한 줄을 더한다.
2. frontmatter의 `status`를 바꾼다.
3. frontmatter의 `superseded_by`를 채운다.

이 모듈 밖에서는 기존 노트 파일에 쓰지 않는다. 모든 변경은 새 내용이 허용 범위인지
바이트 단위로 확인한 뒤 임시 파일과 rename으로 적용한다. 제목과 본문을 바꾸는 경로는 없다.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

from .notes import AMEND_HEADING, STATUSES, split_frontmatter

_STATUS_RE = re.compile(r"^status: .*$", re.MULTILINE)
_SUPERSEDED_BY_RE = re.compile(r"^superseded_by: .*$", re.MULTILINE)


class ForbiddenChange(RuntimeError):
    """허용된 세 가지 변경 밖의 결과가 만들어졌다. 코드 결함이다."""


def _atomic_replace(path: Path, old_text: str, new_text: str) -> None:
    current = path.read_text(encoding="utf-8")
    if current != old_text:
        raise ForbiddenChange(f"파일이 읽은 뒤에 바뀌었다: {path.name}")
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".md", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(new_text)
            f.flush()
        os.rename(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def append_amendment(path: Path, date: str, text: str) -> str:
    """파일 끝에 덧붙임 한 줄을 더한다. `## 덧붙임`이 없으면 제목부터 덧붙인다.

    결과는 반드시 `old + suffix` 다. 돌려주는 값은 덧붙인 줄이다.
    """
    if "\n" in text or "\r" in text:
        raise ValueError("덧붙임은 한 줄이어야 한다.")
    old = path.read_text(encoding="utf-8")
    line = f"- {date}: {text}\n"
    prefix = "" if old.endswith("\n") or old == "" else "\n"
    if _has_amend_heading(old):
        suffix = prefix + line
    else:
        suffix = prefix + "\n" + AMEND_HEADING + "\n" + line
    new = old + suffix
    if not new.startswith(old):  # pragma: no cover - 구조상 항상 참
        raise ForbiddenChange("덧붙임이 기존 내용을 바꿨다.")
    _atomic_replace(path, old, new)
    return line.rstrip("\n")


def _has_amend_heading(text: str) -> bool:
    _, rest = split_frontmatter(text)
    return any(line.rstrip() == AMEND_HEADING for line in rest.split("\n"))


def set_status(path: Path, status: str, superseded_by: str | None = None) -> None:
    """frontmatter의 `status`를 바꾸고, 주어지면 `superseded_by`를 채운다. 그 외는 바이트 단위로 같아야 한다."""
    if status not in STATUSES:
        raise ValueError(f"status 가 틀렸다: {status}")
    old = path.read_text(encoding="utf-8")
    fm_old, body_old = split_frontmatter(old)
    if not _STATUS_RE.search(fm_old):
        raise ForbiddenChange("frontmatter 에 status 줄이 없다.")
    fm_new = _STATUS_RE.sub(f"status: {status}", fm_old, count=1)
    if superseded_by is not None:
        if not _SUPERSEDED_BY_RE.search(fm_new):
            raise ForbiddenChange("frontmatter 에 superseded_by 줄이 없다.")
        fm_new = _SUPERSEDED_BY_RE.sub(f"superseded_by: {superseded_by}", fm_new, count=1)
    new = "---\n" + fm_new + "---\n" + body_old
    # 허용 범위 확인: 본문은 그대로, frontmatter 는 두 줄 외에 같다.
    _, body_new = split_frontmatter(new)
    if body_new != body_old:
        raise ForbiddenChange("본문이 바뀌었다.")
    diff = [
        (a, b)
        for a, b in zip(fm_old.split("\n"), fm_new.split("\n"), strict=True)
        if a != b
    ]
    for a, b in diff:
        if not (a.startswith("status: ") and b.startswith("status: ")) and not (
            a.startswith("superseded_by: ") and b.startswith("superseded_by: ")
        ):
            raise ForbiddenChange(f"허용되지 않은 frontmatter 변경: {a!r} -> {b!r}")
    if new != old:
        _atomic_replace(path, old, new)
