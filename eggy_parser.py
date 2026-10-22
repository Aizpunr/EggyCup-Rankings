"""
eggy_parser.py — pure parser for Eggy Cup COTDTracker logs.

No I/O, no prints, no sys.exit: new_cup.py, the submit poller and the tests
all call this. eggy_parser.js is a line-for-line port; any rule change here
must be mirrored there (tests/test_parser_js.py checks they agree).

Rules kept exactly as new_cup.py had them inline (cups 87-109 replay
byte-identical, see tests/test_replay.py):
  - rounds split on "Doing eliminations with leaderboard"; a round with no
    eliminations is skipped (warmup snapshot)
  - Eggy cup-end marker: a round whose leaderboard has one player who is also
    the only one "eliminated" names the winner and is not counted
  - winner priority: manual winner > marker (unless excluded) > single survivor;
    several survivors need finish_by_times (ranked by last-round time)
  - winner's time = their time in the last non-marker round they appear in
  - per round: finishers get distinct positions by time, DNFs tie at the bottom
  - fastest time scans every round, exclusions ignored (round 0 = warmup)
"""
import re
from dataclasses import dataclass, field

TRACKER = 'COTDTracker'
ROUND_HEADER = 'Doing eliminations with leaderboard'
CUP_START = 'Starting cup with exclude list'

_PLAYER_RE = re.compile(r'Player (.+?): Time: (.+)')
_PLAYER_NAME_RE = re.compile(r'Player (.+?): Time:')
_ELIM_RE = re.compile(r'Eliminating (?:DNF|on time): (.+)')
_HAS_ELIM_RE = re.compile(r'Eliminating (?:DNF|on time):')
_SID_RE = re.compile(r'Checking joined player (.+?) with steamID (\d+)')


class ParseError(Exception):
    pass


@dataclass
class ParsedCup:
    leaderboard: list          # [(name, time_str, round_or_None, pos)]
    winner: str
    winner_time: object        # raw time string, or None
    fastest_time: object       # float or None
    fastest_name: object
    fastest_round: object      # 0 = warmup snapshot
    n_rounds: int              # leaderboard blocks in the log (incl. warmup/marker)
    survivors: list = field(default_factory=list)  # all survivors, winner first


def filter_tracker_lines(lines):
    return [l for l in lines if TRACKER in l]


def split_rounds(lines):
    rounds = []
    current = []
    for line in lines:
        if ROUND_HEADER in line:
            if current:
                rounds.append(current)
            current = []
        elif 'Eliminating ' in line or 'Player ' in line:
            current.append(line)
    if current:
        rounds.append(current)
    return rounds


def split_lobbies(lines):
    """Split tracker lines at each cup start. Returns only the segments with at
    least one round that eliminated someone (a start that was immediately
    restarted, or a trailing start with nothing raced after it, is dropped)."""
    segments = [[]]
    for line in lines:
        if CUP_START in line:
            segments.append([])
        segments[-1].append(line)
    return [s for s in segments if any(round_info(r)[1] for r in split_rounds(s))]


def round_info(rnd):
    """(player_times, eliminated_names) for one round's lines."""
    player_times = {}
    eliminated = []
    for line in rnd:
        m = _PLAYER_RE.search(line)
        if m:
            player_times[m.group(1).strip()] = m.group(2).strip()
        m2 = _ELIM_RE.search(line)
        if m2:
            name = m2.group(1).strip()
            if name not in eliminated:
                eliminated.append(name)
    return player_times, eliminated


def is_marker(player_times, eliminated):
    return (len(player_times) == 1 and len(eliminated) == 1
            and next(iter(player_times)) == eliminated[0])


def named_players(rounds):
    """Every name with a leaderboard line, in first-appearance order. (new_cup.py
    used a set, so tied survivors came out in hash order; this pins it.)"""
    names = {}
    for rnd in rounds:
        for line in rnd:
            m = _PLAYER_NAME_RE.search(line)
            if m:
                names.setdefault(m.group(1).strip(), None)
    return list(names)


def steam_ids(lines):
    """{name: steamid64} from the tracker's join checks (empty names skipped)."""
    out = {}
    for line in lines:
        m = _SID_RE.search(line)
        if m and m.group(1).strip():
            out[m.group(1).strip()] = m.group(2)
    return out


