# 1단계(저장) 종료 보고

2026-09-18 · 커밋 기준: 이 파일이 들어간 커밋

## 시험 결과

```
uv run pytest -k "t01 or t02 or t03 or t04"   → 14 passed
uv run pytest                                  → 200 passed
```

| ID | 시험 | 결과 | 비고 |
| --- | --- | --- | --- |
| T01 | remember 한 번 / 같은 초 두 번 / 같은 입력 10분 안 | 통과 | 노트·이벤트·커밋 각 1개. 같은 초는 입력이 다른 두 호출(D-014). 재호출은 기존 ID |
| T02 | A→B 정정 / supersedes 없이 같은 fact_key | 통과 | A는 status·superseded_by 두 줄 외 바이트 동일. FACT_CONFLICT에 기존 노트 전문 |
| T03 | amend / archive / unarchive / 대체 노트 unarchive | 통과 | 덧붙임만 늘고 본문 불변. 대체 노트는 SUPERSEDED 거부 |
| T04 | 강제 종료(이벤트 전, DB 전) / 외부 삭제 / 외부 수정 / 비밀 형식 / inference fact | 통과 | 재시작 뒤 중복·누락 없음. 삭제는 커밋되지 않음. **"수정은 재색인됨"은 DB가 없어 미검증** (2-1 뒤 재실행) |

실패하거나 실행하지 못한 항목: T04의 재색인 확인 한 가지 (2단계 의존).

## 구현 범위

- MCP 도구 5개: remember, read_memory, amend, archive, unarchive
- CLI: memory init, memory doctor, memory serve
- 쓰기 절차(SPEC 5): flock → 검증 → 임시 파일+rename → 이벤트 flush(saved) → indexed=false → 경로 지정 커밋(committed)
- doctor(SPEC 5 표 전체, Git HEAD 기준) + 정정 일관성 보정(D-030)
- 오류 코드: INVALID_INPUT, INVALID_TAG, INVALID_SOURCE, SECRET_DETECTED, NOT_FOUND, NOTE_CORRUPT, FACT_CONFLICT, INVALID_SUPERSEDES, NOT_ACTIVE, SUPERSEDED, LOCKED, VAULT_NOT_GIT, CONFIG_ERROR, INTERNAL

## 미구현 (계획대로 다음 단계)

| 항목 | 단계 |
| --- | --- |
| DB 색인, 재색인, 색인 대기 수, SIMILAR_EXISTS 경고 | 2-1 |
| recall, min_similarity 조정 | 2-2 |
| get_core, update_core, core.md 생성 | 2-3 |
| rebuild, stats | 2-4 |
| 백업 전체 | 3 |
| mark_used, 열 점수, 링크·태그 확장 | 4 |

## 덮어쓰기·삭제 경로 검색

`src/memory_server/*.py`에서 파일을 쓰거나 지우는 호출 전부:

| 위치 | 동작 | 기존 노트 영향 |
| --- | --- | --- |
| store.py `_write_new_file` | 임시 파일 → 존재하지 않는 최종 경로로 rename. 실패 시 임시 파일 unlink | 없음 (존재하면 거부) |
| mutations.py `_atomic_replace` | 덧붙임 추가 또는 status/superseded_by 줄 변경만 바이트 검증 후 rename | 허용된 세 가지만 |
| events.py `append_event` | jsonl에 append | 없음 |
| doctor.py `_repair_event_logs` | 잘린 꼬리를 recovery로 옮기고 온전한 줄까지 rename | 없음 (이벤트 로그) |
| doctor.py 삭제 복원 | `git checkout -- <경로>` | 복원만 |
| doctor.py `_persist` | runtime/doctor_last.json | 없음 |
| init_cmd.py | 없는 파일만 생성 | 없음 |

노트 제목·본문을 다시 쓰는 경로, 노트 파일을 지우는 경로, 삭제 도구, 원격 호출, 유료 API, `~/memory` 하드코딩: 없음.

## DECISIONS.md 요약 (D-001~D-034)

- 환경: mcp 2.x MCPServer(D-001), MEMORY_HOME 필수(D-002), 저장소 루트=server/(D-003), ID는 로컬 시각(D-004)
- init: 만든 파일만 커밋(D-006), 커밋 작성자 고정(D-007), core.md는 2단계(D-008), quotePath=false(D-019)
- 형식: 오류는 {error:{code,message,retryable}}(D-012), 예약 제목 줄 금지(D-015), 덧붙임 헤더는 첫 amend 때(D-016), reflection↔inference 양방향(D-017)
- 중복 방지: created/recovered/amended 이벤트의 input_hash(D-013, D-026, D-029)
- 정정: 대체된 노트 재대체 불가(D-021), 보관된 fact_key 재사용 허용 후 unarchive는 FACT_CONFLICT(D-022), created 이벤트 하나(D-023)
- doctor: Git HEAD 기준(D-027), 허용 변경이면 서버 변경(D-028), 정정 보정(D-030), 복원 대상 한정(D-031), 비밀은 형식만(D-033)

## 2단계로 넘어가도 되는가

된다. 근거:
- T01~T04가 재색인 확인 한 가지를 빼고 전부 통과했고, 그 하나는 2-1이 있어야 검증 가능하다.
- 기존 노트 변경이 `mutations.py` 한 곳으로 모여 있고 바이트 검증이 있다.
- doctor에 `indexer` 훅이 있어 2-1의 색인·재색인이 표의 나머지 두 칸에 그대로 붙는다.

넘어가기 전에 알아 둘 것:
- SPEC 2장의 sqlite-vec 로딩과 bge-m3 실행은 Apple Silicon 확인이 필요한데 구현 환경은 리눅스 컨테이너다. 여기서는 로딩과 코사인 KNN까지만 검증하고, 맥북 확인은 T08에서 한다.
- 모델 다운로드(bge-m3 약 2.3GB)가 컨테이너 네트워크 정책에 막힐 수 있다. 2-1 시작 전에 확인한다.
