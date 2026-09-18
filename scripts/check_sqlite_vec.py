"""sqlite-vec 확장 로딩과 코사인 KNN 검증 (SPEC 2 실행 조건). 맥북에서 uv 가 받은 파이썬으로 실행한다.

  uv run python scripts/check_sqlite_vec.py
"""

from __future__ import annotations

import platform
import sqlite3
import sys

import sqlite_vec


def main() -> int:
    print(f"python {sys.version.split()[0]} ({sys.executable})")
    print(f"platform {platform.platform()} {platform.machine()}")
    print(f"sqlite {sqlite3.sqlite_version}")
    conn = sqlite3.connect(":memory:")
    try:
        conn.enable_load_extension(True)
    except AttributeError:
        print("실패: 이 파이썬은 확장 로딩이 막혀 있다. uv 가 받은 파이썬을 쓴다 (uv python install 3.11).")
        return 1
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    print(f"sqlite-vec {conn.execute('select vec_version()').fetchone()[0]}")
    conn.execute("create virtual table t using vec0(id text primary key, embedding float[4] distance_metric=cosine)")
    import struct

    rows = {"a": [1, 0, 0, 0], "b": [0.9, 0.1, 0, 0], "c": [0, 1, 0, 0]}
    for k, v in rows.items():
        conn.execute("insert into t(id, embedding) values (?, ?)", (k, struct.pack("4f", *v)))
    got = conn.execute("select id, distance from t where embedding match ? and k = 3 order by distance", (struct.pack("4f", 1, 0, 0, 0),)).fetchall()
    print("knn:", [(i, round(d, 4)) for i, d in got])
    ok = [i for i, _ in got] == ["a", "b", "c"] and abs(got[0][1]) < 1e-6
    print("코사인 KNN:", "통과" if ok else "실패")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
