"""Cup-scoped analysis of LiveLeaderboardLogger output for Eggy Cup.

For a given Eggy cup number N:
  1. Parse cup tracker log (cup logs/eggy_<N>.log).
  2. Parse livelog (cup logs/eggy_<N>_liveleaderboard.log).
  3. Match cup-tracker rounds to livelog rounds via top-3 time fingerprint.
  4. For each cup-tracker DNF, infer LTG: sid set a fresh time during the round
     but is missing from the final LEADERBOARD before ROUND_ENDED.
  5. Patch cup_<N>.json with LTG times and re-sort that round's elim block.
  6. Resolve (sid, name) pairs to canonicals; additively update steam_ids.json.
  7. Write eggy_<N>_ltg_report.json.

Run: python analyze_cup_livelog.py <cup_number>
Optional: --no-apply to skip mutations (report only).

Cloned from the COTD project; only filenames/labels changed.
"""
import argparse
import io
import json
import os
import re
import sys
from collections import Counter, defaultdict

sys.stdout.reconfigure(encoding='utf-8')

HERE = os.path.dirname(os.path.abspath(__file__))
CUP_LOGS = os.path.join(HERE, 'cup logs')
ALLDATA = os.path.join(HERE, 'alldata.json')
STEAM_IDS = os.path.join(HERE, 'steam_ids.json')

# Import CANONICAL silently
_real_stdout = sys.stdout
sys.stdout = io.TextIOWrapper(io.BytesIO(), encoding='utf-8', write_through=True)
try:
    from elo_engine import CANONICAL  # type: ignore
finally:
    sys.stdout = _real_stdout

NAME_MAP = {}
for canonical, aliases in CANONICAL.items():
    NAME_MAP[canonical] = canonical
    for a in aliases:
        NAME_MAP[a] = canonical
if os.path.exists(ALLDATA):
    with open(ALLDATA, encoding='utf-8') as _f:
        _alldata = json.load(_f)
    for _p in _alldata.get('weighted', []):
        NAME_MAP.setdefault(_p['n'], _p['n'])


def strip_tag(name):
    return re.sub(r'\[.*?\]\s*', '', name).strip()


def resolve_canonical(observed_names):
    for n in observed_names:
        if n in NAME_MAP:
            return NAME_MAP[n]
        s = strip_tag(n)
        if s and s in NAME_MAP:
            return NAME_MAP[s]
    return None


# ── Cup tracker parser ──
def parse_cup_tracker(path):
    with open(path, encoding='utf-8', errors='replace') as f:
        lines = [l for l in f if 'COTDTracker' in l]
    blocks = []
    current = None
    for line in lines:
        if 'Doing eliminations with leaderboard' in line:
            if current is not None:
                blocks.append(current)
            current = {'leaderboard': [], 'dnfs': [],
                       'eliminated_dnf': [], 'eliminated_on_time': []}
        elif current is None:
            continue
        elif 'Eliminating DNF: ' in line:
            m = re.search(r'Eliminating DNF: (.+)', line)
            if m:
                current['eliminated_dnf'].append(m.group(1).strip())
        elif 'Eliminating on time: ' in line:
            m = re.search(r'Eliminating on time: (.+)', line)
            if m:
                current['eliminated_on_time'].append(m.group(1).strip())
        else:
            m = re.search(r'Player (.+?): Time: (.+)', line)
            if m:
                name, time = m.group(1).strip(), m.group(2).strip()
                current['leaderboard'].append((name, time))
                if time == 'DNF':
                    current['dnfs'].append(name)
    if current:
        blocks.append(current)
    cup_rounds = []
    for b in blocks:
        n_elim = len(b['eliminated_dnf']) + len(b['eliminated_on_time'])
        if n_elim == 0:
            continue
        b['n_elim'] = n_elim
        b['round_n'] = len(cup_rounds) + 1
        b['eliminated'] = b['eliminated_dnf'] + b['eliminated_on_time']
        cup_rounds.append(b)
    return cup_rounds


# ── Livelog parser ──
LEADERBOARD_RE = re.compile(r'\[LiveLeaderboardLogger\] LEADERBOARD\|(\d+)\|(\d+)\|(.*)')
ROUND_ENDED_RE = re.compile(r'\[LiveLeaderboardLogger\] ROUND_ENDED\|(\d+)')
SESSION_START_RE = re.compile(r'\[LiveLeaderboardLogger\] SESSION_START')
ROSTER_RE = re.compile(r'\[LiveLeaderboardLogger\] ROSTER\|(\d+)\|([^|]*)\|([^|]*)')
RESULT_RE = re.compile(r'\[LiveLeaderboardLogger\] RESULT\|(\d+)\|(\d+)\|([^|]+)\|time=([^|]+)\|')


