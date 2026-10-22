# Eggy Cup Rankings

ELO and Glicko-2 rankings for **Eggy Cup**, a casual weekly Zeepkist tournament hosted by **Wheelie**.

- Format: elimination, same as COTD (the slowest drop each round).
- Schedule: Friday evening US, which is early Saturday in Spain. Cups are dated by that Saturday.
- Mod log: `COTDTracker 0.11.1`.
- Tracking: cups **87 onward**. Cups 1 to 86 have no logs.

Live site: https://aizpunr.github.io/EggyCup-Rankings/ (local preview: `python -m http.server 9004`, then http://localhost:9004).

## Submitting a cup

Anyone who played can send their game log on the [submit page](https://aizpunr.github.io/EggyCup-Rankings/submit.html). See [SUBMIT.md](SUBMIT.md) for the submitter's side.

How it flows:

1. `submit.html` reads `LogOutput.log` in the browser with `eggy_parser.js`, shows the result and asks the submitter one question about it (see "Abnormal endings" below). It posts the log, the cup details and the answer (`ruling`) to the `eggy-submit` Cloudflare Worker (`submit-worker/`).
2. The GitHub Action `.github/workflows/process-submission.yml` runs every 15 minutes from Friday night to Sunday midday. It calls `submissions_poll.py --ci`, which checks the ruling against the log, runs `new_cup.py` and opens a pull request `cup-N` with the result and a "Names to check" list.
3. Merging the PR publishes the cup. The next Action run marks the submission `published`.
4. On the PC, `python catch_up.py` pulls, fetches the raw log from the Worker, recomputes cup strength with the local cross-comp data and runs the cross-comp refresh.

Raw logs can contain chat, so they stay private (in the Worker and in the local, ignored `cup logs/`). Only the COTDTracker lines are published, in `tracker_logs/`.

## Processing a cup by hand

```
python new_cup.py <N> "<RAW mapper name>" --map "Map Name" [--exclude "a,b"] [--date YYYY-MM-DD] [--log PATH | --live]
```

- The mapper is the **raw** log name, clan tag included (`"[ZET] Wheelie"`), so the exclusion matches. If the mapper was not in the lobby, type any name; nothing is excluded.
- `--exclude` takes raw names of people who were in the lobby but did not race.
- Log source: `--log PATH`, or `--live` (BepInEx), or the archived `cup logs/eggy_<N>.log`.
- The script runs `snapshot.py N-1`, parses the log, appends the xlsx block, writes `cup_<N>.json`, `cup_meta.json` and `tracker_logs/eggy_<N>.log`, then runs `elo_engine.py`, `build_cups.py`, `build_fastest.py` (and `analyze_cup_livelog.py` when a livelog exists). It ends with `Eggy N COMPLETE`.
- It refuses a cup that is already processed. `--reprocess` undoes the newest cup first (the xlsx is backed up to `backups/`).
- After a cup: `python "C:\Users\rafa\Desktop\Claude\zeepkist holistic\refresh.py"` for the cross-comp ranking.

### Abnormal endings

`eggy_parser.detect_outcome()` classifies how a log ends. The submit page turns each case into a question, and the answer maps to a flag:

| Log shows | Flag |
|---|---|
| Both finalists DNF the last round, 3rd place wins (house rule) | `--winner "<3rd place>"` |
| Both finalists DNF, the final was replayed off-tracker | `--winner "<replay winner>"` |
| Cup stopped with several players still in (host disconnect) | `--finish-by-times` |
| Several cup starts in one log (false start or redo) | `--lobby K` |
| Roulette night (every round a different map) | `--no-fastest`, processed by hand |

## Files

| File | Role |
|---|---|
| `new_cup.py` | Orchestrator, see above. |
| `eggy_parser.py` | Pure log parser and outcome classifier. `eggy_parser.js` is its browser port; any rule change goes in both. |
| `eggy_files.py` | Finds the one `Eggy Cup A-B.xlsx` workbook. |
| `elo_engine.py` | Computes ELO and Glicko-2 from the xlsx; writes `elo_results.json` and `alldata.json`. Holds the `CANONICAL` alias dictionary. |
| `snapshot.py` | Pre-cup `snapshot.json` for the week-over-week arrows. |
| `build_cups.py` | `cups.json` with cup strength. Map, mapper and date come from `cup_meta.json`; the dictionaries for cups 87 to 109 are frozen history. |
| `build_fastest.py` | `fastest.json`. |
| `analyze_cup_livelog.py` | Left-the-game inference and `steam_ids.json` merge when a LiveLeaderboardLogger log exists. |
| `submit.html`, `submit-worker/`, `submissions_poll.py`, `catch_up.py` | The submit flow. |
| `tests/` | `python -m pytest tests -q`. Every archived cup must replay byte for byte, and the JS parser must equal the Python one. |

## Rating variants

- **ELO** (weighted): `K=32` divided across the lobby, with pairwise multipliers favoring upsets near the top. Anchored at 1500. Inactivity decay after a 3-cup grace period.
- **Glicko-2**: percentile-based single-observation update (ported from ZSL). `R0=1500, RD0=350, vol0=0.06, tau=0.8, RD floor=80`.

## Eggy-specific rules

- **Winner-marker round**: the tracker log can end with a one-player leaderboard where that player is "eliminated" by the mod. That player is the winner, and their time comes from the last round they actually raced.
- **Mappers** who ride along in their own cup are excluded. A host who races counts.
- **Exclusions match raw names exactly** (`[ZET] Wheelie`, with the space). Aliases are resolved later in `elo_engine.py`.

## Out of scope

- Mining cups 1 to 86 from VODs or Discord.
- Applying LTG corrections to the xlsx automatically (they patch only `cup_<N>.json`).

## Origin

The rating engine and the `CANONICAL` alias dictionary come from the COTD project. Eggy-specific aliases are at the bottom of `CANONICAL` in `elo_engine.py`.
