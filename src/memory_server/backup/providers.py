"""외부 도구 경계: age, rclone, macOS 키체인. 테스트에서는 Fake 로 바꾼다.

비밀값은 절대 로그나 예외 메시지에 넣지 않는다.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


class ProviderError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# ------------------------------------------------------------------ 프로토콜


class Encryptor(Protocol):
    def encrypt(self, src: Path, dst: Path, recipient: str) -> None: ...

    def decrypt(self, src: Path, dst: Path, identity_file: Path) -> None: ...


@dataclass(frozen=True)
class RemoteObject:
    name: str
    size: int
    sha256: str | None  # 원격이 sha256 을 못 주면 None. 그때는 sha1 로 대조한다.
    sha1: str | None = None


class Uploader(Protocol):
    def upload(self, local: Path, remote_name: str) -> None: ...

    def stat(self, remote_name: str) -> RemoteObject | None: ...

    def usage_bytes(self) -> int | None: ...

    def list_names(self) -> list[str]: ...

    def download(self, remote_name: str, local: Path, version_id: str | None = None) -> None: ...


class Secrets(Protocol):
    def get(self, service: str) -> str | None: ...


# ------------------------------------------------------------------ 실제 구현


def _run(cmd: list[str], env: dict[str, str] | None = None, input_bytes: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(cmd, capture_output=True, env=env, input=input_bytes, check=False)
    except FileNotFoundError as e:
        raise ProviderError(f"{cmd[0]} 을 찾을 수 없다. SETUP.md 의 설치 절차를 따른다.") from e


class AgeCLI:
    """`age` 명령. 공개키로 암호화하고, 복원 때만 identity 파일로 복호화한다."""

    def __init__(self, binary: str = "age") -> None:
        self.binary = binary

    def encrypt(self, src: Path, dst: Path, recipient: str) -> None:
        if not recipient.startswith("age1"):
            raise ProviderError("age 공개키(age1...) 가 설정되지 않았다.")
        p = _run([self.binary, "-r", recipient, "-o", str(dst), str(src)])
        if p.returncode != 0:
            raise ProviderError(f"age 암호화 실패 (exit {p.returncode})")

    def decrypt(self, src: Path, dst: Path, identity_file: Path) -> None:
        p = _run([self.binary, "-d", "-i", str(identity_file), "-o", str(dst), str(src)])
        if p.returncode != 0:
            raise ProviderError(f"age 복호화 실패 (exit {p.returncode}). 개인키가 맞는지 확인한다.")


class MacKeychain:
    """`security find-generic-password -w -s <service>` 로 키체인에서 읽는다."""

    def get(self, service: str) -> str | None:
        p = _run(["security", "find-generic-password", "-w", "-s", service])
        if p.returncode != 0:
            return None
        return p.stdout.decode("utf-8").strip() or None


class EnvSecrets:
    """리눅스나 CI 용: 환경변수에서 읽는다. 서비스 이름을 대문자와 밑줄로 바꾼 이름."""

    def get(self, service: str) -> str | None:
        return os.environ.get(service.upper().replace("-", "_")) or None


class RcloneCLI:
    """rclone 으로 B2 에 올린다. 자격증명은 환경변수로만 넘기고 설정 파일에 쓰지 않는다.

    remote 는 "b2:bucket/prefix" 형식이다. 이름 "b2" 는 rclone 설정 파일이 아니라 아래 환경변수로 정의한다.
    """

    def __init__(self, remote: str, secrets: Secrets, key_id_service: str, key_service: str, binary: str = "rclone") -> None:
        self.remote = remote.rstrip("/")
        self.secrets = secrets
        self.key_id_service = key_id_service
        self.key_service = key_service
        self.binary = binary

    def _env(self) -> dict[str, str]:
        key_id = self.secrets.get(self.key_id_service)
        key = self.secrets.get(self.key_service)
        if not key_id or not key:
            raise ProviderError("B2 자격증명을 키체인에서 읽지 못했다. SETUP.md 의 키체인 절차를 따른다.")
        remote_name = self.remote.split(":", 1)[0].upper()
        env = dict(os.environ)
        env[f"RCLONE_CONFIG_{remote_name}_TYPE"] = "b2"
        env[f"RCLONE_CONFIG_{remote_name}_ACCOUNT"] = key_id
        env[f"RCLONE_CONFIG_{remote_name}_KEY"] = key
        env[f"RCLONE_CONFIG_{remote_name}_HARD_DELETE"] = "false"
        return env

    def _remote_path(self, name: str) -> str:
        return f"{self.remote}/{name}"

    def upload(self, local: Path, remote_name: str) -> None:
        p = _run([self.binary, "copyto", "--b2-hard-delete=false", str(local), self._remote_path(remote_name)], env=self._env())
        if p.returncode != 0:
            raise ProviderError(f"rclone 업로드 실패 (exit {p.returncode})")

    def stat(self, remote_name: str) -> RemoteObject | None:
        p = _run([self.binary, "lsjson", "--hash", self._remote_path(remote_name)], env=self._env())
        if p.returncode != 0:
            return None
        try:
            items = json.loads(p.stdout.decode("utf-8") or "[]")
        except json.JSONDecodeError:
            return None
        if not items:
            return None
        it = items[0]
        hashes = it.get("Hashes", {}) or {}
        return RemoteObject(name=remote_name, size=int(it.get("Size", -1)), sha256=hashes.get("sha256"), sha1=hashes.get("sha1"))

    def usage_bytes(self) -> int | None:
        p = _run([self.binary, "size", "--json", self.remote], env=self._env())
        if p.returncode != 0:
            return None
        try:
            return int(json.loads(p.stdout.decode("utf-8")).get("bytes", 0))
        except (json.JSONDecodeError, ValueError):
            return None

    def list_names(self) -> list[str]:
        p = _run([self.binary, "lsjson", "--b2-versions", self.remote], env=self._env())
        if p.returncode != 0:
            return []
        try:
            return [it["Path"] for it in json.loads(p.stdout.decode("utf-8") or "[]")]
        except (json.JSONDecodeError, KeyError):
            return []

    def download(self, remote_name: str, local: Path, version_id: str | None = None) -> None:
        cmd = [self.binary, "copyto"]
        if version_id:
            cmd.append("--b2-version-at=" + version_id)
        cmd += [self._remote_path(remote_name), str(local)]
        p = _run(cmd, env=self._env())
        if p.returncode != 0:
            raise ProviderError(f"rclone 다운로드 실패 (exit {p.returncode})")


# ------------------------------------------------------------------ 가짜 (테스트)


class FakeEncryptor:
    """XOR 없이 그냥 헤더를 붙여 복사한다. 암호화가 아니라 경계 검증용이다."""

    MAGIC = b"FAKE-AGE\n"

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[str] = []

    def encrypt(self, src: Path, dst: Path, recipient: str) -> None:
        self.calls.append(f"encrypt:{recipient}")
        if self.fail:
            raise ProviderError("가짜 암호화 실패")
        dst.write_bytes(self.MAGIC + recipient.encode() + b"\n" + src.read_bytes())

    def decrypt(self, src: Path, dst: Path, identity_file: Path) -> None:
        data = src.read_bytes()
        if not data.startswith(self.MAGIC):
            raise ProviderError("가짜 복호화 실패: 형식이 아니다")
        body = data[len(self.MAGIC):]
        _, _, payload = body.partition(b"\n")
        if identity_file.read_text().strip() != "FAKE-IDENTITY":
            raise ProviderError("가짜 복호화 실패: 개인키가 맞지 않는다")
        dst.write_bytes(payload)


@dataclass
class FakeUploader:
    """메모리 원격. fail_uploads 만큼 처음 업로드를 실패시킨다. 원격 수정·삭제 API 는 일부러 없다."""

    objects: dict[str, bytes] = field(default_factory=dict)
    fail_uploads: int = 0
    corrupt_next: bool = False
    upload_calls: list[str] = field(default_factory=list)

    def upload(self, local: Path, remote_name: str) -> None:
        self.upload_calls.append(remote_name)
        if self.fail_uploads > 0:
            self.fail_uploads -= 1
            raise ProviderError("가짜 업로드 실패")
        data = local.read_bytes()
        if self.corrupt_next:
            self.corrupt_next = False
            data = data[:-1]
        self.objects[remote_name] = data

    def stat(self, remote_name: str) -> RemoteObject | None:
        data = self.objects.get(remote_name)
        if data is None:
            return None
        return RemoteObject(name=remote_name, size=len(data), sha256=hashlib.sha256(data).hexdigest(), sha1=hashlib.sha1(data).hexdigest())

    def usage_bytes(self) -> int | None:
        return sum(len(v) for v in self.objects.values())

    def list_names(self) -> list[str]:
        return sorted(self.objects)

    def download(self, remote_name: str, local: Path, version_id: str | None = None) -> None:
        if remote_name not in self.objects:
            raise ProviderError("가짜 원격에 없다")
        local.write_bytes(self.objects[remote_name])


class FakeSecrets:
    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values = values or {}

    def get(self, service: str) -> str | None:
        return self.values.get(service)


def which(binary: str) -> bool:
    return shutil.which(binary) is not None