def parse_lb_entries(s):
    out = []
    if not s:
        return out
    for chunk in s.split(','):
        parts = chunk.split(':')
        if len(parts) >= 3:
            try:
                out.append((int(parts[0]), parts[1], float(parts[2])))
            except (ValueError, IndexError):
                pass
    return out


def parse_livelog(path):
    if not os.path.exists(path):
        return {'roster': {}, 'rounds': []}
    roster = defaultdict(Counter)
    sessions = []
    current_session = None
    current_events = []
    current_fresh_results = {}
    with open(path, encoding='utf-8-sig', errors='replace') as f:
        for line in f:
            if SESSION_START_RE.search(line):
                if current_session is not None:
                    sessions.append(current_session)
                current_session = {'rounds': []}
                current_events = []
                current_fresh_results = {}
                continue
            m = ROSTER_RE.search(line)
            if m:
                sid = m.group(1)
                untagged = m.group(2).strip()
                tagged = m.group(3).strip()
                if untagged:
                    roster[sid][untagged] += 1
                if tagged and tagged != untagged:
                    roster[sid][tagged] += 1
                continue
            if current_session is None:
                continue
            m = RESULT_RE.search(line)
            if m:
                sid = m.group(2)
                name = m.group(3).strip()
                tm = m.group(4).strip()
                if tm not in ('?', 'None', ''):
                    try:
                        current_fresh_results[sid] = float(tm)
                    except ValueError:
                        pass
                if name and name != '?':
                    roster[sid][name] += 1
                continue
            m = LEADERBOARD_RE.search(line)
            if m:
                ts = line.split(' ', 1)[0]
                current_events.append((ts, parse_lb_entries(m.group(3))))
                continue
            m = ROUND_ENDED_RE.search(line)
            if m:
                ts = line.split(' ', 1)[0]
                final_lb = {}
                all_sids_seen = {}
                for _, entries in current_events:
                    for _, sid, time in entries:
                        all_sids_seen[sid] = time
                if current_events:
                    _, last_entries = current_events[-1]
                    final_lb = {sid: time for _, sid, time in last_entries}
                current_session['rounds'].append({
                    'round_idx': len(current_session['rounds']) + 1,
                    'final_lb': final_lb,
                    'all_sids_seen': all_sids_seen,
                    'fresh_results': current_fresh_results,
                    'end_ts': ts,
                })
                current_events = []
                current_fresh_results = {}
    if current_session is not None:
        sessions.append(current_session)
    last_with_rounds = next((s for s in reversed(sessions) if s['rounds']), None)
    return {
        'roster': dict(roster),
        'rounds': last_with_rounds['rounds'] if last_with_rounds else [],
    }


# ── Round matching ──
def parse_cup_time(s):
    if s == 'DNF':
        return None
    return float(s.replace(',', '.'))


def cup_round_top_n(cup_round, n=3):
    finishers = [(name, parse_cup_time(t)) for name, t in cup_round['leaderboard'] if t != 'DNF']
    finishers.sort(key=lambda x: x[1])
    return finishers[:n]


def ll_round_top_n(ll_round, n=3):
    items = sorted(ll_round['final_lb'].items(), key=lambda kv: kv[1])
    return items[:n]


def match_rounds(cup_rounds, ll_rounds, eps_per_player=0.005):
    matches = []
    used_ll = set()
    for cr in cup_rounds:
        cr_top = cup_round_top_n(cr, 3)
        if not cr_top:
            matches.append((cr, None, 'no_finishers_in_cup_tracker'))
            continue
        best_idx = None
        best_score = None
        for lri, lr in enumerate(ll_rounds):
            if lri in used_ll:
                continue
            lr_top = ll_round_top_n(lr, 3)
            if len(lr_top) < len(cr_top):
                continue
            score = sum(abs(cr_top[i][1] - lr_top[i][1]) for i in range(len(cr_top)))
            if best_score is None or score < best_score:
                best_score = score
                best_idx = lri
        threshold = eps_per_player * len(cr_top)
        if best_idx is None or best_score is None or best_score > threshold:
            matches.append((cr, None, f'no_match (best_score={best_score})'))
        else:
            used_ll.add(best_idx)
            matches.append((cr, ll_rounds[best_idx], f'matched (score={best_score:.4f}, ll_idx={best_idx + 1})'))
    return matches


# ── LTG inference ──
def build_lookups(roster):
    name_to_sid = {}
    canonical_to_sid = {}
    for sid, name_counter in roster.items():
        ordered = [n for n, _ in name_counter.most_common()]
        for n in ordered:
            name_to_sid.setdefault(n, sid)
            stripped = strip_tag(n)
            if stripped:
                name_to_sid.setdefault(stripped, sid)
        c = resolve_canonical(ordered)
        if c is not None:
            canonical_to_sid.setdefault(c, sid)
    return name_to_sid, canonical_to_sid


