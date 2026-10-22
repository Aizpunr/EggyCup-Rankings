# eggy-submit Worker

Receives weekly Eggy Cup logs and hands them to `submissions_poll.py`. CI processes one cup per pass, pushes `cup-N`, and opens a PR. Merging publishes it. Local processing leaves publication manual. `--rehearsal` never pushes or opens PRs, including with `--ci`.

## Go live

1. In this folder run `npm install`, then `npx wrangler login` if needed.
2. Create namespaces, then paste the ids into `wrangler.toml` (keep binding `SUBMISSIONS`):
   ```powershell
   npx wrangler kv namespace create EGGY_SUBMISSIONS
   npx wrangler kv namespace create EGGY_SUBMISSIONS --preview
   ```
3. Reuse the existing Turnstile widget, with hostnames `aizpunr.github.io`, `localhost`, and `127.0.0.1`. Put its public site key on the page. Set its secret from Python, NOT by piping in PowerShell: PowerShell 5 pipes (`Get-Clipboard | npx wrangler secret put ...`, `$x | ...`) store a trailing line break, and a token stored that way makes every poller call return 401 (seen in the TyO deploy, 2026-10-22). With the secret on the clipboard:
   ```powershell
   python -c "import subprocess as s; v=s.run(['powershell','-NoProfile','-Command','Get-Clipboard'],capture_output=True,text=True).stdout.strip(); s.run('npx wrangler secret put TURNSTILE_SECRET',input=v,text=True,shell=True,check=True)"
   ```
