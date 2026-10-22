"""
new_cup.py — Process an Eggy Cup from COTDTracker mod logs.

Usage:
  python new_cup.py 110 "[ZET] Wheelie" --map "Map Name"
  python new_cup.py 110 "[ZET] Wheelie" --exclude "aizpun,[ZET]Sword125"
  python new_cup.py 110 Minkus --log "C:/Users/rafa/Downloads/LogOutput.log" --date 2026-10-24
  python new_cup.py 110 --reprocess ...     # undo the newest cup's block, then process again

The mapper is the RAW log name (tag and all) so the exclusion matches; the
display name (tag stripped) is what cup_N.json and cup_meta.json store. A
mapper who was not in the lobby can be typed freely, nothing is excluded then.

Log source, in order: --log PATH, --live (BepInEx LogOutput.log), else the
archived `cup logs/eggy_<N>.log`. The source is copied to `cup logs/` and its
COTDTracker lines to `tracker_logs/eggy_<N>.log` (that one is tracked in git).

Abnormal endings (see eggy_parser.detect_outcome):
  --winner RAW         both finalists DNF -> "3rd wins" rule, or a final replayed
                       off-tracker; that player is placed 1st
  --finish-by-times    cup cut short with survivors left: rank them by last-round time
  --lobby K            the log holds K+ cup starts (redo / false start): use the Kth
  --no-fastest         roulette-style nights: no fastest-lap annotation

Gate: refuses a cup that is already in the xlsx or cup_meta.json unless
--reprocess, which only works on the newest block (xlsx backed up to backups/).

Steps: snapshot.py N-1 -> parse -> xlsx block -> cup_N.json -> cup_meta.json ->
elo_engine -> build_cups -> build_fastest -> analyze_cup_livelog (if a livelog).
Prints "Eggy N COMPLETE" on success; the submissions poller looks for that line.
"""
import argparse, datetime, json, os, re, shutil, subprocess, sys
import openpyxl

import eggy_parser as P
from eggy_files import xlsx_path, xlsx_range

sys.stdout.reconfigure(encoding='utf-8')  # players use exotic unicode names

_dir = os.path.dirname(os.path.abspath(__file__))
_p = lambda f: os.path.join(_dir, f)

LIVE_LOG_PATH = r"C:\Program Files (x86)\Steam\steamapps\common\Zeepkist\BepInEx\LogOutput.log"
LIVE_LIVELOG_PATH = r"C:\Program Files (x86)\Steam\steamapps\common\Zeepkist\BepInEx\LiveLeaderboardLogger.log"
COLS_PER_CUP = 6
META_PATH = _p('cup_meta.json')


def fail(msg):
    print(f"ERROR: {msg}")
    sys.exit(1)


def display_name(raw):
    """'[ZET] Wheelie' -> 'Wheelie'."""
    return re.sub(r'^\[[^\]]*\]\s*', '', raw).strip() or raw


def load_meta():
    if not os.path.exists(META_PATH):
        return {}
    with open(META_PATH, encoding='utf-8') as f:
        return json.load(f)


