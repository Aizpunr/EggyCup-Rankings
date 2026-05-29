# Eggy Cup Rankings

Local-only ELO + Glicko-2 rankings for **Eggy Cup**, a casual weekly Zeepkist tournament hosted by **Wheelie**.

- Format: same elimination format as COTD (drop slowest per round)
- Schedule: Friday evening US / Saturday 01:30 EU (cadence still uncertain — sometimes biweekly, sometimes back-to-back nights)
- Mod log: `COTDTracker 0.11.1` (identical parser to COTD)
- Tracking: cups **87 onward**. Cups 1–86 are unrecoverable (no logs).

## Live view

This project has no GitHub Pages deployment. Serve locally:

```powershell
cd "C:\Users\rafa\Desktop\Claude\eggy cup"
python -m http.server 9004
# Open http://localhost:9004
```

## Per-cup workflow

Run in this order **after** each cup is played:

1. **Before relaunching Zeepkist**, archive the cup-tracker log (BepInEx overwrites it on launch):
   ```
   python C:\Users\rafa\Desktop\Claude\save_log.py eggy <N>
   ```
   Or copy `BepInEx\LogOutput.log` → `cup logs\eggy_<N>.log` manually.

2. **If LiveLeaderboardLogger was active** (`/livelog start` before cup): also copy `BepInEx\LiveLeaderboardLogger.log` → `cup logs\eggy_<N>_liveleaderboard.log` manually. `save_log.py` does not grab this one.

3. **Snapshot pre-cup state** so the next render shows week-over-week delta arrows:
   ```
   python snapshot.py
   ```

4. **Run the pipeline**:
   ```
   python new_cup.py <N> [mapper] [--exclude name1,name2]
   ```
   - Parses `cup logs\eggy_<N>.log` → appends to `Eggy Cup 87-NN.xlsx`
   - Writes `cup_<N>.json` backup
   - Runs `elo_engine.py`, `build_cups.py`, `build_fastest.py`
   - Runs `analyze_cup_livelog.py` if a livelog is present (LTG inference + `steam_ids.json` merge)
   - Mapper defaults to `TBD` if omitted

5. **Fill in map name + mapper** in `build_cups.py` → `map_index` dict, then rerun `python build_cups.py`.

### Wheelie exclusion

Per-cup decision. He raced in cup 88. When he hosts and does not compete, pass `--exclude wheelie`.

## File map

| File | Role |
|---|---|
| `new_cup.py` | Orchestrator. Reads `cup logs\eggy_<N>.log`; pass `--live` to pull from BepInEx instead. |
| `elo_engine.py` | Computes both rating variants. Reads the canonical xlsx, writes `elo_results.json` + `alldata.json`. |
| `snapshot.py` | Captures pre-cup state into `snapshot.json` for week-over-week delta arrows. |
| `build_cups.py` | Inverts `elo_results.json` into cup-centric `cups.json`. **`map_index` dict is the source of truth for map names + mappers.** |
| `build_fastest.py` | Builds `fastest.json` from per-cup logs. |
| `analyze_cup_livelog.py` | LTG inference (Left The Game mid-round) + `steam_ids.json` merge. Reads `cup logs\eggy_<N>_liveleaderboard.log`. |
| `index.html` | Frontend — dark theme, port 9004, ELO / Glicko-2 variant toggle, click player for inline chart. |

## Rating variants

Two ratings are computed:

- **ELO** (weighted): `K=32` divided across the lobby, with pairwise pct-multipliers favoring upsets near the top. Anchored at 1500. Inactivity decay after 3-cup grace.
- **Glicko-2**: percentile-based single-observation update (ported from ZSL). `R0=1500, RD0=350, vol0=0.06, τ=0.8, RD floor=80`. No ELO-style decay — RD captures uncertainty natively.

Glicko-2 swings harder than ELO with sparse data (3 cups → RDs still ~220–290). Will tighten over time.

## Eggy-specific quirks

### Winner-marker round

The cup-tracker log terminates with a synthetic round: a single-player leaderboard, that same player "eliminated by mod". The COTD parser treats this as a regular elimination and fails to identify the winner. `new_cup.py` detects the pattern (single-player leaderboard + single elimination = same name) and treats that player as the winner. See the `winner_marker` block.

### Tier scale

The ELO tier bands (Legend/Pro/Master/Gold/Silver/Bronze) and colors are inherited from COTD's higher-scale palette. With only 3 cups, most players sit in Silver — bands will spread out as cups accumulate.

## Bootstrap state (2026-05-23)

| Cup | Map | Mapper | Winner | Players | Fastest |
|---|---|---|---|---|---|
| Eggy 87 | pizza track | Wheelie | Minkus | 23 | MackCheesy 35.009 |
| Eggy 88 | TBD | void & zodiak | Stick (ZET) | 17 | microways 50.368 |
| Eggy 89 | TBD | vectortrajector | Lexer | 27 | Lexer 31.549 |

Top 5 weighted ELO after cup 89: Stick · Minkus · microways · Lexer · Shattersmith.

## Out of scope

- GitHub Pages public site (this project is unannounced)
- Cross-comp ELO integration
- SOF mod feed (Eggy pool too thin to drive general-lobby SOF)
- Mining cups 1–86 from VODs / Discord
- Auto-apply LTG corrections to xlsx (currently they patch only `cup_<N>.json`)
- Per-map mapper-exclusion semantics (Eggy may or may not have own-map rule)
- Big 3 / consistency / alt-ranking pages — revisit at 10+ cups

## Origin

The rating engine and CANONICAL alias dictionary are inherited from the COTD project. Eggy-specific seeds (maskeddog, Shinikage221, bjenk4, …) are added at the bottom of the `CANONICAL` dict in `elo_engine.py`.