4. Generate a NEW poller token (never reuse another comp's), store it in the Worker the same way, and leave it on the clipboard (not printed) for the GitHub repository secret `EGGY_POLLER_TOKEN`:
   ```powershell
   python -c "import secrets, subprocess as s; v=secrets.token_urlsafe(32); s.run('npx wrangler secret put POLLER_TOKEN',input=v,text=True,shell=True,check=True); s.run(['clip'],input=v,text=True,check=True)"
   ```
   Keep secrets out of source files and terminal transcripts. For the PC, use environment variables, or first ignore `submit_config.json` locally and create it at the repo root with keys `worker_url` and `poller_token`. Environment variables `EGGY_WORKER_URL` and `EGGY_POLLER_TOKEN` override these keys.
5. Run `npx wrangler deploy`. Check that `GET /` answers `{"ok":true,"service":"eggy-submit"}` (on Windows two `wrangler dev` on one port both print Ready, so a local test can hit another comp's Worker). Set repository secret `EGGY_WORKER_URL` to the deployed URL and configure the page with that public URL.
   Then confirm the token has no stray line break: `GET /pending` with `Authorization: Bearer <token>` must return 200, not 401.
6. GitHub Settings > Actions > General > Workflow permissions: select read and write, and allow GitHub Actions to create and approve pull requests.
7. Smoke test from an allowed origin with a real small tracker log, valid fields, today's date, and a fake Turnstile token. Expect HTTP 403 `turnstile_failed`, with code `invalid-input-response`. For example, replace only the public URL and log path:
   ```powershell
   curl.exe -s -H "Origin: http://localhost:9004" -F "file=@sample.log" -F "cup=110" -F "mapper=Example" -F "date=$((Get-Date).ToString('yyyy-MM-dd'))" -F 'ruling={"lobby":null,"outcome":"confirmed"}' -F "cf-turnstile-response=fake-token" https://eggy-submit.YOUR-SUBDOMAIN.workers.dev/submit
   ```

## Form contract

`POST /submit` accepts multipart fields `file`, `cup` (integer 87-999), `map` (optional, 0-80 characters), `mapper` (required, 1-40), `exclude` (JSON array, at most 20 names), `date` (YYYY-MM-DD, last 60 days through tomorrow), `submitter`, `preview` (JSON), `ruling` (JSON), and `cf-turnstile-response`. The file must contain `COTDTracker` and `Doing eliminations with leaderboard`, and be at most 5 MB. Names cannot start with `--` or contain line breaks or NUL; excluded names cannot contain commas.

`ruling` is required, at most 512 raw characters:

```json
{"lobby":null,"outcome":"confirmed","winner":null,"note":null,"detected":"single"}
```

- `lobby`: null or integer 1-9. Required when the log has multiple cup starts.
- `outcome`: `confirmed`, `third_wins`, `replay_winner`, `finish_by_times`, or `review`.
- `winner`: required string of 1-40 characters for `third_wins` and `replay_winner`; null or absent otherwise. Uses the name safety check and cannot contain commas.
- `note`: optional free text or null, maximum 200 characters, using the same safety check (for review).
- `detected`: optional string or null, maximum 20 characters; informational kind seen by the page.

The poller checks the selected lobby against `eggy_parser.detect_outcome` before running `new_cup.py`. Lobby becomes `--lobby K`; third/replay winner becomes `--winner NAME`; finish by times becomes `--finish-by-times`. Confirmed adds no override. Review is never processed.

## API and storage

Public endpoints: `GET /` (health, service `eggy-submit`), `POST /submit`, `GET /status` (last 50 records including ruling). Poller endpoints require `Authorization: Bearer <POLLER_TOKEN>`: `GET /pending`, `GET /log/<id>`, `POST /status/<id>` with status/note/summary, and `POST /reindex`.

Statuses: `received`, `review`, `processed`, `failed`, `published`, `duplicate`, `superseded`. A review ruling is stored directly as `review`; the poller can also set review for a mismatch. `/pending` returns only received and processed records.

KV binding `SUBMISSIONS` stores `log:<id>` (private bytes and checksum metadata), `sub:<id>` (authoritative record), `sha:<sha256>` (exact-file deduplication), and `index` (newest first, capped at 200). An identical file is still recorded: it gets its own `sub:<id>` record with status `duplicate`, `same_file_as` = the first id and a note (prefixed `REVIEW:` when it was sent under a different cup number); its bytes are not stored again. A different log for a cup that is already processed is marked `duplicate` by the poller, which parses it with that submitter's settings and compares winner, top 3 and player count with the published `cup_N.json` (`REVIEW:` prefix when they differ or the log is more complete). Every submission counts toward the daily cap of 20; Turnstile is verified first. The index uses read-modify-write and concurrent writes can lose an index entry, but authoritative records remain. Daily `/reindex` repairs it. The index cap and public status limit mean old raw logs may need manual recovery by submission id.

## Processing and recovery

The workflow tests first, fetches cross-comp SOF data (falls back to Eggy-local SOF on failure), and polls Friday 21:00-23:59, all Saturday, and Sunday 00:00-11:59 UTC. A push to main touching `cup_meta.json` marks merged cups published. While an earlier cup PR is open, later cups wait. Workflow dispatch accepts an id and dry-run flag.

State, downloaded logs, output, lock, and `PAUSE` live under `cup logs/submissions/`. Unposted statuses retry on the next pass. `--once`, `--loop SECONDS`, `--dry-run`, `--repo`, `--worker`, `--id`, `--no-git`, `--reindex`, `--rehearsal`, `--ci`, and `-v` are supported. Install `openpyxl certifi pytest` for Python. Dry-run downloads logs to inspect but does not run the cup pipeline or publish statuses.

PRs include the ruling, podium, warnings, and new canonical names with possible aliases. Raw logs are excluded from staging. Artifacts contain only `poll.log` and `*.out`. No livelog: no Steam ID check and no left-the-game pass. Raw log stays private in the Worker.

After merging, on the PC run `python catch_up.py` on main. It pulls with `--ff-only`, fetches missing raw logs, rebuilds `cups.json` using local cross-comp SOF, optionally refreshes the holistic repo, and prints manual push reminders. It never commits or pushes. `--no-pull` skips pulling; `--no-external` skips refresh.

For local Wrangler development, create `.dev.vars` from `.dev.vars.example` and enter your development values yourself; never commit it. `npm run dev` listens on port 8787. Rotate tokens by setting a new Worker `POLLER_TOKEN`, GitHub `EGGY_POLLER_TOKEN`, and PC config value together.

The existing root `.gitignore` does not currently ignore `submit_config.json`. This port was restricted to new files, so it does not change that file. Prefer environment credentials until you add an ignore rule yourself.