def save_meta(meta):
    def key(cid):
        m = re.search(r'\d+', cid)
        return int(m.group()) if m else 0
    ordered = {k: meta[k] for k in sorted(meta, key=key)}
    with open(META_PATH, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(ordered, f, ensure_ascii=False, indent=1)
        f.write('\n')


def block_columns(ws):
    """[(col_start, cup_title)] for every block in the sheet, left to right."""
    rows = list(ws.iter_rows(min_row=1, max_row=5, values_only=True))
    out = []
    if len(rows) >= 5:
        for ci, val in enumerate(rows[4]):
            if val == 'Position':
                out.append((ci + 1, rows[1][ci] if len(rows) > 1 else None))
    return out


def run(script, label, *args):
    path = _p(script)
    if not os.path.exists(path):
        print(f"  SKIP: {script} not found")
        return True
    print("=" * 50)
    print(f"Running {script}...")
    print("=" * 50)
    r = subprocess.run([sys.executable, path, *args], cwd=_dir)
    if r.returncode != 0:
        print(f"  WARNING: {label} failed!")
        return False
    return True


# ── Parse arguments ──
ap = argparse.ArgumentParser(allow_abbrev=False, description='Process an Eggy Cup log.')
ap.add_argument('cup', type=int)
ap.add_argument('mapper', nargs='?', default='TBD', help='RAW log name of the mapper')
ap.add_argument('--exclude', default='', help='comma-separated RAW names who did not compete')
ap.add_argument('--winner', help='RAW name placed 1st (abnormal finals)')
ap.add_argument('--finish-by-times', action='store_true')
ap.add_argument('--no-fastest', action='store_true')
ap.add_argument('--lobby', type=int, help='use the Kth cup start in the log (1-based)')
ap.add_argument('--map', default='', help='map name for cup_meta.json (blank = TBD)')
ap.add_argument('--date', help='YYYY-MM-DD; default = the log file date')
ap.add_argument('--log', help='parse this log instead of the archived one')
ap.add_argument('--live', action='store_true', help='grab LogOutput.log from BepInEx')
ap.add_argument('--reprocess', action='store_true', help='undo the newest block first')
args = ap.parse_args()

cup_num = args.cup
cup_id = f"Eggy {cup_num}"
mapper = args.mapper
extra_excluded = [n.strip() for n in args.exclude.split(',') if n.strip()]
excluded = ({mapper} if mapper and mapper != "TBD" else set()) | set(extra_excluded)
if args.date:
    try:
        datetime.date.fromisoformat(args.date)
    except ValueError:
        fail(f"--date must be YYYY-MM-DD, got {args.date!r}")

print(f"Processing {cup_id}, mapper: {mapper}")
if extra_excluded:
    print(f"Also excluding: {', '.join(extra_excluded)}")
print()

# ── 0. Idempotency gate / reprocess ──
xlsx_file = xlsx_path()
meta = load_meta()
wb = openpyxl.load_workbook(xlsx_file)
ws = wb[wb.sheetnames[0]]
blocks = block_columns(ws)
in_xlsx = [c for c, title in blocks if title == cup_id]
if (in_xlsx or cup_id in meta) and not args.reprocess:
    fail(f"{cup_id} is already processed (found in "
         f"{'the xlsx' if in_xlsx else ''}{' + ' if in_xlsx and cup_id in meta else ''}"
         f"{'cup_meta.json' if cup_id in meta else ''}). Pass --reprocess to redo the newest cup.")
if args.reprocess:
    if in_xlsx:
        if blocks[-1][1] != cup_id:
            fail(f"--reprocess only undoes the newest block; the newest is {blocks[-1][1]!r}")
        os.makedirs(_p('backups'), exist_ok=True)
        bak = _p(os.path.join('backups', f"{os.path.basename(xlsx_file)}.pre_reprocess_{cup_num}"))
        shutil.copy2(xlsx_file, bak)
        ws.delete_cols(in_xlsx[0], 4)
        wb.save(xlsx_file)
        print(f"Reprocess: removed {cup_id} block at column {in_xlsx[0]} (backup: {bak})")
    if cup_id in meta:
        del meta[cup_id]
        save_meta(meta)
        print(f"Reprocess: dropped {cup_id} from cup_meta.json")
    print()

# ── 1. Pre-cup snapshot (explicit target = idempotent) ──
if cup_num > 1:
    if not run('snapshot.py', 'snapshot', str(cup_num - 1)):
        fail("snapshot.py failed")
    print()

# ── 2. Locate / archive log ──
log_dir = _p('cup logs')
os.makedirs(log_dir, exist_ok=True)
log_backup = os.path.join(log_dir, f'eggy_{cup_num}.log')
live_log_backup = os.path.join(log_dir, f'eggy_{cup_num}_liveleaderboard.log')


def archive(src, dst):
    if os.path.exists(dst) and os.path.samefile(src, dst):
        return
    shutil.copy2(src, dst)


if args.log:
    if not os.path.exists(args.log):
        fail(f"{args.log} not found")
    archive(args.log, log_backup)
    print(f"Log {args.log} archived to {log_backup}")
    live_log_backup = None
elif args.live:
    shutil.copy2(LIVE_LOG_PATH, log_backup)
    print(f"Live BepInEx log archived to {log_backup}")
    if os.path.exists(LIVE_LIVELOG_PATH):
        shutil.copy2(LIVE_LIVELOG_PATH, live_log_backup)
        print(f"Live livelog archived to {live_log_backup}")
    else:
        live_log_backup = None
else:
    if not os.path.exists(log_backup):
        fail(f"{log_backup} not found. Pass --log PATH or --live, or save_log first.")
    print(f"Reading archived log: {log_backup}")
    if not os.path.exists(live_log_backup):
        live_log_backup = None

with open(log_backup, encoding='utf-8', errors='replace') as f:
    tracker_lines = P.filter_tracker_lines(f.readlines())
if not tracker_lines:
    fail("No COTDTracker lines found in log file.")

lobbies = P.split_lobbies(tracker_lines)
if args.lobby:
    if not 1 <= args.lobby <= len(lobbies):
        fail(f"--lobby {args.lobby}: the log has {len(lobbies)} cup start(s) with rounds")
    lines = lobbies[args.lobby - 1]
    print(f"Using cup start {args.lobby} of {len(lobbies)}")
elif len(lobbies) > 1:
    fail(f"the log has {len(lobbies)} cup starts with rounds (redo or false start). "
         f"Pass --lobby K to pick the official one.")
else:
    lines = tracker_lines

# ── 3. Parse ──
named = set(P.named_players(P.split_rounds(lines)))
for n in extra_excluded:
    if n not in named:
        print(f"WARNING: excluded name {n!r} is not on any leaderboard in this log")
try:
    parsed = P.parse_cup_log(lines, excluded, manual_winner=args.winner,
                             finish_by_times=args.finish_by_times)
except P.ParseError as e:
    fail(str(e))
if parsed.survivors:
    print(f"Cut-short cup: ranking {len(parsed.survivors)} survivors by last-round times "
          f"-> {', '.join(parsed.survivors)}")
leaderboard = parsed.leaderboard

print(f"Parsed {len(leaderboard)} players (excluded: {', '.join(sorted(excluded)) or 'none'})")
print(f"Winner: {parsed.winner} ({parsed.winner_time})")
print(f"Rounds in log: {parsed.n_rounds}")
print()

# Tracked copy of the tracker lines only (the full log can hold chat).
os.makedirs(_p('tracker_logs'), exist_ok=True)
with open(_p(os.path.join('tracker_logs', f'eggy_{cup_num}.log')), 'w',
          encoding='utf-8', newline='\n') as f:
    f.writelines(l if l.endswith('\n') else l + '\n' for l in tracker_lines)

# ── 4. xlsx block ──
print(f"Current xlsx: {os.path.basename(xlsx_file)}")
blocks = block_columns(ws)
col_start = 1 if not blocks else blocks[-1][0] + COLS_PER_CUP
print(f"New cup at column {col_start}")

ws.cell(row=2, column=col_start, value=cup_id)
ws.cell(row=3, column=col_start, value=f'Map: {cup_id} by {display_name(mapper)}')
if parsed.fastest_time is not None and not args.no_fastest:
    ws.cell(row=4, column=col_start + 2,
            value=f'Fastest Time: {parsed.fastest_time:.3f} by {parsed.fastest_name} '
                  f'in Round {parsed.fastest_round}')
ws.cell(row=5, column=col_start, value='Position')
ws.cell(row=5, column=col_start + 1, value='Name')
ws.cell(row=5, column=col_start + 2, value='Elim Time')
ws.cell(row=5, column=col_start + 3, value='Elim Round')

for i, (name, time, rnd, position) in enumerate(leaderboard):
    row = 6 + i
    ws.cell(row=row, column=col_start, value=position)
    ws.cell(row=row, column=col_start + 1, value=name)
    if time == 'DNF' or time is None:
        ws.cell(row=row, column=col_start + 2, value='DNF')
    else:
        ws.cell(row=row, column=col_start + 2, value=round(float(time.replace(',', '.')) * 1000))
    if rnd is not None:
        ws.cell(row=row, column=col_start + 3, value=rnd)

# Grow the range in the filename by renaming (never deleting) the workbook.
start_num, end_num = xlsx_range(xlsx_file)
if cup_num > end_num:
    new_file = _p(f'Eggy Cup {start_num}-{cup_num}.xlsx')
    os.replace(xlsx_file, new_file)
    print(f"Renamed {os.path.basename(xlsx_file)} -> {os.path.basename(new_file)}")
    xlsx_file = new_file
wb.save(xlsx_file)
print(f"Saved to {os.path.basename(xlsx_file)}")

# ── 5. JSON backup + cup_meta ──
with open(_p(f'cup_{cup_num}.json'), 'w', encoding='utf-8') as f:
    json.dump(P.cup_json_payload(parsed, cup_num, display_name(mapper)), f,
              ensure_ascii=False, indent=2)
print(f"JSON backup: cup_{cup_num}.json")

date = args.date or datetime.date.fromtimestamp(os.path.getmtime(log_backup)).isoformat()
entry = {
    'map': args.map,
    'mapper': '' if mapper == 'TBD' else display_name(mapper),
    'date': date,
    'excluded': sorted(excluded),
}
if args.winner:
    entry['winner'] = args.winner
if args.finish_by_times:
    entry['finish_by_times'] = True
if args.lobby:
    entry['lobby'] = args.lobby
if args.no_fastest:
    entry['no_fastest'] = True
meta = load_meta()
meta[cup_id] = entry
save_meta(meta)
print(f"cup_meta.json: {cup_id} = {entry['map'] or 'TBD'} by {entry['mapper'] or 'TBD'}, {date}")
print()

# ── 6. Downstream scripts ──
for script, label in [('elo_engine.py', 'elo engine'), ('build_cups.py', 'cup data'),
                      ('build_fastest.py', 'fastest times')]:
    if not run(script, label):
        fail(f"{script} failed")
    print()

# ── 7. LTG inference if livelog exists ──
if live_log_backup and os.path.exists(live_log_backup):
    print("=" * 50)
    print("Running analyze_cup_livelog.py (LTG inference)...")
    print("=" * 50)
    r = subprocess.run([sys.executable, _p('analyze_cup_livelog.py'), str(cup_num)], cwd=_dir)
    if r.returncode != 0:
        print("  WARNING: analyze_cup_livelog.py failed")
else:
    print(f"(no livelog for Eggy {cup_num} — skipping LTG inference)")
print()

# ── 8. Summary ──
print("=" * 50)
print(f"{cup_id} COMPLETE")
print(f"  Players: {len(leaderboard)}")
print(f"  Winner: {parsed.winner}")
if parsed.fastest_time and not args.no_fastest:
    print(f"  Fastest: {parsed.fastest_time:.3f} by {parsed.fastest_name}")
elif args.no_fastest:
    print("  Fastest: skipped (--no-fastest)")
print(f"  Columns: {col_start}-{col_start+3}")
print("=" * 50)
