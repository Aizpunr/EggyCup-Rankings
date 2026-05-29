"""
build_cups.py — invert elo_results.json into cup-centric cups.json.

Pulls cup dates from cup log mtimes (under `cup logs/`). Map names + mappers
are stored in the local `map_index` dict — fill them in once you know them.
Strength of Field is computed within the Eggy pool (top 10 ELOs in the lobby,
normalized against the running ELO pool).
"""
import json, re, sys, os, datetime
sys.stdout.reconfigure(encoding='utf-8')

base = os.path.dirname(os.path.abspath(__file__))
def _p(f): return os.path.join(base, f)

# Map + mapper per cup. Fill in as info arrives — empty strings render as "TBD".
map_index = {
    'Eggy 87': {'map': 'pizza track', 'mapper': 'Wheelie'},
    'Eggy 88': {'map': '',            'mapper': 'void & zodiak'},
    'Eggy 89': {'map': '',            'mapper': 'vectortrajector'},
}

# Cup dates — fall back to log mtime if not explicit.
CUP_DATES = {}


def cup_date(cid):
    if cid in CUP_DATES:
        return CUP_DATES[cid]
    m = re.search(r'\d+', cid)
    if not m:
        return None
    log_path = _p(os.path.join('cup logs', f'eggy_{m.group()}.log'))
    if os.path.exists(log_path):
        return datetime.date.fromtimestamp(os.path.getmtime(log_path)).isoformat()
    return None


# ── Invert player history into cup-centric data ──
with open(_p('elo_results.json'), encoding='utf-8') as f:
    elo = json.load(f)

cups = {}
for player in elo['leaderboard']:
    for h in player['history']:
        cid = h['cup']
        if cid not in cups:
            cups[cid] = {'players': [], 'lobby_size': h['lobby_size']}
        cups[cid]['players'].append({
            'pos': h['position'],
            'name': player['name'],
            'rating_after': h['rating'],
        })

for cid in cups:
    cups[cid]['players'].sort(key=lambda p: p['pos'])


def cup_sort_key(cid):
    m = re.search(r'(\d+)', cid)
    return int(m.group(1)) if m else 0


result = []
for cid in sorted(cups.keys(), key=cup_sort_key):
    meta = map_index.get(cid, {'map': '', 'mapper': ''})
    result.append({
        'id': cid,
        'map': meta['map'],
        'mapper': meta['mapper'],
        'date': cup_date(cid),
        'lobby_size': cups[cid]['lobby_size'],
        'players': cups[cid]['players'],
    })

# ── Strength of Field (within Eggy pool) ──
POOL_CAP = 100
running = {}    # name -> latest known rating
pre_norm = []   # pre_norm[i] = normalized pool BEFORE cup i

for cup in result:
    entries = sorted(running.items(), key=lambda x: x[1], reverse=True)
    pool = entries[:POOL_CAP]
    norm = {}
    if pool:
        max_r = pool[0][1]
        scale = 2000 / max_r if max_r > 0 else 1
        for name, rating in pool:
            norm[name] = rating * scale
    pre_norm.append(norm)
    for p in cup['players']:
        running[p['name']] = p['rating_after']

# Early cups have no prior data — use the most-developed pool we've got
seed_idx = next((i for i, n in enumerate(pre_norm) if len(n) >= 10), len(pre_norm) - 1)
seed_norm = pre_norm[seed_idx] if pre_norm else {}
rank_maps = [seed_norm if i < seed_idx else pre_norm[i] for i in range(len(result))]

for i, cup in enumerate(result):
    norm_map = rank_maps[i]
    if len(norm_map) < 2:
        cup['strength'] = 0
        continue
    elos = sorted(
        [norm_map[p['name']] for p in cup['players'] if p['name'] in norm_map],
        reverse=True,
    )[:10]
    if not elos:
        cup['strength'] = 0
        continue
    min_pool = min(norm_map.values())
    while len(elos) < 10:
        elos.append(min_pool)
    avg = sum(elos) / len(elos)
    cup['strength'] = round(avg / 1850 * 100, 1)

with open(_p('cups.json'), 'w', encoding='utf-8') as f:
    json.dump(result, f, ensure_ascii=False, separators=(',', ':'))

print(f"Done. {len(result)} cups written to cups.json")
for c in result:
    map_label = c['map'] or 'TBD'
    mapper_label = c['mapper'] or 'TBD'
    print(f"  {c['id']}: {map_label} by {mapper_label} — {len(c['players'])} players, SOF {c['strength']}")
