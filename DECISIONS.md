# DECISIONS.md

SPEC.md에 없거나 두 갈래로 읽히는 부분을 정할 때 여기에 결정과 이유를 적는다.
판단 순서는 원본 보존, 현재 사실의 정확성, 복원 가능성이다.

## 1단계 1-1 뼈대

### D-001 mcp 2.x의 `MCPServer`를 쓴다
- 결정: 공식 `mcp` SDK 2.x를 쓰고 `mcp.server.mcpserver.MCPServer`로 서버를 만든다.
- 이유: SPEC 2장이 말하는 `FastMCP`는 2.x에서 `MCPServer`로 이름만 바뀌었다. 새 프로젝트에서 1.x에 고정하면 보안 수정과 Claude Desktop 호환을 놓친다. 같은 SDK의 같은 클래스라 SPEC의 뜻(공식 SDK, stdio)은 그대로다.

### D-002 `MEMORY_HOME`은 필수이고 기본값이 없다
- 결정: 환경변수가 없거나 상대 경로면 시작을 거부한다. `~/memory`로 대체하지 않는다.
- 이유: CLAUDE.md 규칙. 테스트나 시험용 홈(`~/memory-test`)과 실제 vault가 섞이는 사고를 코드 수준에서 막는다.

### D-003 저장소 루트가 SPEC 3.1의 `server/`다
- 결정: 이 Git 저장소 자체가 `~/memory/server/`에 놓이는 코드 디렉터리다. 패키지 이름은 `memory_server`, CLI 이름은 `memory`.
- 이유: SPEC 2장에서 서버 코드는 별도 Git 저장소다. `memory`라는 패키지 이름은 너무 일반적이라 충돌 여지가 있어 `memory_server`로 했다. CLI는 SPEC이 부르는 대로 `memory`다.

### D-004 ID의 시각은 로컬 시간대다
- 결정: `YYYYMMDD-HHMMSS`는 `datetime.now().astimezone()` 기준 로컬 시각이다. 이벤트 `ts`와 `created`도 오프셋이 붙은 로컬 ISO 8601이다.
- 이유: SPEC 3.3 예시가 `+09:00`이다. ID와 `created`가 같은 시계를 쓰면 파일명만 보고 만든 시각을 알 수 있다. 시간대가 바뀌어도 ID 뒤 4자리 무작위와 존재 검사로 충돌은 막힌다.

### D-005 제목 정리 뒤 비면 `무제`로 저장한다
- 결정: 금지 문자와 공백을 빼고 60자로 자른 뒤 남는 게 없으면 파일명 제목을 `무제`로 한다. 개행과 탭은 공백 하나로 바꾼다.
- 이유: 파일명이 ` <id>.md`처럼 공백으로 시작하지 않게 한다. 노트 안의 `# 제목`은 원문 그대로 두므로 원본은 보존된다.

### D-006 `memory init`은 이번 실행이 만든 파일만 커밋한다
- 결정: init은 없는 폴더와 파일만 만들고, 그것만 `git add` 후 커밋한다. 이미 있는 파일은 읽지도 고치지도 않는다.
- 이유: 두 번 실행해도 안전해야 한다. 사용자가 고친 `config.toml`을 init이 몰래 커밋하면 doctor의 "커밋되지 않은 변경" 처리와 역할이 겹친다.

### D-007 vault 저장소의 커밋 작성자를 고정한다
- 결정: vault 저장소에 `user.name`/`user.email`이 없으면 `memory-server` / `memory-server@localhost`를 로컬 설정으로 넣는다.
- 이유: 개인 기억 저장소는 원격이 없고 한 사람만 쓴다. 전역 Git 설정이 없는 맥에서 첫 저장이 커밋 실패로 끝나는 일을 막는다. 이미 설정이 있으면 건드리지 않는다.

### D-008 init은 `core.json`을 만들고 `core.md`는 만들지 않는다
- 결정: `core.json`은 `{"entries": []}`로 만든다. `core.md`는 2단계 `update_core`가 `core.json`에서 생성한다.
- 이유: SPEC 3.1이 `core.json`을 원본, `core.md`를 생성물로 둔다. 생성 로직 없이 빈 `core.md`를 두면 원본과 어긋난 파일이 생긴다.

### D-009 `.lock` 잠금은 기본 30초 대기 후 실패한다
- 결정: `write_lock`은 `flock(LOCK_EX|LOCK_NB)`를 폴링하며 기본 30초까지 기다리고, 넘으면 `LockTimeout`을 낸다.
- 이유: 백업 CLI가 staging 복사 중 잠금을 잡을 수 있다. 무한 대기는 MCP 도구 호출을 멈추게 하므로 상한을 둔다. 값은 호출자가 바꿀 수 있다.

### D-010 `tag_rules.yaml`은 분류별 허용 이름 목록이다
- 결정: 다섯 분류(`주제`, `사람`, `프로젝트`, `상태`, `시기`)가 모두 있어야 하고, `values`가 비면 이름은 자유, 채우면 그 목록만 허용한다.
- 이유: SPEC 4장은 "tag_rules.yaml로 검증한다"고만 한다. 처음에는 분류만 강제하고, 쓰다가 이름이 흩어지면 목록을 채워 조이는 쪽이 저장 누락을 덜 낸다 (설계 원칙 7).

### D-011 CLI 안내 출력도 stderr로 보낸다
- 결정: `memory init` 같은 CLI의 사람용 메시지도 stdout이 아니라 stderr로 낸다.
- 이유: 같은 실행 파일이 `memory serve`로 MCP 서버가 된다. 어느 하위 명령이든 stdout에 아무것도 쓰지 않는 습관을 유지한다.
