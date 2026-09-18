"""T05: 한국어 정답 질의 20개, 무관 질의 10개 (SPEC 11).

조정용 질의로 min_similarity 를 정하고 평가용 질의로 평가한다. 두 집합은 분리되어 있다.
실제 모델을 쓴다. 실제 개인 기억은 넣지 않는다 (data/eval/notes.jsonl 은 가상 인물).

사용:
  MEMORY_HOME 은 쓰지 않는다. 임시 디렉터리를 만든다.
  uv run python scripts/run_t05.py --model BAAI/bge-m3 --dimension 1024
  uv run python scripts/run_t05.py --model intfloat/multilingual-e5-small --dimension 384
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from memory_server.app import build_app  # noqa: E402
from memory_server.config import RecallConfig  # noqa: E402
from memory_server.init_cmd import init_memory_home  # noqa: E402
from memory_server.paths import MemoryPaths  # noqa: E402


def load_jsonl(p: Path) -> list[dict]:
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


def setup(model: str, dimension: int, home: Path) -> MemoryPaths:
    paths = MemoryPaths(home=home)
    init_memory_home(paths)
    cfg = paths.config_toml.read_text(encoding="utf-8")
    cfg = cfg.replace('model_id = "BAAI/bge-m3"', f'model_id = "{model}"').replace("dimension = 1024", f"dimension = {dimension}")
    paths.config_toml.write_text(cfg, encoding="utf-8")
    return paths


def ingest(app, notes: list[dict]) -> dict[str, str]:
    title_to_id = {}
    for n in notes:
        r = app.store.remember(n["title"], n["body"], tags=n.get("tags"), type=n.get("type", "episode"), source=n.get("source", "user"), fact_key=n.get("fact_key"))
        if not r.indexed:
            raise SystemExit(f"색인 실패: {n['title']} {r.warnings}")
        title_to_id[n["title"]] = r.id
    return title_to_id


def raw_scores(app, queries: list[dict], title_to_id: dict[str, str]):
    """threshold 0 으로 상위 10 을 뽑아 (정답 순위, 정답 유사도, 1위 유사도) 를 기록한다."""
    app.recaller.config = app.config.__class__(**{**app.config.__dict__, "recall": RecallConfig(min_similarity=0.0, epsilon=app.config.recall.epsilon)})
    out = []
    for q in queries:
        t = time.perf_counter()
        res = app.recaller.recall(q["query"], k=10)
        ms = (time.perf_counter() - t) * 1000
        vec = [r for r in res.results if r.path == "vector"]
        ans = title_to_id.get(q["answer_title"]) if q["answer_title"] else None
        rank = next((i for i, r in enumerate(vec) if r.id == ans), None)
        out.append({
            "query": q["query"], "answer": q["answer_title"], "rank": rank,
            "answer_sim": vec[rank].similarity if rank is not None else None,
            "top_sim": vec[0].similarity if vec else None,
            "top_title": vec[0].title if vec else None,
            "ms": ms,
        })
    return out


def evaluate(rows, threshold: float, top_n: int = 3):
    rel = [r for r in rows if r["answer"]]
    irr = [r for r in rows if not r["answer"]]
    hits = sum(1 for r in rel if r["rank"] is not None and r["rank"] < top_n and r["answer_sim"] >= threshold)
    empties = sum(1 for r in irr if r["top_sim"] is None or r["top_sim"] < threshold)
    return hits, len(rel), empties, len(irr)


def choose_threshold(rows, rel_target: float = 0.9, irr_target: float = 0.9):
    """조건을 만족하는 구간 [lo, hi] 의 중간값. 없으면 무관 질의 공백률을 최대로 하는 값."""
    candidates = [round(0.30 + i * 0.005, 3) for i in range(121)]
    feasible = []
    best = None
    for t in candidates:
        h, nr, e, ni = evaluate(rows, t)
        score = (e / ni if ni else 1.0, h / nr if nr else 1.0)
        if best is None or score > best[0]:
            best = (score, t)
        if nr and ni and h / nr >= rel_target and e / ni >= irr_target:
            feasible.append(t)
    if feasible:
        return round((feasible[0] + feasible[-1]) / 2, 3), (feasible[0], feasible[-1])
    return best[1], None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="BAAI/bge-m3")
    ap.add_argument("--dimension", type=int, default=1024)
    ap.add_argument("--keep", action="store_true", help="임시 디렉터리를 남긴다")
    args = ap.parse_args()

    notes = load_jsonl(ROOT / "data/eval/notes.jsonl")
    tuning = load_jsonl(ROOT / "data/eval/tuning_queries.jsonl")
    evalq = load_jsonl(ROOT / "data/eval/eval_queries.jsonl")
    assert sum(1 for q in evalq if q["answer_title"]) == 20 and sum(1 for q in evalq if not q["answer_title"]) == 10
    titles = {n["title"] for n in notes}
    for q in tuning + evalq:
        assert q["answer_title"] is None or q["answer_title"] in titles, q
    assert not ({q["query"] for q in tuning} & {q["query"] for q in evalq})

    home = Path(tempfile.mkdtemp(prefix="memory-t05-"))
    os.environ["GIT_CONFIG_GLOBAL"] = str(home / "no-gitconfig")
    print(f"임시 MEMORY_HOME: {home}", file=sys.stderr)
    paths = setup(args.model, args.dimension, home)
    app = build_app(paths)
    try:
        t = time.perf_counter()
        title_to_id = ingest(app, notes)
        print(f"노트 {len(notes)}개 색인: {time.perf_counter() - t:.1f}s (모델 로딩 포함)", file=sys.stderr)

        tune_rows = raw_scores(app, tuning, title_to_id)
        chosen, band = choose_threshold(tune_rows)
        print(f"\n## 조정 (tuning {len(tuning)}개) → min_similarity = {chosen}" + (f" (가능 구간 {band[0]}~{band[1]})" if band else " (조건 만족 구간 없음, 차선값)"), file=sys.stderr)
        for t_ in (0.45, 0.50, 0.55, 0.60, 0.65, chosen):
            h, nr, e, ni = evaluate(tune_rows, t_)
            print(f"  t={t_:.3f}: 정답 top3 {h}/{nr}, 무관 공백 {e}/{ni}", file=sys.stderr)

        eval_rows = raw_scores(app, evalq, title_to_id)
        h, nr, e, ni = evaluate(eval_rows, chosen)
        print(f"\n## 평가 (eval 정답 {nr}, 무관 {ni}) @ min_similarity={chosen}", file=sys.stderr)
        print(f"  정답이 상위 3건: {h}/{nr} (기준 18 이상) → {'통과' if h >= 18 else '실패'}", file=sys.stderr)
        print(f"  무관 질의 빈 결과: {e}/{ni} (기준 9 이상) → {'통과' if e >= 9 else '실패'}", file=sys.stderr)
        lat = sorted(r["ms"] for r in eval_rows)
        print(f"  recall 지연 ms: 중앙값 {lat[len(lat)//2]:.0f}, 최대 {lat[-1]:.0f}", file=sys.stderr)
        print("\n## 실패 사례", file=sys.stderr)
        fails = 0
        for r in eval_rows:
            if r["answer"]:
                ok = r["rank"] is not None and r["rank"] < 3 and r["answer_sim"] >= chosen
                if not ok:
                    fails += 1
                    print(f"  [정답 누락] '{r['query']}' → 정답 순위 {r['rank']}, 정답 유사도 {r['answer_sim']}, 1위 '{r['top_title']}' {r['top_sim']:.3f}", file=sys.stderr)
            else:
                if r["top_sim"] is not None and r["top_sim"] >= chosen:
                    fails += 1
                    print(f"  [무관인데 결과] '{r['query']}' → 1위 '{r['top_title']}' {r['top_sim']:.3f}", file=sys.stderr)
        if not fails:
            print("  없음", file=sys.stderr)
        print("\n## 유사도 분포 (eval)", file=sys.stderr)
        rel_sims = sorted(r["answer_sim"] for r in eval_rows if r["answer"] and r["answer_sim"] is not None)
        irr_sims = sorted(r["top_sim"] for r in eval_rows if not r["answer"] and r["top_sim"] is not None)
        print(f"  정답 유사도: 최소 {rel_sims[0]:.3f}, 중앙 {rel_sims[len(rel_sims)//2]:.3f}, 최대 {rel_sims[-1]:.3f}", file=sys.stderr)
        print(f"  무관 1위 유사도: 최소 {irr_sims[0]:.3f}, 중앙 {irr_sims[len(irr_sims)//2]:.3f}, 최대 {irr_sims[-1]:.3f}", file=sys.stderr)
        result = {"model": args.model, "min_similarity": chosen, "band": band, "eval": {"hits": h, "rel": nr, "empties": e, "irr": ni}, "passed": h >= 18 and e >= 9}
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["passed"] else 1
    finally:
        app.close()
        if not args.keep:
            shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
