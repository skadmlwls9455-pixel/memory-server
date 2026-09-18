import pytest

from memory_server.secrets_guard import find_secret


@pytest.mark.parametrize(
    "text,name",
    [
        ("키는 sk-abcdefghijklmnopqrstuvwxyz1234 이다", "openai_or_anthropic_key"),
        ("sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef", "openai_or_anthropic_key"),
        ("AKIAIOSFODNN7EXAMPLE", "aws_access_key"),
        ("-----BEGIN RSA PRIVATE KEY-----\nMIIE...", "private_key_block"),
        ("-----BEGIN OPENSSH PRIVATE KEY-----", "private_key_block"),
        ("-----BEGIN PGP MESSAGE-----", "pem_block"),
        ("AGE-SECRET-KEY-1QYQSZQGPQYQSZQGPQYQSZQGPQYQSZQGPQYQSZQGPQYQSZQGPQYQS3PTVJA", "age_secret_key"),
        ("ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef0123456789", "github_token"),
        ("github_pat_11ABCDEFG0123456789_abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ", "github_fine_grained_pat"),
        ("xoxb-1234567890-abcdefghijklmnop", "slack_token"),
        ("AIzaSyA1234567890abcdefghijklmnopqrstuv", "google_api_key"),
        ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U", "jwt"),
        ("glpat-abcdefghijklmnopqrstuvwx", "gitlab_token"),
    ],
)
def test_detects_known_formats(text, name):
    assert find_secret(text) == name


@pytest.mark.parametrize(
    "text",
    [
        "",
        "sk-",
        "이번 주 회의는 sk-텔레콤 건물에서 했다",
        "Akiabu 라는 이름의 고양이",
        "비밀번호를 8자리로 바꿨다고 말했다",
        "BEGIN 은 시작이라는 뜻",
        "프로젝트 진행 상태는 계획 단계다",
        "ghp_short",
    ],
)
def test_ignores_normal_text(text):
    assert find_secret(text) is None
