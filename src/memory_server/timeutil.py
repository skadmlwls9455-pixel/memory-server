"""시각 도우미. 서버는 로컬 시간대의 오프셋 붙은 ISO 8601을 쓴다."""

from __future__ import annotations

from datetime import datetime


def now_local() -> datetime:
    return datetime.now().astimezone()
