"""T08 측정: 모델 로딩 시간, 최대 메모리, recall 지연 (SPEC 11). 맥북에서 실행한다.

  uv run python scripts/measure_t08.py                 # 임시 vault 에 가상 노트 60개를 넣고 잰다
  uv run python scripts/measure_t08.py --model intfloat/multilingual-e5-small --dimension 384
  MEMORY_HOME=~/memory-test uv run python scripts/measure_t08.py --existing   # 이미 있는 시험 vault 로 잰다 (노트를 넣지 않는다)

결과는 stderr 로 표를 내고 stdout 으로 JSON 한 줄을 낸다. 보고서(reports/t08.md)에 붙인다.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import resource
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from memory_server.app import build_app  # noqa: E402
from memory_server.init_cmd import init_memory_home  # noqa: E402
from memory_server.paths import MemoryPaths, get_paths  # noqa: E402

QUERIES = ["회사 어디 다녀", "고양이 이름", "11월 여행 계획", "배포 규칙", "임베딩 모델 결정", "아침 운동 루틴", "알레르기", "독서 모임 언제", "투자 원칙", "온콜 순번"]


def maxrss_mb() -> float:
    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return r / (1024 * 1024) if sys.platform == "darwin" else r / 1024


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=None)
    ap.add_argument("--dimension", type=int, default=None)
    ap.add_argument("--existing", action="store_true")
    ap.add_argument("--runs", type=int, default=3)
    args = ap.parse_args()

    tmp = None
    if args.existing:
        paths = get_paths()
    else:
        tmp = Path(tempfile.mkdtemp(prefix="memory-t08-"))
        os.environ["GIT_CONFIG_GLOBAL"] = str(tmp / "no-gitconfig")
        paths = MemoryPaths(home=tmp)
        init_memory_home(paths)
    if args.model:
        cfg = paths.config_toml.read_text(encoding="utf-8")
        cfg = cfg.replace('model_id = "BAAI/bge-m3"', f'model_id = "{args.model}"').replace("dimension = 1024", f"dimension = {args.dimension}")
        paths.config_toml.write_text(cfg, encoding="utf-8")

    from memory_server.index.db import load_sqlite_vec
    import sqlite3

    vec_version = load_sqlite_vec(sqlite3.connect(":memory:"))
    info = {"platform": platform.platform(), "machine": platform.machine(), "python": sys.version.split()[0], "sqlite_vec": vec_version}
    rss_before = maxrss_mb()
    app = build_app(paths)
    try:
        info["model"] = app.config.embedding.model_id
        t = time.perf_counter()
        app.embedder.embed_query("워밍업")  # 여기서 모델이 올라온다
        info["model_load_s"] = round(time.perf_counter() - t, 2)
        info["rss_after_load_mb"] = round(maxrss_mb(), 0)
        try:
            import torch

            info["device"] = "mps" if torch.backends.mps.is_available() else ("cuda" if torch.cuda.is_available() else "cpu")
        except Exception:  # noqa: BLE001
            info["device"] = "unknown"

        if not args.existing:
            notes = [json.loads(l) for l in (ROOT / "data/eval/notes.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
            t = time.perf_counter()
            for n in notes:
                app.store.remember(n["title"], n["body"], tags=n.get("tags"), type=n.get("type", "episode"), source=n.get("source", "user"), fact_key=n.get("fact_key"))
            info["ingest_notes"] = len(notes)
            info["ingest_s"] = round(time.perf_counter() - t, 1)
            info["remember_ms_avg"] = round(info["ingest_s"] * 1000 / len(notes), 0)

        lat = []
        for _ in range(args.runs):
            for q in QUERIES:
                t = time.perf_counter()
                app.recaller.recall(q, k=10)
                lat.append((time.perf_counter() - t) * 1000)
        info["recall_ms_median"] = round(statistics.median(lat), 0)
        info["recall_ms_p95"] = round(sorted(lat)[int(len(lat) * 0.95) - 1], 0)
        info["recall_ms_max"] = round(max(lat), 0)
        info["peak_rss_mb"] = round(maxrss_mb(), 0)
        info["rss_baseline_mb"] = round(rss_before, 0)
    finally:
        app.close()
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)

    print("| 항목 | 값 |", file=sys.stderr)
    print("| --- | --- |", file=sys.stderr)
    for k, v in info.items():
        print(f"| {k} | {v} |", file=sys.stderr)
    print(json.dumps(info, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
