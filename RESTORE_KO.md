# RESTORE.md — 복원 절차 (SPEC 8.5)

이 파일은 스냅샷 안의 `server/` 에 같이 들어간다. 맥북을 잃어버린 상태를 가정하고 쓴다.

## 준비물

- age 개인키 사본 하나 (비밀번호 관리자 또는 종이 사본). 종이 사본이면 오타 없이 한 줄로 옮겨 적는다.
- B2 계정 접근 (2단계 인증 기기).
- `uv`, `rclone`, `age` 가 설치된 맥.

## 절차

1. **빈 디렉터리에서 시작한다.** 개인 데이터가 없는 새 폴더를 만든다. 예: `mkdir -p ~/memory-restore && cd ~/memory-restore`
2. **B2 에서 스냅샷을 고른다.** B2 웹 콘솔 또는 rclone 으로 `snapshots/` 아래 전체 버전 목록을 본다. 숨겨진 버전도 버전 ID 로 받을 수 있다.
   ```sh
   export RCLONE_CONFIG_B2_TYPE=b2 RCLONE_CONFIG_B2_ACCOUNT=<keyID> RCLONE_CONFIG_B2_KEY=<applicationKey>
   rclone lsjson --b2-versions b2:<버킷>/snapshots
   rclone copyto b2:<버킷>/snapshots/<이름>.tar.gz.age ./snapshot.tar.gz.age
   ```
   위 환경변수는 이 셸에서만 쓰고 파일에 적지 않는다. 끝나면 `unset` 한다.
3. **암호문 해시를 확인한다.** `shasum -a 256 snapshot.tar.gz.age` 를 `runtime/backup/backup_state.json` 의 기록(있다면)이나 B2 의 sha1/크기와 대조한다. 기록이 없으면 크기만 대조한다.
4. **개인키를 임시 파일로 만들고 복호화·해제한다.** 아래 명령은 절대 경로, 상위 경로 이동, 심볼릭 링크를 거부하고 대상 디렉터리가 비어 있어야만 푼다.
   ```sh
   umask 077
   printf '%s\n' 'AGE-SECRET-KEY-1...' > /tmp/age-identity.txt
   # 서버 코드가 아직 없으므로 먼저 age 로 풀어 server/ 를 꺼내거나, 다른 체크아웃의 서버 코드를 쓴다:
   age -d -i /tmp/age-identity.txt -o snapshot.tar.gz snapshot.tar.gz.age
   mkdir server-bootstrap && tar -xzf snapshot.tar.gz -C server-bootstrap server
   cd server-bootstrap/server && uv sync && cd ../..
   uv run --directory server-bootstrap/server memory restore ./snapshot.tar.gz.age --into ~/memory-restore/home --identity /tmp/age-identity.txt --sha256 <해시>
   rm -P /tmp/age-identity.txt snapshot.tar.gz
   ```
   결과: `~/memory-restore/home/vault/` (Git 이력 포함) 와 `~/memory-restore/home/server/`.
5. **제자리에 두고 DB 를 다시 만든다.**
   ```sh
   export MEMORY_HOME=~/memory-restore/home
   cd $MEMORY_HOME/server && uv sync
   uv run memory doctor --no-index      # .git, 이벤트 로그, 노트 상태 점검
   uv run memory rebuild                 # 노트 + used 이벤트로 DB 재생성 (모델을 받는다)
   ```
6. **시험 질의로 검색을 확인하고 리허설 날짜를 기록한다.**
   ```sh
   uv run memory stats                   # active 수, 색인 수 확인
   uv run python scripts/measure_t08.py --existing   # recall 이 도는지
   uv run memory restore-drill-done
   ```
7. 실사용 vault 로 바꿔 쓸 때는 Claude Desktop 설정의 `MEMORY_HOME` 을 이 경로로 바꾼다 (SETUP.md).

## 리허설 주기

실사용 전에 한 번, 이후 분기마다 한 번. 종이 사본 키로도 한 번은 복호화해 본다.

## 안 되는 경우

- `age: error: no identity matched any of the recipients` → 개인키가 다르다. 다른 사본을 쓴다.
- `복원 실패: 대상 디렉터리가 비어 있지 않다` → 새 빈 폴더를 쓴다. 기존 데이터 위에 덮어쓰지 않는다.
- rclone 403 → 키 권한(`readFiles`, `listFiles`) 확인. 잠금 기간 안의 버전은 삭제 권한이 있어도 지워지지 않는다.
