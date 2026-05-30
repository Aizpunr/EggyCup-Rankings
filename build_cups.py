"""
build_cups.py — invert elo_results.json into cup-centric cups.json.

Pulls cup dates from cup log mtimes (under `cup logs/`). Map names + mappers
are stored in the local `map_index` dict — fill them in once you know them.

Strength of Field uses **cross-comp ELO** (not Eggy-local ratings) so SOF
values are comparable to the cross-comp page and the SOF mod. Reads
`../zeepkist holistic/elo_pool.json` for ratings and the dynamic FLOOR/PEAK
anchors from `../zeepkist holistic/allcompdata.json`. Falls back to the old
Eggy-local SOF if those files aren't available.
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
    'Eggy 90': {'map': '',            'mapper': 'heyovio'},
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

# ── Strength of Field (cross-comp rating pool) ──
CROSSCOMP_DIR = os.path.normpath(os.path.join(base, '..', 'zeepkist holistic'))
ELO_POOL_PATH = os.path.join(CROSSCOMP_DIR, 'elo_pool.json')
ALLCOMP_PATH  = os.path.join(CROSSCOMP_DIR, 'allcompdata.json')
EGGY_SIDS_PATH = _p('steam_ids.json')


def load_crosscomp_sof():
    """Returns (sid_to_rating, name_to_sid, SOF_FLOOR, SOF_PEAK) or None if
    cross-comp data is unavailable."""
    if not (os.path.exists(ELO_POOL_PATH) and os.path.exists(ALLCOMP_PATH)
            and os.path.exists(EGGY_SIDS_PATH)):
        return None
    try:
        with open(ELO_POOL_PATH, encoding='utf-8') as f:
            pool = json.load(f)
        with open(ALLCOMP_PATH, encoding='utf-8') as f:
            allcomp = json.load(f)
        with open(EGGY_SIDS_PATH, encoding='utf-8') as f:
            eggy_sids = json.load(f)
    except Exception as e:
        print(f"[SOF] cross-comp load failed ({e}); falling back to Eggy-local SOF")
        return None
    sid_to_r = {p['steam_id']: p['elo'] for p in pool.get('players', [])}
    cfg = allcomp.get('config', {})
    floor = cfg.get('SOF_FLOOR', 1287.0)
    peak  = cfg.get('SOF_PEAK', 1968.11)
    return sid_to_r, eggy_sids, floor, peak


sof_data = load_crosscomp_sof()
if sof_data is not None:
    sid_to_r, name_to_sid, SOF_FLOOR, SOF_PEAK = sof_data

    # If allcompdata.json has a pre-computed SOF for this Eggy cup (rating-as-of-event-time),
    # use it directly so Eggy page and cross-comp page stay identical. Otherwise fall back
    # to current-pool computation (covers brand-new cups not yet in cross-comp).
    crosscomp_sof_by_id = {}
    try:
        with open(ALLCOMP_PATH, encoding='utf-8') as f:
            allcomp = json.load(f)
        for ev in allcomp.get('events', []):
            if ev.get('comp') == 'eggy':
                crosscomp_sof_by_id[ev.get('id')] = (ev.get('sof'), ev.get('avg_top10'))
    except Exception:
        pass

    print(f"[SOF] cross-comp pool: FLOOR={SOF_FLOOR} PEAK={SOF_PEAK}, "
          f"{len(crosscomp_sof_by_id)} Eggy events pre-computed in allcompdata")

    for cup in result:
        if cup['id'] in crosscomp_sof_by_id:
            sof, avg = crosscomp_sof_by_id[cup['id']]
            cup['strength'] = round(sof, 1) if sof is not None else 0.0
            cup['sof_avg_top10'] = round(avg, 1) if avg is not None else None
            cup['sof_source'] = 'crosscomp'
            continue
        # Fallback: compute from current pool (rating-now, not rating-then)
        ratings = []
        unknown = []
        for p in cup['players']:
            sid = name_to_sid.get(p['name'])
            r = sid_to_r.get(sid) if sid else None
            if r is not None:
                ratings.append(r)
            else:
                unknown.append(p['name'])
        ratings.sort(reverse=True)
        top10 = ratings[:10]
        while len(top10) < 10:
            top10.append(SOF_FLOOR)
        avg = sum(top10) / 10.0
        if SOF_PEAK > SOF_FLOOR:
            cup['strength'] = round((avg - SOF_FLOOR) / (SOF_PEAK - SOF_FLOOR) * 100, 1)
        else:
            cup['strength'] = 0.0
        cup['sof_avg_top10'] = round(avg, 1)
        cup['sof_unrated_count'] = len(unknown)
        cup['sof_source'] = 'eggy_current_pool'

else:
    # ── Fallback: original Eggy-local SOF (kept so the script still works if
    # cross-comp data is missing) ──
    print("[SOF] cross-comp data unavailable; using Eggy-local SOF")
    POOL_CAP = 100
    running = {}
    pre_norm = []

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
