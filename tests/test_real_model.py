"""실제 모델 시험. MEMORY_REAL_MODEL=1 일 때만 돈다 (모델을 받아야 한다)."""

import os

import pytest

from memory_server.app import build_app
from memory_server.init_cmd import init_memory_home

pytestmark = pytest.mark.skipif(os.environ.get("MEMORY_REAL_MODEL") != "1", reason="MEMORY_REAL_MODEL=1 일 때만")


def test_bge_m3_index_and_recall(paths):
    init_memory_home(paths)  # 기본 설정 = bge-m3
    app = build_app(paths)
    try:
        a = app.store.remember("현재 근무 회사는 노을소프트", "민서는 노을소프트 결제 플랫폼팀에서 일한다", type="fact", fact_key="self/employer")
        app.store.remember("고양이 이름은 모찌", "회색 고양이 모찌를 키운다")
        app.store.remember("주말 취미는 클라이밍", "토요일마다 볼더링을 한다")
        assert app.db.get_meta().model_id == "BAAI/bge-m3" and app.db.get_meta().dimension == 1024
        res = app.recaller.recall("민서 회사 어디 다녀?")  # 이름 없는 "회사 어디 다녀?" 는 0.45 로 경계값이다 (D-050)
        assert res.results and res.results[0].id == a.id
        assert app.recaller.recall("양자역학 벨 부등식").results == []
    finally:
        app.close()
