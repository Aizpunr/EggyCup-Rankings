"""
snapshot.py — write snapshot.json from alldata.json.

Run BEFORE adding a new cup so the index page shows week-over-week delta arrows.

Usage:
  python snapshot.py          → snapshot at current cup state
  python snapshot.py 88       → snapshot as of cup 88
"""
import json, os, sys, shutil

base = os.path.dirname(os.path.abspath(__file__))
def _p(f): return os.path.join(base, f)

DECAY = 0.995
GRACE = 3

with open(_p('alldata.json')) as f:
    data = json.load(f)

g_players = data['glicko']
w_players = data['weighted']


def max_cup(players):
    if not players: return 0
    return max(h['c'] for p in players for h in p['h'])


current_cup = max(max_cup(g_players), max_cup(w_players))

target_cup = int(sys.argv[1]) if len(sys.argv) > 1 else current_cup
print(f"Current cup: {current_cup}  |  Snapshot at cup: {target_cup}")


def build_snap_at(players, target):
    """Qualified = any cup played up to target. Early Eggy data is sparse; we
    show everyone who has played until we have enough cups to filter (~10+)."""
    entries = []
    for p in players:
        name = p.get('n') or p.get('name')
        hist = p.get('h') or p.get('history', [])
        hist_before = [h for h in hist if h['c'] <= target]
        if not hist_before:
            continue
        last = max(hist_before, key=lambda h: h['c'])
        raw = last['r']
        missed = target - last['c']
        if missed > GRACE:
            active = round(1500 + (raw - 1500) * (DECAY ** (missed - GRACE)), 1)
        else:
            active = round(raw, 1)
        wins = sum(1 for h in hist_before if h['p'] == 1)
        pods = sum(1 for h in hist_before if h['p'] <= 3)
        entries.append((name, raw, active, wins, pods))
    entries.sort(key=lambda x: x[2], reverse=True)
    return {name: [i + 1, active, wins, pods] for i, (name, _, active, wins, pods) in enumerate(entries[:150])}


# Back up old snapshot
snap_path = _p('snapshot.json')
backup_dir = _p('old snapshots')
if os.path.exists(snap_path):
    os.makedirs(backup_dir, exist_ok=True)
    # Detect what cup the old snapshot was at
    with open(snap_path) as f:
        old_snap = json.load(f)
    old_cup = None
    w_snap = old_snap.get('w', {})
    if w_snap and w_players:
        top_name = next((n for n, v in w_snap.items() if v[0] == 1), None)
        if top_name:
            tp = next((p for p in w_players if p['n'] == top_name), None)
            if tp:
                for h in reversed(tp['h']):
                    if round(h['r'], 1) == round(w_snap[top_name][1], 1):
                        old_cup = h['c']
                        break
    label = f'snapshot {old_cup}.json' if old_cup else 'snapshot backup.json'
    backup_path = os.path.join(backup_dir, label)
    i = 0
    while os.path.exists(backup_path):
        i += 1
        stem = f'snapshot {old_cup}' if old_cup else 'snapshot backup'
        backup_path = os.path.join(backup_dir, f'{stem}_{i}.json')
    shutil.copy2(snap_path, backup_path)
    print(f"Backed up old snapshot -> old snapshots/{os.path.basename(backup_path)}")

snap = {
    'g': build_snap_at(g_players, target_cup),
    'w': build_snap_at(w_players, target_cup),
}

tmp = _p('snapshot.json') + '.tmp'
with open(tmp, 'w') as f:
    json.dump(snap, f, separators=(',', ':'))
os.replace(tmp, _p('snapshot.json'))
print(f"snapshot.json written (cup {target_cup}, {len(snap)} variants)")