def lookup_sid(name, name_to_sid, canonical_to_sid):
    if name in name_to_sid:
        return name_to_sid[name]
    stripped = strip_tag(name)
    if stripped in name_to_sid:
        return name_to_sid[stripped]
    canonical = resolve_canonical([name])
    if canonical and canonical in canonical_to_sid:
        return canonical_to_sid[canonical]
    return None


def infer_ltg(matches, name_to_sid, canonical_to_sid):
    findings = []
    for cr, lr, _status in matches:
        if lr is None:
            for dnf_name in cr['dnfs']:
                findings.append({
                    'round': cr['round_n'], 'name': dnf_name, 'sid': None,
                    'verdict': 'unmatched_round', 'ltg_time': None,
                })
            continue
        for dnf_name in cr['dnfs']:
            sid = lookup_sid(dnf_name, name_to_sid, canonical_to_sid)
            if not sid:
                findings.append({
                    'round': cr['round_n'], 'name': dnf_name, 'sid': None,
                    'verdict': 'unresolved_name', 'ltg_time': None,
                })
                continue
            fresh_time = lr.get('fresh_results', {}).get(sid)
            if fresh_time is not None:
                findings.append({
                    'round': cr['round_n'], 'name': dnf_name, 'sid': sid,
                    'verdict': 'LTG', 'ltg_time': round(fresh_time, 5),
                })
            else:
                findings.append({
                    'round': cr['round_n'], 'name': dnf_name, 'sid': sid,
                    'verdict': 'true_DNF', 'ltg_time': None,
                })
    return findings


# ── Patch cup_<N>.json ──
def patch_cup_json(cup_n, ltg_findings, dry_run=False):
    path = os.path.join(HERE, f'cup_{cup_n}.json')
    if not os.path.exists(path):
        return {'patched': False, 'reason': 'cup_<N>.json not found'}
    with open(path, encoding='utf-8') as f:
        cup_data = json.load(f)
    ltg_cases = [f for f in ltg_findings if f['verdict'] == 'LTG']
    if not ltg_cases:
        return {'patched': False, 'reason': 'no LTG cases', 'changes': []}
    players = cup_data['players']
    changes = []
    for case in ltg_cases:
        round_n = case['round']
        name = case['name']
        ltg_time = case['ltg_time']
        time_str = f'{ltg_time:.5f}'.replace('.', ',')
        target = next(
            (p for p in players if p.get('round') == round_n and p['name'] == name and p['time'] == 'DNF'),
            None,
        )
        if target is None:
            changes.append({'round': round_n, 'name': name, 'status': 'no_dnf_row_found'})
            continue
        old_pos = target['pos']
        target['time'] = time_str
        changes.append({'round': round_n, 'name': name, 'old_pos': old_pos, 'new_time': time_str})
    winner = [p for p in players if p.get('round') is None]
    rounds = defaultdict(list)
    for p in players:
        if p.get('round') is not None:
            rounds[p['round']].append(p)

    def sort_key(p):
        if p['time'] == 'DNF':
            return (1, 0.0)
        return (0, float(str(p['time']).replace(',', '.')))

    for rn in rounds:
        rounds[rn].sort(key=sort_key)
    new_players = list(winner)
    for rn in sorted(rounds.keys(), reverse=True):
        new_players.extend(rounds[rn])
    for i, p in enumerate(new_players):
        new_pos = i + 1
        if p['pos'] != new_pos:
            for c in changes:
                if c.get('name') == p['name'] and c.get('round') == p.get('round'):
                    c['new_pos'] = new_pos
        p['pos'] = new_pos
    cup_data['players'] = new_players
    if not dry_run:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(cup_data, f, ensure_ascii=False, indent=2)
    return {'patched': not dry_run, 'changes': changes}


