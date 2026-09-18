"""도구 오류. 코드, 한국어 설명, 재시도 가능 여부를 돌려준다 (SPEC 6)."""

from __future__ import annotations

from typing import Any


class MemoryError(Exception):
    """도구가 모델에게 돌려주는 오류. details는 응답에 그대로 실린다."""

    def __init__(self, code: str, message: str, *, retryable: bool = False, **details: Any) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": self.message, "retryable": self.retryable, **self.details}}


def invalid_input(message: str, **details: Any) -> MemoryError:
    return MemoryError("INVALID_INPUT", message, retryable=False, **details)


def not_found(note_id: str) -> MemoryError:
    return MemoryError("NOT_FOUND", f"ID {note_id} 인 노트가 없다.", retryable=False, id=note_id)
