"""열 점수 (SPEC 7.3). 0~1. 최근에 쓰였다고 신고된 정도이지 중요도가 아니다."""

from __future__ import annotations

from datetime import datetime

from ..config import HeatConfig


def decayed_heat(heat: float, heat_at: datetime, now: datetime, cfg: HeatConfig) -> float:
    """읽을 때 계산한다: heat × 0.5^(경과일 / 반감기). 저장값은 건드리지 않는다."""
    days = max(0.0, (now - heat_at).total_seconds() / 86400.0)
    return heat * (0.5 ** (days / cfg.half_life_days))


def reheat(heat: float, heat_at: datetime, now: datetime, cfg: HeatConfig) -> float:
    """신고되면 먼저 그 시각까지 감쇠하고 min(1, 값 + 가열량)."""
    return min(1.0, decayed_heat(heat, heat_at, now, cfg) + cfg.boost)
