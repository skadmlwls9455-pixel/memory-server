"""복원 (SPEC 8.5).

풀 때 절대 경로, 상위 경로 이동, 심볼릭 링크, 하드 링크, 장치 파일을 거부한다.
"""

from __future__ import annotations

import tarfile
from dataclasses import dataclass, field
from pathlib import Path

from .providers import Encryptor, ProviderError, sha256_file


class UnsafeArchive(RuntimeError):
    pass


@dataclass
class RestoreReport:
    files: int = 0
    dirs: int = 0
    top_level: list[str] = field(default_factory=list)


def _check_member(m: tarfile.TarInfo) -> None:
    name = m.name
    if name.startswith("/") or name.startswith("\\"):
        raise UnsafeArchive(f"절대 경로 항목: {name}")
    parts = Path(name).parts
    if any(p == ".." for p in parts) or Path(name).is_absolute():
        raise UnsafeArchive(f"상위 경로 이동 항목: {name}")
    if m.issym() or m.islnk():
        raise UnsafeArchive(f"링크 항목: {name}")
    if m.isdev() or m.isfifo() or m.ischr() or m.isblk():
        raise UnsafeArchive(f"장치 항목: {name}")
    if not (m.isfile() or m.isdir()):
        raise UnsafeArchive(f"허용되지 않은 항목 종류: {name}")


def safe_extract(archive: Path, dest: Path) -> RestoreReport:
    """검사를 전부 통과한 뒤에만 푼다. 하나라도 걸리면 아무것도 쓰지 않는다."""
    report = RestoreReport()
    dest = dest.resolve()
    with tarfile.open(archive, "r:gz") as tf:
        members = tf.getmembers()
        for m in members:
            _check_member(m)
            target = (dest / m.name).resolve()
            if target != dest and dest not in target.parents:
                raise UnsafeArchive(f"대상 밖으로 나가는 항목: {m.name}")
        for m in members:
            if m.isdir():
                (dest / m.name).mkdir(parents=True, exist_ok=True)
                report.dirs += 1
            else:
                out = dest / m.name
                out.parent.mkdir(parents=True, exist_ok=True)
                src = tf.extractfile(m)
                assert src is not None
                with open(out, "wb") as f:
                    f.write(src.read())
                out.chmod(m.mode & 0o777 or 0o644)
                report.files += 1
    report.top_level = sorted({Path(m.name).parts[0] for m in members if m.name})
    return report


def restore_snapshot(
    ciphertext: Path,
    dest: Path,
    identity_file: Path,
    encryptor: Encryptor,
    *,
    expected_sha256: str | None = None,
) -> RestoreReport:
    """빈 디렉터리에 vault/ 와 server/ 를 푼다."""
    if dest.exists() and any(dest.iterdir()):
        raise UnsafeArchive(f"대상 디렉터리가 비어 있지 않다: {dest}")
    if expected_sha256 and sha256_file(ciphertext) != expected_sha256:
        raise ProviderError("암호문 해시가 기록과 다르다. 손상됐거나 다른 파일이다.")
    dest.mkdir(parents=True, exist_ok=True)
    plain = dest / ".restore.tar.gz"
    try:
        encryptor.decrypt(ciphertext, plain, identity_file)
        report = safe_extract(plain, dest)
    finally:
        if plain.exists():
            plain.unlink()
    return report
