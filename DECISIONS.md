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

## 1단계 1-2 remember, read_memory

### D-012 도구 오류는 `{error: {code, message, retryable, ...}}` 딕셔너리로 돌려준다
- 결정: MCP의 `isError` 문자열 대신 구조화된 결과값으로 오류를 낸다. 추가 정보(필드명, 충돌한 노트의 ID와 전문)는 같은 객체에 싣는다.
- 이유: SPEC 4장의 `FACT_CONFLICT`는 기존 노트 전문을 오류에 실어야 한다. 문자열 오류보다 구조화된 값이 모델의 한 번 재시도에 맞다.

### D-013 10분 중복 방지는 `created` 이벤트의 `input_hash`로 판단한다
- 결정: 정규화한 입력(제목, 본문, 태그, type, source, fact_key, links, supersedes)의 SHA-256을 `created` 이벤트 data에 넣고, 최근 10분의 이벤트에서 같은 해시를 찾으면 그 ID를 돌려준다. 별도 상태 파일이나 메모리 캐시는 두지 않는다.
- 이유: 재시작해도 유지되고, 이벤트 로그가 이미 원본이라 새 저장 위치가 늘지 않는다. 해시만 넣으므로 이벤트에 본문이 들어가지 않는다.

### D-014 T01의 "같은 초에 두 번"은 입력이 다른 두 호출로 본다
- 결정: 같은 초라도 입력이 다르면 ID가 다른 노트 둘을 만든다. 입력까지 같으면 10분 규칙이 우선해서 기존 ID를 돌려준다.
- 이유: 두 규칙이 겹치는 경우는 "같은 입력"뿐이고, 그때는 SPEC 5장의 중복 방지가 의도한 상황이다.

### D-015 본문에 예약 제목 줄과 `---` 시작을 금지한다
- 결정: 본문 안에 `## 관련`이나 `## 덧붙임`과 정확히 같은 줄이 있거나 본문이 `---`로 시작하면 `INVALID_INPUT`이다. `## 관련 사항`처럼 다른 줄은 허용한다.
- 이유: 두 제목은 파서가 구획을 나누는 표식이다. 본문에 섞이면 `amend`가 덧붙일 위치와 `read_memory`의 본문 경계가 흔들린다.

### D-016 `## 덧붙임`은 생성 때 만들지 않고 첫 `amend` 때 파일 끝에 덧붙인다
- 결정: 새 노트에는 `## 덧붙임`을 넣지 않는다. 1-3의 `amend`가 없는 경우 제목을 포함해 파일 끝에 덧붙이고, 있으면 한 줄만 더한다.
- 이유: 둘 다 파일 끝에 덧붙이는 작업이라 본문을 다시 쓰지 않는다. 빈 제목이 모든 노트에 들어가는 것보다 낫다.

### D-017 `reflection`은 `source=inference`여야 한다 (양방향)
- 결정: `source=inference`이면 `type=reflection`이어야 하고, `type=reflection`이면 `source=inference`여야 한다.
- 이유: SPEC 4장 표의 "reflection: `source=inference`일 때만"을 그대로 적용했다. 사용자가 말한 내용은 episode나 fact다.

### D-018 `links` 대상은 존재하는 노트여야 하고 `## 관련`에 `- 관련: [[<제목> <id>|<제목>]]`로 쓴다
- 결정: 없는 ID를 links에 넣으면 `NOT_FOUND`다. 위키링크 표시는 대상 노트의 현재 제목을 읽어서 만든다.
- 이유: Obsidian 그래프에서 깨진 링크가 생기지 않게 한다. 제목은 생성 뒤 바뀌지 않으므로 표시가 어긋날 일이 없다.

### D-019 vault 저장소에 `core.quotePath=false`를 설정한다
- 결정: init이 `core.quotePath`를 끈다.
- 이유: 한글 파일명이 `git status`나 `ls-files`에서 8진수로 깨져 보이면 doctor의 경로 비교와 사람의 확인이 모두 불편하다. 동작에는 영향이 없다.

## 1단계 1-3 정정, 보충, 보관

### D-020 기존 노트 변경은 `mutations.py` 한 곳에서만 하고 결과를 바이트 단위로 검증한다
- 결정: `append_amendment`는 결과가 `기존 내용 + 덧붙인 부분`인지, `set_status`는 frontmatter의 `status`·`superseded_by` 줄 외에 한 바이트도 다르지 않은지 확인한 뒤에만 임시 파일과 rename으로 적용한다. 어긋나면 `ForbiddenChange`를 내고 쓰지 않는다.
- 이유: SPEC 3.3의 "허용된 변경 세 가지"를 리뷰가 아니라 코드가 보장하게 한다. `store.py`에는 기존 파일에 쓰는 경로가 없다.

### D-021 이미 대체된 노트는 다시 대체할 수 없다 (`INVALID_SUPERSEDES`)
- 결정: `supersedes` 대상의 상태가 `superseded`면 거부하고 `superseded_by`를 알려 준다. `archived`인 노트는 대체할 수 있다.
- 이유: 대체 사슬이 갈라지면 "현재 유효한 사실"이 둘이 된다. 보관은 회상 제외일 뿐이라 정정 대상이 될 수 있다.

### D-022 보관된 fact의 `fact_key`는 새 active fact가 쓸 수 있고, 그 뒤 `unarchive`는 `FACT_CONFLICT`다
- 결정: 유일성은 active 노트끼리만 본다. 보관된 노트를 되살릴 때 같은 키의 active 노트가 있으면 그 노트의 ID와 전문을 실어 거부한다.
- 이유: SPEC 4장 "같은 키의 active 노트는 하나뿐"과 6장 "`unarchive`는 fact_key 충돌은 거부"를 그대로 따랐다.

### D-023 정정은 `created` 이벤트 하나로 기록한다
- 결정: 새 노트의 `created` 이벤트 data에 `supersedes`와 `reason`을 넣는다. 옛 노트의 상태 변경에 별도 이벤트를 남기지 않는다.
- 이유: SPEC 3.4의 아홉 가지 이벤트에 `superseded`가 없고, 예시가 `created`에 `supersedes`를 싣는다. 새 노트, 옛 노트, 이벤트는 한 잠금 안에서 바뀌고 한 커밋에 들어간다.

### D-024 대체된 노트의 `archive`는 `NOT_ACTIVE`로 거부한다
- 결정: `archive`는 active 노트만 받는다. 이미 보관이면 `no_change`, 대체됐으면 거부.
- 이유: 대체된 노트는 이미 기본 회상에서 빠져 있다. 상태를 `archived`로 바꾸면 `superseded_by` 정보와 상태가 어긋난다.

### D-025 `archive`와 `unarchive`는 `reason`이 필수다
- 결정: 비어 있으면 `INVALID_INPUT`. 이유는 이벤트 data에 남기고 노트에는 쓰지 않는다.
- 이유: SPEC 6장이 두 도구 입력에 `reason`을 둔다. 노트 본문을 건드리지 않으면서 감사 기록은 이벤트 로그가 맡는다.

### D-026 `amend`도 10분 중복 방지를 적용한다
- 결정: 같은 노트에 같은 정규화 문장이 10분 안에 다시 오면 줄을 더하지 않고 `DUPLICATE_INPUT` 경고와 함께 같은 ID를 돌려준다. `amended` 이벤트의 `input_hash`로 판단하고 본문은 이벤트에 넣지 않는다.
- 이유: SPEC 5장의 중복 방지는 "같은 도구에 같은 정규화 입력"이라 `remember`에 한정되지 않는다.
