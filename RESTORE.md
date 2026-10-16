# RESTORE.md — Restore procedure (SPEC 8.5)

_English translation of RESTORE_KO.md. If the two differ, the Korean original is authoritative._

This file goes into the snapshot along with `server/`. It is written assuming the MacBook has been lost.

## What you need

- One copy of the age private key (password manager or paper copy). If it is the paper copy, transcribe it onto one line without typos.
- Access to the B2 account (two-factor authentication device).
- A Mac with `uv`, `rclone`, `age` installed.

## Procedure

1. **Start in an empty directory.** Create a new folder with no personal data. Example: `mkdir -p ~/memory-restore && cd ~/memory-restore`
2. **Choose a snapshot from B2.** Look at the full version list under `snapshots/` in the B2 web console or with rclone. Hidden versions can also be downloaded by version ID.
   ```sh
   export RCLONE_CONFIG_B2_TYPE=b2 RCLONE_CONFIG_B2_ACCOUNT=<keyID> RCLONE_CONFIG_B2_KEY=<applicationKey>
   rclone lsjson --b2-versions b2:<bucket>/snapshots
   rclone copyto b2:<bucket>/snapshots/<name>.tar.gz.age ./snapshot.tar.gz.age
   ```
   Use the environment variables above only in this shell and do not write them to a file. `unset` them when done.
3. **Check the ciphertext hash.** Compare `shasum -a 256 snapshot.tar.gz.age` against the record in `runtime/backup/backup_state.json` (if any) or B2's sha1/size. If there is no record, compare only the size.
4. **Make the private key into a temporary file, then decrypt and extract.** The commands below reject absolute paths, parent-path traversal, and symbolic links, and extract only if the target directory is empty.
   ```sh
   umask 077
   printf '%s\n' 'AGE-SECRET-KEY-1...' > /tmp/age-identity.txt
   # Since there is no server code yet, first decrypt with age and extract server/, or use server code from another checkout:
   age -d -i /tmp/age-identity.txt -o snapshot.tar.gz snapshot.tar.gz.age
   mkdir server-bootstrap && tar -xzf snapshot.tar.gz -C server-bootstrap server
   cd server-bootstrap/server && uv sync && cd ../..
   uv run --directory server-bootstrap/server memory restore ./snapshot.tar.gz.age --into ~/memory-restore/home --identity /tmp/age-identity.txt --sha256 <hash>
   rm -P /tmp/age-identity.txt snapshot.tar.gz
   ```
   Result: `~/memory-restore/home/vault/` (including Git history) and `~/memory-restore/home/server/`.
5. **Put it in place and rebuild the DB.**
   ```sh
   export MEMORY_HOME=~/memory-restore/home
   cd $MEMORY_HOME/server && uv sync
   uv run memory doctor --no-index      # check .git, event logs, note state
   uv run memory rebuild                 # regenerate the DB from notes + used events (downloads the model)
   ```
6. **Confirm search with a test query and record the drill date.**
   ```sh
   uv run memory stats                   # check active count, index count
   uv run python scripts/measure_t08.py --existing   # whether recall runs
   uv run memory restore-drill-done
   ```
7. When switching to use this as the real vault, change `MEMORY_HOME` in the Claude Desktop config to this path (SETUP.md).

## Drill frequency

Once before real use, then once every quarter. Also try decrypting at least once with the paper-copy key.

## When it doesn't work

- `age: error: no identity matched any of the recipients` → the private key is different. Use another copy.
- `복원 실패: 대상 디렉터리가 비어 있지 않다` ("restore failed: target directory is not empty") → use a new empty folder. Do not overwrite on top of existing data.
- rclone 403 → check the key permissions (`readFiles`, `listFiles`). Versions within the lock period cannot be deleted even with delete permission.
