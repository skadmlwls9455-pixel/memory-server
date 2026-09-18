"""알려진 비밀 형식 탐지 (SPEC 4). 완전한 탐지는 아니다. 값은 절대 로그나 오류에 싣지 않는다."""

from __future__ import annotations

import re

# (이름, 패턴). 이름만 오류에 실린다.
SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private_key_block", re.compile(r"-----BEGIN [A-Z0-9 ]*(PRIVATE KEY|CERTIFICATE)-----")),
    ("pem_block", re.compile(r"-----BEGIN [A-Z0-9 ]+-----")),
    ("age_secret_key", re.compile(r"\bAGE-SECRET-KEY-1[0-9A-Z]{20,}", re.IGNORECASE)),
    ("openai_or_anthropic_key", re.compile(r"\bsk-(?:ant-|proj-|live-|test-)?[A-Za-z0-9_\-]{16,}")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("aws_secret_key_assignment", re.compile(r"(?i)aws_secret_access_key\s*[:=]\s*[A-Za-z0-9/+=]{30,}")),
    ("github_token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}\b")),
    ("github_fine_grained_pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}\b")),
    ("gitlab_token", re.compile(r"\bglpat-[A-Za-z0-9\-_]{20,}\b")),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("stripe_key", re.compile(r"\b(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{16,}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\b")),
    ("backblaze_app_key", re.compile(r"\bK00[0-9][A-Za-z0-9+/]{27}\b")),
    ("recovery_codes", re.compile(r"(?i)(?:recovery|backup) codes?\s*[:：]\s*(?:[A-Za-z0-9]{4,6}[-\s]){3,}")),
)


def find_secret(text: str) -> str | None:
    """비밀 형식이 있으면 패턴 이름을, 없으면 None 을 돌려준다."""
    if not text:
        return None
    for name, pattern in SECRET_PATTERNS:
        if pattern.search(text):
            return name
    return None
