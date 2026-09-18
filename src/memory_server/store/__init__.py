"""저장소 (SPEC 4, 5, 6, 7.3).

쓰기 절차 (SPEC 5):
1. 잠금을 얻고 입력을 검증한다.
2. 임시 파일에 쓴 뒤 존재하지 않는 최종 경로로 rename 한다. 기존 노트 변경은 mutations 모듈만 한다.
3. 이벤트 한 줄을 추가하고 flush 한다. 여기까지 끝나면 saved=true.
4. DB 를 갱신하고 임베딩한다. 실패하면 indexed=false.
5. 바뀐 경로만 git add 하고 커밋한다. 실패하면 committed=false.

모듈: models(자료형·상한), validation(검증), base(공통·읽기), writes(쓰기 도구), usage(mark_used).
"""

from __future__ import annotations

from .base import StoreBase
from .models import LIMITS, RememberInput, WriteResult, _collapse_ws, _hash_payload
from .usage import UsageMixin
from .validation import ValidationMixin
from .writes import WriteMixin


class MemoryStore(ValidationMixin, WriteMixin, UsageMixin, StoreBase):
    """도구 구현체. 믹스인은 상태를 갖지 않고 StoreBase 의 속성만 쓴다."""


__all__ = ["LIMITS", "MemoryStore", "RememberInput", "WriteResult", "_collapse_ws", "_hash_payload"]
