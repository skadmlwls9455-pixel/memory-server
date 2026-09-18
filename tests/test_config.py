import pytest

from memory_server.config import (
    DEFAULT_CONFIG_TOML,
    DEFAULT_TAG_RULES_YAML,
    TAG_CATEGORIES,
    ConfigError,
    load_config,
    load_tag_rules,
    parse_config,
    parse_tag_rules,
)


def test_default_config_parses_to_spec_values():
    cfg = parse_config(DEFAULT_CONFIG_TOML)
    assert cfg.embedding.model_id == "BAAI/bge-m3"
    assert cfg.embedding.dimension == 1024
    assert cfg.recall.min_similarity == 0.45
    assert cfg.recall.relevance_calibrated is False
    assert cfg.recall.epsilon == 0.02
    assert cfg.recall.similar_exists_threshold == 0.92
    assert cfg.heat.initial == 0.5
    assert cfg.heat.boost == 0.3
    assert cfg.heat.half_life_days == 30
    assert cfg.heat.reheat_window_hours == 6
    assert cfg.write.dedupe_window_minutes == 10
    assert cfg.backup.min_interval_hours == 24
    assert cfg.backup.stale_warning_hours == 48


def test_empty_config_uses_defaults():
    assert parse_config("") == parse_config(DEFAULT_CONFIG_TOML)


def test_partial_override():
    cfg = parse_config('[recall]\nmin_similarity = 0.6\nrelevance_calibrated = true\n')
    assert cfg.recall.min_similarity == 0.6
    assert cfg.recall.relevance_calibrated is True
    assert cfg.heat.initial == 0.5


def test_unknown_key_rejected():
    with pytest.raises(ConfigError):
        parse_config("[recall]\nmin_similarty = 0.5\n")


@pytest.mark.parametrize(
    "text",
    [
        "[embedding]\ndimension = 0\n",
        "[recall]\nmin_similarity = 1.5\n",
        "[heat]\nhalf_life_days = 0\n",
        "not toml ===",
    ],
)
def test_invalid_values_rejected(text):
    with pytest.raises(ConfigError):
        parse_config(text)


def test_no_secrets_in_default_config():
    for needle in ("key", "token", "secret", "password"):
        assert needle not in DEFAULT_CONFIG_TOML.lower()


def test_load_config_missing_file(paths):
    with pytest.raises(ConfigError):
        load_config(paths.config_toml)


def test_load_after_init(initialized):
    cfg = load_config(initialized.config_toml)
    rules = load_tag_rules(initialized.tag_rules_yaml)
    assert cfg.embedding.dimension == 1024
    assert set(rules.categories) == set(TAG_CATEGORIES)


def test_default_tag_rules_parse():
    rules = parse_tag_rules(DEFAULT_TAG_RULES_YAML)
    assert set(rules.categories) == set(TAG_CATEGORIES)
    assert all(v == frozenset() for v in rules.categories.values())


@pytest.mark.parametrize(
    "tag,valid",
    [
        ("주제/직장", True),
        ("사람/김철수", True),
        ("프로젝트/기억시스템", True),
        ("상태/진행중", True),
        ("시기/2026", True),
        ("직장", False),
        ("주제/", False),
        ("/직장", False),
        ("주제/a/b", False),
        ("취미/독서", False),
        ("주제/직장".encode("utf-8").decode("utf-8"), True),
    ],
)
def test_tag_validity_free_names(tag, valid):
    rules = parse_tag_rules(DEFAULT_TAG_RULES_YAML)
    assert rules.is_valid(tag) is valid


def test_tag_validity_nfd_input_normalized():
    import unicodedata

    rules = parse_tag_rules(DEFAULT_TAG_RULES_YAML)
    nfd = unicodedata.normalize("NFD", "주제/직장")
    assert nfd != "주제/직장"
    assert rules.is_valid(nfd)


def test_tag_rules_restricted_values():
    text = """
categories:
  주제: {values: [직장, 건강]}
  사람: {values: []}
  프로젝트: {values: []}
  상태: {values: [진행중, 완료]}
  시기: {values: []}
"""
    rules = parse_tag_rules(text)
    assert rules.is_valid("주제/직장")
    assert not rules.is_valid("주제/취미")
    assert rules.is_valid("사람/누구든")
    assert not rules.is_valid("상태/보류")


@pytest.mark.parametrize(
    "text",
    [
        "categories: [주제]",
        "categories:\n  취미: {values: []}\n",
        "categories:\n  주제: {values: []}\n",  # 나머지 분류 누락
        "categories:\n  주제: {values: 직장}\n  사람: {values: []}\n  프로젝트: {values: []}\n  상태: {values: []}\n  시기: {values: []}\n",
        ": bad: yaml: [",
    ],
)
def test_invalid_tag_rules_rejected(text):
    with pytest.raises(ConfigError):
        parse_tag_rules(text)
