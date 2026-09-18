"""유사도 묶음 안에서만 열 점수로 순서를 바꾼다 (SPEC 7.2).

유사도 내림차순으로 놓고, 묶이지 않은 최상위 후보와 유사도 차이가 epsilon 이내인 후보를 한 묶음으로 만든다.
묶음 안은 열 점수 내림차순, 유사도 내림차순, ID 오름차순. 낮은 묶음이 높은 묶음을 넘지 않는다.
"""

from __future__ import annotations

from typing import Callable, TypeVar

T = TypeVar("T")


def bucket_sort(
    items: list[T],
    similarity: Callable[[T], float],
    heat: Callable[[T], float],
    ident: Callable[[T], str],
    epsilon: float,
) -> list[T]:
    ordered = sorted(items, key=lambda x: (-similarity(x), ident(x)))
    out: list[T] = []
    i = 0
    while i < len(ordered):
        top = similarity(ordered[i])
        j = i
        while j < len(ordered) and top - similarity(ordered[j]) <= epsilon + 1e-12:
            j += 1
        bucket = ordered[i:j]
        bucket.sort(key=lambda x: (-heat(x), -similarity(x), ident(x)))
        out.extend(bucket)
        i = j
    return out
