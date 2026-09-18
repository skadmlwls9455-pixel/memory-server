# memory-server

개인 AI 기억 시스템의 로컬 MCP 서버. 명세는 `SPEC.md`, 작업 규칙은 `CLAUDE.md`, 결정 기록은 `DECISIONS.md`에 있다.

## 개발

```sh
uv sync
uv run pytest
```

## 실행

모든 경로는 환경변수 `MEMORY_HOME` 아래에 만들어진다. 시험 중에는 실제 vault가 아닌 별도 디렉터리를 쓴다.

```sh
export MEMORY_HOME=~/memory-test
uv run memory init     # 폴더 구조와 vault Git 저장소 생성
uv run memory serve    # MCP stdio 서버 (아직 도구 없음)
```
