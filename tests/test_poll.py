"""Offline checks for the Eggy submission contract and private-log boundary."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import submissions_poll as P


def submission(outcome='confirmed', winner=None, lobby=None):
    return {'id': 'test', 'cup': 110, 'mapper': 'Mapper', 'date': '2026-10-24', 'map': '',
            'exclude': [], 'ruling': {'lobby': lobby, 'outcome': outcome, 'winner': winner}}


@pytest.mark.parametrize('outcome,flags', [
    ('confirmed', []), ('third_wins', ['--winner', 'Winner']),
    ('replay_winner', ['--winner', 'Winner']), ('finish_by_times', ['--finish-by-times'])])
def test_command(outcome, flags):
    sub = submission(outcome, 'Winner' if 'wins' in outcome or outcome == 'replay_winner' else None, 2)
    ctx = SimpleNamespace(python='python', repo=str(ROOT))
    base = ['python', str(ROOT / 'new_cup.py'), '110', 'Mapper', '--date', '2026-10-24', '--log', 'input.log']
    assert P.new_cup_command(ctx, sub, 'input.log') == base + ['--lobby', '2'] + flags
    sub.update(map='Map Name', exclude=['Other', 'Another'])
    assert P.new_cup_command(ctx, sub, 'input.log') == base + ['--map', 'Map Name', '--exclude', 'Other,Another', '--lobby', '2'] + flags


def test_review_no_command():
    with pytest.raises(ValueError):
        P.new_cup_command(SimpleNamespace(python='python', repo=str(ROOT)), submission('review'), 'input.log')


@pytest.mark.parametrize('log,outcome,winner,lobby,exclude,status', [
    ('eggy_94.log', 'third_wins', 'SuperHeavy', None, ['[ZET]vectortrajector'], None),
    ('eggy_94.log', 'third_wins', 'Kernkob', None, ['[ZET]vectortrajector'], 'review'),
    ('eggy_94.log', 'confirmed', None, None, ['[ZET]vectortrajector'], 'review'),
    ('eggy_109.log', 'confirmed', None, None, ['[ZET] Wheelie'], None),
    ('eggy_96.log', 'finish_by_times', None, None, 'manifest96', None),
    ('eggy_101_full.log', 'confirmed', None, None, [], 'failed'),
    ('eggy_101_full.log', 'confirmed', None, 2, [], None),
])
def test_real_log(log, outcome, winner, lobby, exclude, status):
    if exclude == 'manifest96':
        exclude = json.loads((ROOT / 'tests/fixtures/manifest.json').read_text(encoding='utf-8'))['96']['exclude']
    sub = submission(outcome, winner, lobby)
    sub['exclude'] = exclude
    result, note = P.check_ruling(sub, ROOT / 'cup logs' / log)
    assert result == status, note
    if status == 'review':
        assert note == f'ruling {outcome} does not match the log (double_dnf_final)'
    if status == 'failed':
        assert note == 'log has 2 cup starts; the submitter must pick one'


def test_fold_name():
    assert P.fold_name('[ZET] Wheel-ie!') == 'wheelie'
    assert P.fold_name('A[clan] B_12') == 'ab12'
    assert P.fold_name('\u00c9lodie') == '\u00e9lodie'


def test_names_body():
    old = {'glicko': [{'n': '[ZET] Wheelie'}], 'weighted': [{'n': 'Existing'}]}
    new = {'glicko': [{'n': 'Wheel-ie'}, {'n': 'New.Name'}], 'weighted': [{'n': 'Other'}]}
    players = {'New Name': {'steam_id': '123', 'aliases': ['newname']}}
    section = P.names_to_check(old, new, players)
    body = P.pr_body(submission(), {'players': 3, 'podium': [{'name': 'Winner'}], 'warnings': []}, section)
    assert 'Names to check' in body
    assert 'possible alias of [ZET] Wheelie' in body
    assert 'known in other comps as New Name' in body
    assert '- Other' in body
    assert 'No livelog: no Steam ID check and no left-the-game pass. Raw log stays private in the Worker.' in body
    assert 'players.json not reachable' in P.names_to_check(old, new)


def test_stage_private_boundary(tmp_path, monkeypatch):
    (tmp_path / 'Eggy Cup 87-110.xlsx').touch()
    (tmp_path / 'cup logs').mkdir()
    (tmp_path / 'cup logs/private.log').touch()
    paths = P.staging_paths(str(tmp_path), 110)
    assert 'Eggy Cup 87-110.xlsx' in paths
    assert not any(p.replace('\\', '/').startswith('cup logs/') for p in paths)
    calls = []
    def git(ctx, *args, **kwargs):
        calls.append(args)
        return ''
    monkeypatch.setattr(P, 'git', git)
    monkeypatch.setattr(P, 'collect_names', lambda ctx: 'Names to check')
    monkeypatch.setattr(P.subprocess, 'run', lambda *a, **kw: SimpleNamespace(returncode=0, stdout='https://example.test/pr/1', stderr=''))
    ctx = SimpleNamespace(repo=str(tmp_path), subdir=str(tmp_path), branch='main', remote='origin', rehearsal=False, dry_run=False)
    P.open_pr(ctx, submission(), {'players': 3, 'winner': 'Winner'})
    add = [args for args in calls if args[0] == 'add']
    assert add[0] == ('add', '-u', '--', '.', ':(exclude)cup logs/**')
    assert not any(p.startswith('cup logs/') for args in add for p in args)


def test_worker_offline():
    node = shutil.which('node')
    if node is None:
        pytest.skip('node not installed')
    r = subprocess.run([node, '--preserve-symlinks', '--preserve-symlinks-main', str(ROOT / 'tests/js/test_worker.mjs')], cwd=ROOT,
                       capture_output=True, encoding='utf-8', timeout=30)
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.parametrize('output,returncode,expected', [
    ('Eggy 110 COMPLETE\n', 0, 'processed'),
    ('prefix Eggy 110 COMPLETE suffix\n', 0, 'failed'),
    ('Eggy 110 COMPLETE\n', 1, 'failed'),
])
def test_completion_contract(tmp_path, monkeypatch, output, returncode, expected):
    ctx = SimpleNamespace(repo=str(tmp_path), subdir=str(tmp_path), python='python',
                          dry_run=False, rehearsal=True, ci=True, state={}, exit_code=0,
                          save_state=lambda: None)
    statuses = []
    monkeypatch.setattr(P, 'resolve_mapper', lambda ctx, sub, path: (sub['mapper'], ''))
    monkeypatch.setattr(P, 'check_ruling', lambda *a: (None, ''))
    monkeypatch.setattr(P, 'post_status', lambda ctx, sid, status, *a: statuses.append(status))
    monkeypatch.setattr(P, 'toast', lambda *a: None)
    monkeypatch.setattr(P, 'open_pr', lambda *a: pytest.fail('rehearsal must never publish'))
    monkeypatch.setattr(P, 'publish_direct', lambda *a: pytest.fail('rehearsal must never publish'))
    def run(*args, **kwargs):
        assert kwargs['env']['PYTHONIOENCODING'] == 'utf-8'
        kwargs['stdout'].write(output.encode())
        return SimpleNamespace(returncode=returncode)
    monkeypatch.setattr(P.subprocess, 'run', run)
    P.run_cup(ctx, submission(), 'input.log')
    assert statuses == [expected]
    assert ctx.exit_code == (1 if expected == 'failed' else 0)


def test_mismatch_does_not_run_pipeline(tmp_path, monkeypatch):
    ctx = SimpleNamespace(exit_code=0)
    statuses = []
    monkeypatch.setattr(P, 'resolve_mapper', lambda ctx, sub, path: (sub['mapper'], ''))
    monkeypatch.setattr(P, 'check_ruling', lambda *a: ('review', 'mismatch'))
    monkeypatch.setattr(P, 'post_status', lambda ctx, sid, status, *a: statuses.append(status))
    monkeypatch.setattr(P.subprocess, 'run', lambda *a, **kw: pytest.fail('mismatched ruling ran pipeline'))
    P.run_cup(ctx, submission(), 'input.log')
    assert statuses == ['review']


def test_open_pr_guard():
    with pytest.raises(RuntimeError, match='publication disabled'):
        P.open_pr(SimpleNamespace(rehearsal=True, dry_run=False), submission(), {})


def test_publish_direct_guard():
    with pytest.raises(RuntimeError, match='publication disabled'):
        P.publish_direct(SimpleNamespace(rehearsal=False, dry_run=True), submission(), {}, '')


def _mapper_ctx(tmp_path):
    (tmp_path / 'cups.json').write_text(json.dumps({'cups': [
        {'mapper': 'vectortrajector & Wheelie'}, {'mapper': 'justMaki'}, {'mapper': 'TBD'},
        {'mapper': 'Minkus'}]}), encoding='utf-8')
    return SimpleNamespace(repo=str(tmp_path))


@pytest.mark.parametrize('typed,exclude,raced,want,noted', [
    # The typo that went live on 110 and 112, with and without the raw name excluded.
    ('Vectortrojector', ['[ZET]vectortrajector'], [], '[ZET]vectortrajector', False),
    ('Vectortrojector', [], [], 'vectortrajector', False),
    ('Justmaki', [], [], 'justMaki', False),
    ('[ZET]vectortrajector', ['[ZET]vectortrajector'], [], '[ZET]vectortrajector', False),
    ('Brand New Mapper', [], [], 'Brand New Mapper', False),
    # Picked from the log's names: taken as is, and the pipeline excludes them.
    ('Minkus', [], ['Minkus'], 'Minkus', False),
    # A typo of someone who raced: excluding them would drop a real result.
    ('Minkuss', [], ['Minkus'], 'Minkuss', True),
])
def test_resolve_mapper(tmp_path, monkeypatch, typed, exclude, raced, want, noted):
    monkeypatch.setattr('eggy_parser.named_players', lambda lines: raced)
    log_file = tmp_path / 'input.log'
    log_file.write_text('', encoding='utf-8')
    sub = dict(submission(), mapper=typed, exclude=exclude)
    name, note = P.resolve_mapper(_mapper_ctx(tmp_path), sub, str(log_file))
    assert name == want
    assert bool(note) == noted


def _flag_ctx(tmp_path, monkeypatch, last=112, date='2026-11-07', previous=('A', 'B'), now=('B', 'A')):
    monkeypatch.setattr(P, 'git', lambda ctx, *a, **k: json.dumps({f'Eggy {last}': {'date': date}}))
    for cup, names in ((last, previous), (113, now)):
        (tmp_path / f'cup_{cup}.json').write_text(json.dumps({'players': [{'name': n} for n in names]}), encoding='utf-8')
    return SimpleNamespace(repo=str(tmp_path), remote='origin', branch='main')


def _clean_sub(**kw):
    return dict(submission(), cup=113, date='2026-11-14', **kw)


CLEAN = {'players': 20, 'warnings': []}


def test_flags_clean_cup_has_none(tmp_path, monkeypatch):
    ctx = _flag_ctx(tmp_path, monkeypatch)
    names = 'Names to check\n\n- Korvoth\n- deadlleg: known in other comps as deadlleg'
    assert P.publish_flags(ctx, _clean_sub(), CLEAN, names) == []


@pytest.mark.parametrize('sub,summary,names,word', [
    (dict(cup=115), CLEAN, '', 'does not follow'),
    (dict(date='2026-11-13'), CLEAN, '', 'not a Saturday'),
    (dict(date='2026-10-31'), CLEAN, '', 'not after'),
    (dict(date='nonsense'), CLEAN, '', 'unreadable'),
    (dict(), {'players': 5, 'warnings': []}, '', 'only 5 players'),
    (dict(), {'players': 20, 'warnings': ['WARNING odd']}, '', 'WARNING odd'),
    (dict(), CLEAN, '- [KBW]micro: possible alias of micro', 'possible alias'),
    (dict(mapper_note='mapper "x" looks like y'), CLEAN, '', 'looks like'),
    (dict(ruling=dict(submission('third_wins', winner='C')['ruling'])), CLEAN, '', 'abnormal'),
    (dict(ruling=dict(submission(lobby=2)['ruling'])), CLEAN, '', 'abnormal'),
])
def test_flags_each_reason(tmp_path, monkeypatch, sub, summary, names, word):
    ctx = _flag_ctx(tmp_path, monkeypatch)
    flags = P.publish_flags(ctx, dict(_clean_sub(), **sub), summary, names)
    assert any(word in f for f in flags), flags


def test_flags_same_result_as_last_cup(tmp_path, monkeypatch):
    ctx = _flag_ctx(tmp_path, monkeypatch, now=('A', 'B'))
    assert any('old log' in f for f in P.publish_flags(ctx, _clean_sub(), CLEAN, ''))


@pytest.mark.parametrize('flags,status,direct,pr', [
    ([], 'published', 1, 0),
    (['only 5 players'], 'processed', 0, 1),
])
def test_routing(tmp_path, monkeypatch, flags, status, direct, pr):
    ctx = SimpleNamespace(repo=str(tmp_path), subdir=str(tmp_path), python='python', branch='main',
                          dry_run=False, rehearsal=False, ci=True, state={}, exit_code=0,
                          save_state=lambda: None)
    statuses, calls = [], {'direct': 0, 'pr': 0}
    monkeypatch.setattr(P, 'resolve_mapper', lambda ctx, sub, path: (sub['mapper'], ''))
    monkeypatch.setattr(P, 'check_ruling', lambda *a: (None, ''))
    monkeypatch.setattr(P, 'post_status', lambda ctx, sid, status, *a: statuses.append(status))
    monkeypatch.setattr(P, 'collect_names', lambda ctx: 'Names to check')
    monkeypatch.setattr(P, 'publish_flags', lambda *a: flags)
    monkeypatch.setattr(P, 'publish_direct', lambda *a: calls.update(direct=calls['direct'] + 1))
    def open_pr(*a):
        calls['pr'] += 1
        return 'https://example.test/pr/1'
    monkeypatch.setattr(P, 'open_pr', open_pr)
    def run(*args, **kwargs):
        kwargs['stdout'].write(b'Eggy 110 COMPLETE\n')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(P.subprocess, 'run', run)
    P.run_cup(ctx, submission(), 'input.log')
    assert statuses == [status]
    assert (calls['direct'], calls['pr']) == (direct, pr)
    assert ctx.exit_code == 0


def test_publish_direct_real_git(tmp_path):
    origin, work = tmp_path / 'origin.git', tmp_path / 'work'
    subprocess.run(['git', 'init', '-q', '--bare', '-b', 'main', str(origin)], check=True)
    subprocess.run(['git', 'clone', '-q', str(origin), str(work)], check=True, capture_output=True)
    for args in (['config', 'user.name', 't'], ['config', 'user.email', 't@example.test'],
                 ['checkout', '-q', '-b', 'main']):
        _git(work, *args)
    (work / 'cups.json').write_text('{}', encoding='utf-8')
    _git(work, 'add', '.')
    _git(work, 'commit', '-q', '-m', 'start')
    _git(work, 'push', '-q', 'origin', 'main')
    (work / 'cups.json').write_text('{"new": 1}', encoding='utf-8')
    (work / 'cup_meta.json').write_text('{}', encoding='utf-8')
    (work / 'cup_110.json').write_text('{}', encoding='utf-8')
    (work / 'tracker_logs').mkdir()
    (work / 'tracker_logs/eggy_110.log').write_text('x', encoding='utf-8')
    (work / 'cup logs').mkdir()
    (work / 'cup logs/private.log').write_text('secret', encoding='utf-8')
    ctx = SimpleNamespace(repo=str(work), remote='origin', branch='main', rehearsal=False, dry_run=False)
    P.publish_direct(ctx, submission(), {'winner': 'W', 'players': 3}, 'Names to check')
    show = lambda *a: subprocess.run(['git', *a], cwd=origin, check=True, capture_output=True, text=True).stdout
    files = show('ls-tree', '-r', '--name-only', 'main').splitlines()
    assert 'cup_110.json' in files and 'tracker_logs/eggy_110.log' in files
    assert not any(f.startswith('cup logs/') for f in files)
    assert 'Names to check' in show('log', '-1', '--format=%B', 'main')


def _published(cup):
    return json.loads((ROOT / f'cup_{cup}.json').read_text(encoding='utf-8'))['players']


@pytest.mark.parametrize('log,cup,mapper,exclude,outcome,winner,lobby,agrees,expect', [
    # the same cup sent again with the same reading: on record, no flag
    ('eggy_109.log', 109, '[ZET] Wheelie', [], 'confirmed', None, None, True, 'matches the published result'),
    # the mapper left in as a player: one extra player, flagged
    ('eggy_109.log', 109, 'Wheelie', [], 'confirmed', None, None, False, '23 players (published 22)'),
    # a different ruling on a double-DNF final: different winner, flagged
    ('eggy_94_full.log', 94, '[ZET]vectortrajector', [], 'replay_winner', 'Kernkob', 1, False,
     'winner Kernkob (published SuperHeavy)'),
    ('eggy_94_full.log', 94, '[ZET]vectortrajector', [], 'third_wins', 'SuperHeavy', 1, True, 'matches'),
    # someone else's cup log sent under this cup number
    ('eggy_107.log', 109, '[ZET] Wheelie', [], 'confirmed', None, None, False, 'DIFFERS'),
    ('eggy_101_full.log', 101, 'Wheelie', [], 'confirmed', None, None, False, '2 cup starts'),
])
def test_compare_with_published(log, cup, mapper, exclude, outcome, winner, lobby, agrees, expect):
    sub = submission(outcome, winner, lobby)
    sub.update(mapper=mapper, exclude=exclude, cup=cup)
    ok, note = P.compare_with_published(sub, ROOT / 'cup logs' / log, _published(cup))
    assert ok is agrees, note
    assert expect in note, note


def _git(cwd, *args):
    subprocess.run(['git', *args], cwd=cwd, check=True, capture_output=True)


def test_published_cups_before_first_publish(tmp_path):
    # The state of main before the first cup is merged: pushed, but no
    # cup_meta.json. That must read as "nothing published", not as a failure
    # (every scheduled CI run died here, 2026-10-24 to 10-31).
    origin, work = tmp_path / 'origin.git', tmp_path / 'work'
    _git(tmp_path, 'init', '-q', '--bare', '-b', 'main', str(origin))
    _git(tmp_path, 'clone', '-q', str(origin), str(work))
    _git(work, 'config', 'user.email', 't@example.com')
    _git(work, 'config', 'user.name', 't')
    _git(work, 'checkout', '-q', '-b', 'main')
    (work / 'README.md').write_text('x', encoding='utf-8')
    _git(work, 'add', '.')
    _git(work, 'commit', '-q', '-m', 'init')
    _git(work, 'push', '-q', 'origin', 'main')
    ctx = SimpleNamespace(no_git=False, remote='origin', branch='main', repo=str(work), exit_code=0)
    assert P.published_cups(ctx) == set()
    assert ctx.exit_code == 0

    (work / 'cup_meta.json').write_text('{"Eggy 110": {}}', encoding='utf-8')
    _git(work, 'add', '.')
    _git(work, 'commit', '-q', '-m', 'publish')
    _git(work, 'push', '-q', 'origin', 'main')
    assert P.published_cups(ctx) == {'Eggy 110'}

    ctx_bad = SimpleNamespace(no_git=False, remote='nowhere', branch='main', repo=str(work), exit_code=0)
    assert P.published_cups(ctx_bad) is None