def _time_key(t):
    try:
        return (0, float(t.replace(',', '.')))
    except (ValueError, AttributeError):
        return (1, 0.0)


def _eliminations(rounds, excluded):
    """Walk the rounds. Returns (elim_order, winner_marker, last_elim_times,
    last_round_elims) where last_round_elims are the raw names eliminated in the
    last competitive round (exclusions applied)."""
    elim_order = []
    actual_round = 0
    winner_marker = None
    last_elim_times = {}
    last_round_elims = []
    for rnd in rounds:
        player_times, eliminated = round_info(rnd)
        if not eliminated:
            continue
        if is_marker(player_times, eliminated):
            winner_marker = eliminated[0]
            continue
        actual_round += 1
        last_elim_times = player_times
        last_round_elims = []
        for name in eliminated:
            if name not in excluded:
                t = player_times.get(name, 'DNF')
                elim_order.append((name, t, actual_round, t == 'DNF'))
                last_round_elims.append(name)
    return elim_order, winner_marker, last_elim_times, last_round_elims


def _build_leaderboard(winner, winner_time, survivor_ranks, last_elim_times, elim_order):
    elim_order = list(reversed(elim_order))
    leaderboard = [(winner, winner_time, None, 1)]
    pos = 2
    for nm in survivor_ranks:
        leaderboard.append((nm, last_elim_times.get(nm, 'DNF'), None, pos))
        pos += 1
    i = 0
    while i < len(elim_order):
        rnd = elim_order[i][2]
        group = []
        while i < len(elim_order) and elim_order[i][2] == rnd:
            group.append(elim_order[i])
            i += 1
        finishers = []
        dnfs = []
        for name, display_time, r, dnf in group:
            if dnf:
                dnfs.append((name, display_time, r))
            else:
                try:
                    t = float(str(display_time).replace(',', '.'))
                    finishers.append((name, display_time, r, t))
                except (ValueError, TypeError):
                    dnfs.append((name, display_time, r))
        finishers.sort(key=lambda x: x[3])
        for name, display_time, r, _ in finishers:
            leaderboard.append((name, display_time, r, pos))
            pos += 1
        if dnfs:
            dnf_pos = pos
            for name, display_time, r in dnfs:
                leaderboard.append((name, display_time, r, dnf_pos))
            pos += len(dnfs)
    return leaderboard


def _winner_time(rounds, winner):
    for rnd in reversed(rounds):
        pt, elim = round_info(rnd)
        if is_marker(pt, elim):
            continue
        if winner in pt:
            return pt[winner]
    return None


def _fastest(rounds):
    fastest_time = fastest_name = fastest_round = None
    actual_round = 0
    for rnd in rounds:
        has_elim = any(_HAS_ELIM_RE.search(line) for line in rnd)
        if has_elim:
            actual_round += 1
        rnd_num = actual_round if has_elim else 0
        for line in rnd:
            m = _PLAYER_RE.search(line)
            if m:
                time_str = m.group(2).strip()
                if time_str != 'DNF':
                    t = float(time_str.replace(',', '.'))
                    if fastest_time is None or t < fastest_time:
                        fastest_time = t
                        fastest_name = m.group(1).strip()
                        fastest_round = rnd_num
    return fastest_time, fastest_name, fastest_round


