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
    monkeypatch.setattr(P, 'check_ruling', lambda *a: (None, ''))
    monkeypatch.setattr(P, 'post_status', lambda ctx, sid, status, *a: statuses.append(status))
    monkeypatch.setattr(P, 'toast', lambda *a: None)
    monkeypatch.setattr(P, 'open_pr', lambda *a: pytest.fail('rehearsal must never publish'))
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
    monkeypatch.setattr(P, 'check_ruling', lambda *a: ('review', 'mismatch'))
    monkeypatch.setattr(P, 'post_status', lambda ctx, sid, status, *a: statuses.append(status))
    monkeypatch.setattr(P.subprocess, 'run', lambda *a, **kw: pytest.fail('mismatched ruling ran pipeline'))
    P.run_cup(ctx, submission(), 'input.log')
    assert statuses == ['review']


def test_open_pr_guard():
    with pytest.raises(RuntimeError, match='publication disabled'):
        P.open_pr(SimpleNamespace(rehearsal=True, dry_run=False), submission(), {})


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