# ── Steam-id merge (additive) ──
def merge_steam_ids(roster, dry_run=False):
    if os.path.exists(STEAM_IDS):
        with open(STEAM_IDS, encoding='utf-8') as f:
            current = json.load(f)
    else:
        current = {}
    added, confirmed, conflicts, unresolved = [], [], [], []
    for sid, name_counter in roster.items():
        ordered = [n for n, _ in name_counter.most_common()]
        canonical = resolve_canonical(ordered)
        if canonical is None:
            unresolved.append({'sid': sid, 'observed_names': ordered})
            continue
        existing = current.get(canonical)
        if existing is None:
            current[canonical] = sid
            added.append({'canonical': canonical, 'sid': sid, 'observed_as': ordered[0]})
        elif existing == sid:
            confirmed.append(canonical)
        else:
            conflicts.append({'canonical': canonical, 'existing': existing, 'observed': sid, 'observed_as': ordered[0]})
    if not dry_run and added:
        out = dict(sorted(current.items(), key=lambda kv: kv[0].lower()))
        with open(STEAM_IDS, 'w', encoding='utf-8') as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
    return {'added': added, 'confirmed_count': len(confirmed),
            'conflicts': conflicts, 'unresolved': unresolved}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('cup_n', type=int, help='Cup number (e.g., 89)')
    p.add_argument('--no-apply', action='store_true', help='Dry run')
    args = p.parse_args()

    n = args.cup_n
    cup_log = os.path.join(CUP_LOGS, f'eggy_{n}.log')
    ll_log = os.path.join(CUP_LOGS, f'eggy_{n}_liveleaderboard.log')

    if not os.path.exists(cup_log):
        print(f'[!] Cup tracker log not found: {cup_log}', file=sys.stderr)
        sys.exit(1)
    if not os.path.exists(ll_log):
        print(f'[!] Livelog not found: {ll_log}', file=sys.stderr)
        sys.exit(1)

    dry = args.no_apply
    print(f'== Analyzing Eggy {n}{" (DRY RUN)" if dry else ""} ==')
    cup_rounds = parse_cup_tracker(cup_log)
    print(f'  cup tracker rounds: {len(cup_rounds)}')
    ll_data = parse_livelog(ll_log)
    print(f'  livelog roster: {len(ll_data["roster"])} sids')
    print(f'  livelog rounds (last session): {len(ll_data["rounds"])}')

    matches = match_rounds(cup_rounds, ll_data['rounds'])
    matched_count = sum(1 for _, lr, _ in matches if lr is not None)
    print(f'  matched: {matched_count}/{len(cup_rounds)}')
    for cr, lr, status in matches:
        if lr is None:
            print(f'    [!] cup R{cr["round_n"]}: {status}')

    name_to_sid, canonical_to_sid = build_lookups(ll_data['roster'])
    findings = infer_ltg(matches, name_to_sid, canonical_to_sid)
    by_verdict = Counter(f['verdict'] for f in findings)
    print(f'  DNF entries scanned: {len(findings)}')
    for v, c in by_verdict.most_common():
        print(f'    {v}: {c}')

    for f in [x for x in findings if x['verdict'] == 'LTG']:
        print(f'    R{f["round"]} {f["name"]} -> LTG {f["ltg_time"]:.3f}')

    patch_result = patch_cup_json(n, findings, dry_run=dry)
    if patch_result.get('changes'):
        action = 'applied' if patch_result['patched'] else 'would apply (dry run)'
        print(f'  cup_{n}.json {action}: {len(patch_result["changes"])} change(s)')
        for c in patch_result['changes']:
            if 'new_time' in c:
                print(f'    R{c["round"]} {c["name"]}: pos {c["old_pos"]} -> {c.get("new_pos", c["old_pos"])}, time DNF -> {c["new_time"]}')
            else:
                print(f'    R{c["round"]} {c["name"]}: {c["status"]}')
    else:
        print(f'  cup_{n}.json: no LTG patches needed')

    sid_result = merge_steam_ids(ll_data['roster'], dry_run=dry)
    action_word = 'applied' if not dry else 'would apply'
    print(f'  steam_ids.json: {action_word} +{len(sid_result["added"])} new, '
          f'{sid_result["confirmed_count"]} confirmed, '
          f'{len(sid_result["conflicts"])} conflicts, '
          f'{len(sid_result["unresolved"])} unresolved')
    for a in sid_result['added']:
        print(f'    + {a["canonical"]} -> {a["sid"]}  (observed as {a["observed_as"]!r})')
    for c in sid_result['conflicts']:
        print(f'    [!] CONFLICT {c["canonical"]}: kept existing={c["existing"]}, ignored observed={c["observed"]}')
    if sid_result['unresolved']:
        print(f'  unresolved sids (potential new players, not added):')
        for u in sid_result['unresolved']:
            print(f'    {u["sid"]}  observed as {u["observed_names"]}')

    report = {
        'cup': f'Eggy {n}',
        'cup_num': n,
        'dry_run': dry,
        'cup_tracker_rounds': len(cup_rounds),
        'livelog_rounds': len(ll_data['rounds']),
        'matched_rounds': matched_count,
        'match_log': [
            {'cup_round': cr['round_n'], 'livelog_round_idx': lr['round_idx'] if lr else None, 'status': status}
            for cr, lr, status in matches
        ],
        'findings': findings,
        'cup_json_patch': patch_result,
        'steam_ids_merge': sid_result,
    }
    out_path = os.path.join(HERE, f'eggy_{n}_ltg_report.json')
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f'\nWrote {out_path}')


if __name__ == '__main__':
    main()