def parse_cup_log(lines, excluded=(), manual_winner=None, finish_by_times=False):
    """Parse tracker lines (already filtered or not) into a ParsedCup.
    Raises ParseError with the same messages new_cup.py used to print."""
    lines = filter_tracker_lines(lines)
    if not lines:
        raise ParseError('No COTDTracker lines found in log file.')
    rounds = split_rounds(lines)
    if not rounds:
        raise ParseError('No elimination rounds found in log.')
    excluded = set(excluded)

    elim_order, winner_marker, last_elim_times, _ = _eliminations(rounds, excluded)
    elim_set = {e[0] for e in elim_order}
    all_named = named_players(rounds)

    survivor_ranks = []
    survivors = []
    if manual_winner:
        winner = manual_winner
        elim_order = [e for e in elim_order if e[0] != winner]
    elif winner_marker and winner_marker not in excluded:
        winner = winner_marker
    else:
        winners = [n for n in all_named if n not in elim_set and n not in excluded]
        if not winners:
            raise ParseError('Could not determine winner.')
        if len(winners) > 1:
            if not finish_by_times:
                raise ParseError(
                    f"{len(winners)} survivors ({', '.join(winners)}) — cup never "
                    f"reached a final. Pass --finish-by-times to rank them by their "
                    f"last-round times, or --winner to pick one.")
            winners.sort(key=lambda nm: _time_key(last_elim_times.get(nm, 'DNF')))
            survivor_ranks = winners[1:]
            survivors = list(winners)
        winner = winners[0]

    winner_time = _winner_time(rounds, winner)
    leaderboard = _build_leaderboard(winner, winner_time, survivor_ranks,
                                     last_elim_times, elim_order)
    fastest_time, fastest_name, fastest_round = _fastest(rounds)
    return ParsedCup(leaderboard, winner, winner_time, fastest_time, fastest_name,
                     fastest_round, len(rounds), survivors)


def detect_outcome(lines, excluded=()):
    """Classify how a cup ended, before any override is chosen. This drives the
    submit page's confirmation question. Returns a dict with 'kind':
      multi_start       several cup starts with rounds; 'lobbies' summarises each
      marker / single   one clear winner; 'podium' = top 3 names
      double_dnf_final  nobody survived and the last round was all DNF;
                        'finalists' + 'third' (None if it can't be named)
      survivors_left    2+ never eliminated; 'ranking' by last-round time
      no_winner         nobody survived, any other shape
    """
    lines = filter_tracker_lines(lines)
    lobbies = split_lobbies(lines)
    if len(lobbies) > 1:
        out = []
        for seg in lobbies:
            rounds = split_rounds(seg)
            comp = [r for r in rounds if round_info(r)[1]]
            first = round_info(comp[0])[0] if comp else {}
            out.append({'rounds': len(comp), 'players': len(first)})
        return {'kind': 'multi_start', 'lobbies': out}

    excluded = set(excluded)
    rounds = split_rounds(lines)
    if not rounds:
        return {'kind': 'no_winner'}
    elim_order, winner_marker, last_elim_times, last_round_elims = _eliminations(rounds, excluded)
    if winner_marker and winner_marker not in excluded:
        p = parse_cup_log(lines, excluded)
        return {'kind': 'marker', 'podium': [r[0] for r in p.leaderboard[:3]]}
    elim_set = {e[0] for e in elim_order}
    survivors = [n for n in named_players(rounds) if n not in elim_set and n not in excluded]
    if len(survivors) == 1:
        p = parse_cup_log(lines, excluded)
        return {'kind': 'single', 'podium': [r[0] for r in p.leaderboard[:3]]}
    if len(survivors) > 1:
        survivors.sort(key=lambda nm: _time_key(last_elim_times.get(nm, 'DNF')))
        return {'kind': 'survivors_left',
                'ranking': [(n, last_elim_times.get(n, 'DNF')) for n in survivors]}
    last = [e for e in elim_order if e[0] in last_round_elims and e[2] == elim_order[-1][2]] \
        if elim_order else []
    if len(last) >= 2 and all(e[3] for e in last):
        finalists = [e[0] for e in last]
        # 3rd = best of the round before, if it had a finisher
        prev_round = last[0][2] - 1
        prev = [e for e in elim_order if e[2] == prev_round]
        finishers = sorted((e for e in prev if not e[3]), key=lambda e: _time_key(e[1]))
        third = finishers[0][0] if finishers else (prev[0][0] if len(prev) == 1 else None)
        return {'kind': 'double_dnf_final', 'finalists': finalists, 'third': third}
    return {'kind': 'no_winner'}


def cup_json_payload(parsed, cup_num, mapper, label=None):
    """cup_N.json content. label overrides 'Eggy N' for unnumbered specials
    ('Eggy Roulette' kept the cup_num it had before the renumbering)."""
    return {
        'cup': label or f'Eggy {cup_num}',
        'cup_num': cup_num,
        'mapper': mapper,
        'players': [
            {'pos': position, 'name': name, 'time': time, 'round': rnd}
            for name, time, rnd, position in parsed.leaderboard
        ]
    }
