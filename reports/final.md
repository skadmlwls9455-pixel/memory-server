# 최종 보고 (4-3)

2026-09-18. 구현 환경은 리눅스 컨테이너, 실물 시험은 사용자 맥북(M3 Pro 18GB, macOS 14.6)에서 `memory setup` 으로.

## T01~T12 전체 재실행

```
uv run pytest                      267개 통과 (가짜 임베더; 실제 모델 1개는 MEMORY_REAL_MODEL=1 에서 통과)
uv run pytest -k "t01 ... t12"     27개 통과
scripts/run_t05.py                 bge-m3 통과, e5-small 통과
```

| ID | 결과 | 근거 |
| --- | --- | --- |
| T01 | 통과 | tests/test_remember.py. 노트·이벤트·커밋 각 1, 같은 초 다른 ID, 10분 중복은 기존 ID |
| T02 | 통과 | tests/test_correction.py. 정정 뒤 A 는 두 줄 외 바이트 동일, FACT_CONFLICT 에 전문 |
| T03 | 통과 | amend 는 append 만, archive/unarchive, 대체 노트 거부 |
| T04 | 통과 | tests/test_doctor.py + test_index.py. 두 지점 강제 종료 뒤 중복·누락 없음, 삭제 복원(커밋 안 됨), 외부 수정 재색인, 비밀·출처 거부 |
| T05 | 통과 | bge-m3: 정답 20/20, 무관 10/10 @ 0.45 (구간 0.435~0.50). reports/stage2-4.md |
| T06 | 통과 | tests/test_core.py. 근거 없음·reflection·1,500자 초과 거부, 근거 무효화 시 문장 숨김 |
| T07 | 통과 | tests/test_rebuild_stats.py. db 삭제 후 rebuild → 같은 ID·순서·유사도·열·core |
| T08 | 통과(일부 미검증) | reports/t08.md. Desktop 에서 저장 3건 saved/indexed/committed, 새 대화에서 recall 로 3건 회상(0.63/0.53/0.48). "미리보기만으로 단정하지 않음" 은 노트 3개라 전부 전문이어서 미검증 |
| T09 | 통과 | reports/t09.md. 삭제 권한 키로도 Access Denied, 업로드 키에 deleteFiles 없음, compliance 30일 |
| T10 | 통과 | reports/t10.md. 비밀번호 관리자 키와 종이 사본 키 각 1회. 종이 사본 회차에서 노트 3개 rebuild 후 검색 적중 |
| T11 | 통과 | tests/test_backup.py. 48h 경고, 같은 이름 재시도, 재업로드가 최신성 안 바꿈 |
| T12 | 통과 | tests/test_heat.py, test_recall.py. 0.5→0.25→0.55, 6h 재신고 무시, 묶음 정렬, 보관 노트 확장 차단 |

맥북 측정 표(모델 로딩·메모리·recall 지연)는 사용자 맥의 `reports/t08.md` 에 있고 이 저장소에는 아직 안 붙었다. 컨테이너 참고값: 로딩 9.5s, RSS 2.0GB, recall 111ms.

## DECISIONS.md 요약 (D-001~D-053)

- **환경**: mcp 2.x MCPServer(FastMCP 후속), MEMORY_HOME 필수, 이 저장소가 server/, ID 는 로컬 시각, 테스트는 가짜 임베더.
- **형식·규칙**: 오류는 구조화 dict, 예약 제목 줄 금지, `## 덧붙임` 은 첫 amend 때, reflection↔inference 양방향, 정정은 created 이벤트 하나, 보관된 fact_key 재사용 허용.
- **안전장치**: 기존 노트 변경은 mutations 한 곳에서 바이트 검증, doctor 는 Git HEAD 기준 + 정정 보정, recovered 에 input_hash, 비밀 탐지는 형식만.
- **회상**: KNN 200 후 필터, 확장에도 입력 필터, 태그 확장은 공유 수 순, min_similarity 0.45(구간 아래쪽).
- **백업**: 자격증명은 키체인→환경변수, pending 재시도는 해시 일치 시, 리허설 날짜는 runtime, 복원은 2단계 부트스트랩.
- **운영**: 설치 마법사가 B2 API 로 버킷·키를 만들고 T08~T10 을 실행, 모델 미리 로딩, store 패키지 분리.

## SPEC 과 다르게 구현한 것

| 항목 | SPEC | 구현 | 기록 |
| --- | --- | --- | --- |
| 서버 클래스 | FastMCP | MCPServer (같은 SDK 의 2.x 이름) | D-001 |
| doctor 표 | 7행 | + 정정 일관성 보정(supersedes 가 가리키는 active 노트를 superseded 로) | D-030 |
| DB memories 열 | heat, heat_at | + used_at (6시간 판정) | D-037 |
| 태그 확장 | "3개를 공유하는" | 상위 3개 중 하나라도 공유, 공유 수 순 | D-041 |
| 모델 로딩 | 첫 recall/색인 때 | 시작 직후 백그라운드 미리 로딩(끌 수 있음) | D-052 |
| 이벤트 로그 | 아홉 가지 type | 그대로. `recovered`/`amended` 에 input_hash, `restored` 에 path 를 data 로 추가 | D-013, D-029 |
| 설치 | 사용자가 SETUP.md 대로 | `memory setup` 마법사가 대행 (계정 가입·2FA·마스터 키·인쇄 제외) | D-051 |

추가하지 않은 것: 삭제 도구, 본문 덮어쓰기, 유료 API, 새 업로드 대상, 잠금 기간 단축, 10장 보류 항목. 검색 결과에서 확인했다 (reports/stage1.md, stage2-4.md).

## 남은 위험

1. **기기 범위.** 이 서버는 맥북의 Claude Desktop 에서만 쓰인다. 폰·웹 대화는 저장도 회상도 안 된다. 사용자의 실제 사용은 폰이 많아서 가치가 좁다. 해결하려면 원격 전송(streamable-http)+인증+상시 호스트가 필요하고 이는 SPEC 10 의 "상시 서버" 보류를 되살리는 결정이다. 되살릴 조건("맥북이 없어서 못 쓰는 일이 잦으면")은 이미 충족된 것으로 보인다.
2. **임계값 여유.** 0.45 는 무관 1위 최대 0.423 과 0.027 차이고, 실제 대화에서 정답 0.48 이 나왔다. 실패 사례가 쌓이면 재조정한다(config 한 줄).
3. **T05 편향.** 노트와 질의를 같은 사람이 만들었다. 실사용 실패 사례로 보정해야 한다.
4. **미검증 항목.** 미리보기만으로 단정하지 않는지(노트 4개 이상 필요), 맥북 측정 표 반영, `memory setup golive` 실행(아직 안 함).
5. **토큰 비용.** 대화마다 도구 설명 약 2천 토큰. 줄였지만 0 은 아니다. 기억이 쓰일 때만 값어치를 한다.
6. **운영 습관.** 분기 복원 리허설, 마스터 키 삭제(사용자가 콘솔에서), Command Line Tools 업데이트(브루 소스 빌드 방지).
7. **동시 실행.** 서버가 켜진 채 `memory rebuild` 를 돌리면 서버를 재시작해야 한다. mark_used 마다 커밋이 생겨 이력이 길어진다(동작 문제 없음).

## 실사용 전환 여부

코드와 시험은 준비됐다. 다만 위 1번 때문에 사용자가 "맥북 공부용 기억" 으로 쓸지, 원격 전송을 먼저 붙일지 정한 뒤에 `memory setup golive` 를 하는 게 맞다.
