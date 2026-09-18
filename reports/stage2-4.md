# 2·3·4단계 구현 보고 (색인, 회상, core, rebuild, stats, 백업, 열 점수, 확장)

2026-09-18 · 구현 환경: 리눅스 x86_64 컨테이너, CPU 4코어, RAM 15GB. 맥북 측정(T08)과 B2·age 실물 시험(T09, T10)은 사용자 몫이다.

## 시험 결과 요약

| ID | 시험 | 결과 | 비고 |
| --- | --- | --- | --- |
| T05 | 정답 20 / 무관 10 (bge-m3) | **통과** 20/20, 10/10 | `scripts/run_t05.py`. 조정 구간 0.435~0.50, 채택 0.45 (D-050). e5-small: 0.83 에서 19/20, 9/10 통과 |
| T06 | core 거부 3종 + 근거 무효화 | 통과 | `tests/test_core.py` |
| T07 | db 삭제 뒤 rebuild 동일성 | 통과 | 같은 질의 → 같은 ID·순서·유사도·열, 상태·core 동일. `tests/test_rebuild_stats.py` |
| T08 | 맥북 측정·Claude Desktop | **미실행 (사용자)** | 컨테이너 참고값은 아래. `scripts/measure_t08.py`, SETUP.md 3장 |
| T09 | 잠금 삭제 거부, 키 권한 | **미실행 (사용자)** | SETUP.md 6장에 절차 |
| T10 | 빈 디렉터리 복원 | 절차만 검증 | 가짜 age·rclone 으로 복원→rebuild→검색 통과 (`test_t10_shape_...`). 실물은 사용자 |
| T11 | 48h 공백, 실패 재시도, 재업로드 | 통과 | `tests/test_backup.py`, 가짜 provider |
| T12 | 감쇠·가열·6h·묶음·확장 | 통과 | `tests/test_heat.py`, `tests/test_recall.py` |
| T04 재실행 | "수정은 재색인됨" | 통과 | `test_external_edit_is_reindexed_by_doctor` |

```
uv run pytest                      → 통과 (실제 모델 테스트 1개 skip)
MEMORY_REAL_MODEL=1 pytest tests/test_real_model.py → 통과
```

## T05 상세 (bge-m3, 가상 노트 64개)

| min_similarity | 조정용 정답 top3 | 조정용 무관 공백 |
| --- | --- | --- |
| 0.45 | 15/15 | 10/10 |
| 0.50 | 15/15 | 10/10 |
| 0.55 | 12/15 | 10/10 |
| 0.60 | 8/15 | 10/10 |

평가용(분리): 정답 유사도 최소 0.499 / 중앙 0.648 / 최대 0.735, 무관 1위 유사도 최소 0.304 / 중앙 0.376 / 최대 0.423. 실패 사례 없음. recall 지연 중앙값 135ms(CPU).

주의: 이름이 없는 짧은 질의는 경계에 걸린다. "회사 어디 다녀?" 0.45, "직장" 0.425, "어느 회사에서 일해?" 0.582. 그래서 구간 아래쪽 0.45 를 골랐다. 노트와 질의를 같은 사람이 만들어 점수가 후할 수 있으므로 실사용 뒤 실패 사례로 재조정한다.

e5-small 실패 사례: "전 직장이 어디였고 왜 그만뒀어?" 정답 유사도 0.824 (임계 0.83 아래), "농구 트래블링 규칙" → "PR 리뷰 규칙" 0.841. e5 는 유사도 폭이 좁아(무관 0.77~0.84, 정답 0.82~0.92) 경계가 더 날카롭다.

## T08 컨테이너 참고값 (맥북 값이 아니다)

| 항목 | 값 |
| --- | --- |
| 모델 로딩 | 9.5s (캐시 웜) / 20s (첫 실행) |
| 로딩 후 RSS | 약 2.0GB, 피크 2.7GB |
| remember 평균 | 178ms (임베딩 포함) |
| recall 중앙값 / p95 | 111ms / 121ms |
| sqlite-vec | v0.1.9 로딩·코사인 KNN 통과 |

## 미구현·미검증

- T08, T09, T10 실물: 사용자 실행. 절차는 SETUP.md, RESTORE.md.
- stats 의 원격 사용량은 `rclone size` 결과를 백업 실행 때 갱신한다. 별도 주기 조회는 없다.
- 4단계 "T05 를 다시 돌려 나빠지지 않았는지 비교": 확장 경로가 들어간 상태에서 T05 를 돌린 것이 위 결과다 (상위 3건 기준 변화 없음).

## 테스트 쪽을 고친 부분

- 픽스처가 `config.toml` 의 임베더를 `fake/ngram-hash` 로 바꾼다 (D-036). 이에 따라 `test_load_after_init`, `test_init_files_are_loadable` 의 모델 확인 위치를 옮겼다.
- `test_tools_via_stdio_roundtrip`: 1단계의 `indexed is False` 를 `indexed is True` 로 (색인이 생겼으므로).
- `test_no_secrets_in_default_config`: "key" 단어 검사 → 비밀 값 패턴 검사 (키체인 서비스 이름에 key 가 들어간다).
- `min_similarity` 기본값 검사 0.45 → 0.47 → 0.45 (T05 조정 결과 반영).
- 가짜 임베더의 유사도 특성에 맞춰 recall 테스트의 예시 문장을 조정했다 (구조 검증이 목적).

## 파일에 쓰는 경로 (추가분)

- `index/db.py`: `db/memory.db` (캐시). `rebuild.py`: 임시 DB 생성 후 `os.replace` 로 교체 (캐시).
- `core.py`: `core.json`, `core.md` 를 임시 파일 + `os.replace` (노트가 아닌 생성물, D-042).
- `backup/`: `runtime/backup/` 아래 staging, pending 암호문, `backup_state.json`. vault 에는 쓰지 않는다. 원격 삭제 API 는 구현하지 않았다.
- `backup/restore.py`: 빈 대상 디렉터리에만 푼다.

기억 노트의 본문·제목을 다시 쓰거나 노트를 지우는 경로는 여전히 없다.
