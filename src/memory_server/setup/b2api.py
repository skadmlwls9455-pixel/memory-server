"""Backblaze B2 네이티브 API 최소 클라이언트 (버킷·잠금·수명주기·키). 외부 의존성 없음.

마스터(또는 전체 권한) 키로 한 번 인증해서 버킷과 업로드 전용 키를 만든다.
이 모듈은 키 값을 로그나 예외 메시지에 넣지 않는다.
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

AUTH_URL = "https://api.backblazeb2.com/b2api/v3/b2_authorize_account"
UPLOAD_KEY_CAPS = ["listFiles", "readFiles", "writeFiles"]
DELETE_TEST_CAPS = ["listFiles", "readFiles", "writeFiles", "deleteFiles"]
LIFECYCLE_RULE = {"daysFromHidingToDeleting": 1, "daysFromUploadingToHiding": 39, "fileNamePrefix": "snapshots/"}
RETENTION_DAYS = 30
NEEDED_CAPS = {"listBuckets", "writeBuckets", "listKeys", "writeKeys", "writeBucketRetentions"}

Transport = Callable[[str, dict[str, str], bytes | None], dict[str, Any]]


class B2Error(RuntimeError):
    pass


def _http(url: str, headers: dict[str, str], body: bytes | None) -> dict[str, Any]:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode("utf-8"))
            code = detail.get("code", "")
            msg = detail.get("message", "")
        except Exception:  # noqa: BLE001
            code, msg = str(e.code), ""
        raise B2Error(f"B2 API 오류 {e.code} {code}: {msg}") from None
    except urllib.error.URLError as e:
        raise B2Error(f"B2 API 연결 실패: {e.reason}") from None


@dataclass
class Bucket:
    id: str
    name: str
    file_lock_enabled: bool
    retention_mode: str | None
    retention_days: int | None
    lifecycle_rules: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class AppKey:
    key_id: str
    key_name: str
    capabilities: list[str]
    bucket_id: str | None
    secret: str | None = None  # 생성 직후에만 있다


class B2Client:
    def __init__(self, transport: Transport = _http) -> None:
        self._t = transport
        self.account_id = ""
        self.api_url = ""
        self._token = ""
        self.capabilities: list[str] = []

    # ------------------------------------------------------------------ 인증

    def authorize(self, key_id: str, key: str) -> None:
        cred = base64.b64encode(f"{key_id}:{key}".encode("utf-8")).decode("ascii")
        data = self._t(AUTH_URL, {"Authorization": f"Basic {cred}"}, None)
        self.account_id = data["accountId"]
        self._token = data["authorizationToken"]
        api = data.get("apiInfo", {}).get("storageApi", {})
        self.api_url = api.get("apiUrl") or data.get("apiUrl", "")
        self.capabilities = list(api.get("capabilities") or data.get("allowed", {}).get("capabilities", []))
        missing = NEEDED_CAPS - set(self.capabilities)
        if missing:
            raise B2Error(f"이 키에는 필요한 권한이 없다: {sorted(missing)}. 마스터 키나 전체 권한 키를 쓴다.")

    def _post(self, op: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self._token:
            raise B2Error("먼저 authorize 해야 한다.")
        headers = {"Authorization": self._token, "Content-Type": "application/json"}
        return self._t(f"{self.api_url}/b2api/v3/{op}", headers, json.dumps(payload).encode("utf-8"))

    # ------------------------------------------------------------------ 버킷

    @staticmethod
    def _bucket(d: dict[str, Any]) -> Bucket:
        lock = d.get("fileLockConfiguration", {}).get("value", {}) or {}
        ret = (lock.get("defaultRetention") or {})
        period = ret.get("period") or {}
        days = None
        if period.get("unit") == "days":
            days = int(period.get("duration", 0))
        elif period.get("unit") == "years":
            days = int(period.get("duration", 0)) * 365
        return Bucket(
            id=d["bucketId"], name=d["bucketName"], file_lock_enabled=bool(lock.get("isFileLockEnabled")),
            retention_mode=ret.get("mode"), retention_days=days, lifecycle_rules=list(d.get("lifecycleRules") or []),
        )

    def find_bucket(self, name: str) -> Bucket | None:
        data = self._post("b2_list_buckets", {"accountId": self.account_id, "bucketName": name})
        for b in data.get("buckets", []):
            if b["bucketName"] == name:
                return self._bucket(b)
        return None

    def create_locked_bucket(self, name: str) -> Bucket:
        """Object Lock 을 켠 비공개 버킷. 있으면 그대로 돌려준다 (잠금이 꺼져 있으면 오류)."""
        existing = self.find_bucket(name)
        if existing is not None:
            if not existing.file_lock_enabled:
                raise B2Error(f"버킷 {name} 은 이미 있는데 Object Lock 이 꺼져 있다. 잠금은 만들 때만 켤 수 있으니 다른 이름을 쓴다.")
            return existing
        data = self._post(
            "b2_create_bucket",
            {"accountId": self.account_id, "bucketName": name, "bucketType": "allPrivate", "fileLockEnabled": True, "lifecycleRules": [LIFECYCLE_RULE]},
        )
        return self._bucket(data)

    def ensure_retention_and_lifecycle(self, bucket: Bucket) -> Bucket:
        """기본 보존 compliance 30일과 수명 주기 규칙을 맞춘다."""
        payload = {
            "accountId": self.account_id,
            "bucketId": bucket.id,
            "defaultRetention": {"mode": "compliance", "period": {"duration": RETENTION_DAYS, "unit": "days"}},
            "lifecycleRules": [LIFECYCLE_RULE],
        }
        data = self._post("b2_update_bucket", payload)
        b = self._bucket(data)
        if b.retention_mode != "compliance" or b.retention_days != RETENTION_DAYS:
            raise B2Error(f"기본 보존 설정이 반영되지 않았다: {b.retention_mode} {b.retention_days}")
        return b

    # ------------------------------------------------------------------ 키

    def list_keys(self) -> list[AppKey]:
        out: list[AppKey] = []
        start = None
        while True:
            payload: dict[str, Any] = {"accountId": self.account_id, "maxKeyCount": 1000}
            if start:
                payload["startApplicationKeyId"] = start
            data = self._post("b2_list_keys", payload)
            for k in data.get("keys", []):
                out.append(AppKey(k["applicationKeyId"], k.get("keyName", ""), list(k.get("capabilities", [])), k.get("bucketId")))
            start = data.get("nextApplicationKeyId")
            if not start:
                return out

    def create_key(self, name: str, bucket_id: str, capabilities: list[str], valid_seconds: int | None = None) -> AppKey:
        payload: dict[str, Any] = {"accountId": self.account_id, "capabilities": capabilities, "keyName": name, "bucketId": bucket_id}
        if valid_seconds:
            payload["validDurationInSeconds"] = valid_seconds
        d = self._post("b2_create_key", payload)
        return AppKey(d["applicationKeyId"], d["keyName"], list(d["capabilities"]), d.get("bucketId"), secret=d["applicationKey"])

    def delete_key(self, key_id: str) -> None:
        self._post("b2_delete_key", {"applicationKeyId": key_id})


def check_upload_key(keys: list[AppKey], key_id: str, bucket_id: str) -> list[str]:
    """업로드 키가 SPEC 8.2 대로인지. 문제 목록을 돌려준다 (비어 있으면 통과)."""
    k = next((x for x in keys if x.key_id == key_id), None)
    if k is None:
        return ["업로드 키를 계정에서 찾을 수 없다"]
    problems = []
    if k.bucket_id != bucket_id:
        problems.append("업로드 키가 버킷에 한정되어 있지 않다")
    extra = set(k.capabilities) - set(UPLOAD_KEY_CAPS)
    if extra:
        problems.append(f"업로드 키에 불필요한 권한이 있다: {sorted(extra)}")
    if "deleteFiles" in k.capabilities:
        problems.append("업로드 키에 deleteFiles 가 있다")
    return problems
