# memory-server

개인 AI 기억 시스템의 로컬 MCP 서버 (SPEC v0.3). 명세는 `SPEC.md`, 작업 규칙은 `CLAUDE.md`, 결정 기록은 `DECISIONS.md`, 설치·운영은 `SETUP.md`, 복원은 `RESTORE.md`.

## 구조

```
src/memory_server/
  paths.py, config.py, lock.py, events.py, naming.py, notes.py   # 원본(마크다운·이벤트) 계층
  mutations.py        # 기존 노트에 허용된 세 가지 변경만 (바이트 검증)
  store.py            # remember, read_memory, amend, archive, unarchive, mark_used
  doctor.py           # SPEC 5 표: 복원, recovered, external_edit, 로그 조각, 정정 보정, 색인 대기
  secrets_guard.py    # 알려진 비밀 형식 거부
  index/              # SQLite + sqlite-vec 캐시, 임베더(bge-m3 / e5 / fake), 색인기
  recall/             # 벡터 검색, 링크·태그 확장, 열 점수, epsilon 묶음 정렬
  core.py             # core.json(원본) → core.md(생성물), get_core / update_core
  rebuild.py, stats.py
  backup/             # 스냅샷(tar+age), rclone 업로드·검증, 주기·경고, 안전 복원, provider 경계
  app.py              # 구성 루트
  server.py           # MCP 도구 10개
  cli.py              # memory init | doctor | rebuild | backup | backup-check | restore | restore-drill-done | stats | serve
scripts/              # check_sqlite_vec.py, run_t05.py(임계값 조정·평가), measure_t08.py
data/eval/            # T05 용 가상 노트 64개, 조정용·평가용 질의 (실제 개인 정보 없음)
deploy/               # launchd plist 템플릿
reports/              # 단계 보고
```

## 개발

```sh
uv sync
uv run pytest                       # 가짜 임베더. 모델을 받지 않는다
MEMORY_REAL_MODEL=1 uv run pytest tests/test_real_model.py   # 실제 bge-m3
uv run python scripts/run_t05.py    # 임계값 조정과 T05 평가 (실제 모델)
```

## 실행

모든 경로는 환경변수 `MEMORY_HOME` 아래에 만들어진다. 시험 중에는 실제 vault 가 아닌 별도 디렉터리를 쓴다.

```sh
export MEMORY_HOME=~/memory-test
uv run memory init
uv run memory serve                 # MCP stdio 서버. Claude Desktop 연결은 SETUP.md
```
