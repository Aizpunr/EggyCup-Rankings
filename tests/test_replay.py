"""Golden replay: every archived cup log, parsed with the flags it was processed
with (tests/fixtures/manifest.json), must reproduce the committed cup_N.json
byte for byte. Entries with a 'skip' reason were hand-patched after parsing."""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import eggy_parser as P  # noqa: E402

with open(os.path.join(ROOT, 'tests', 'fixtures', 'manifest.json'), encoding='utf-8') as f:
    MANIFEST = json.load(f)


def load_lines(entry):
    with open(os.path.join(ROOT, 'cup logs', entry['log']), encoding='utf-8', errors='replace') as f:
        lines = P.filter_tracker_lines(f.readlines())
    if entry.get('lobby'):
        lines = P.split_lobbies(lines)[entry['lobby'] - 1]
    return lines


def parse_entry(entry):
    return P.parse_cup_log(load_lines(entry), entry['exclude'],
                           manual_winner=entry.get('winner'),
                           finish_by_times=entry.get('finish_by_times', False))


@pytest.mark.parametrize('key', sorted(MANIFEST))
def test_replay(key):
    entry = MANIFEST[key]
    if entry.get('skip'):
        pytest.skip(entry['skip'])
    with open(os.path.join(ROOT, entry['expected']), encoding='utf-8') as f:
        raw = f.read()
    want = json.loads(raw)
    got = P.cup_json_payload(parse_entry(entry), want['cup_num'], entry['mapper'],
                             label=want['cup'] if want['cup'] != f"Eggy {want['cup_num']}" else None)
    assert json.dumps(got, ensure_ascii=False, indent=2) == raw


# How each abnormal cup is classified before any ruling is applied. This is what
# the submit page asks the submitter about.
OUTCOMES = {
    '94': ('double_dnf_final', {'third': 'SuperHeavy'}),
    '103': ('double_dnf_final', {'third': 'Jake'}),
    '105': ('double_dnf_final', {'third': 'Minkus'}),
    '96': ('survivors_left', {}),
    '93': ('marker', {}),
    '109': ('single', {}),
}


@pytest.mark.parametrize('key', sorted(OUTCOMES))
def test_outcome(key):
    entry = MANIFEST[key]
    kind, extra = OUTCOMES[key]
    out = P.detect_outcome(load_lines(entry), entry['exclude'])
    assert out['kind'] == kind
    for k, v in extra.items():
        assert out[k] == v


@pytest.mark.parametrize('log,n', [('eggy_94_full.log', 2), ('eggy_101_full.log', 2)])
def test_multi_start(log, n):
    with open(os.path.join(ROOT, 'cup logs', log), encoding='utf-8', errors='replace') as f:
        lines = P.filter_tracker_lines(f.readlines())
    out = P.detect_outcome(lines)
    assert out['kind'] == 'multi_start'
    assert len(out['lobbies']) == n


def test_host_dc_full_log_goes_to_review():
    # Cup 96 full log: the bugged round after the host DC is all DNF with 3 players,
    # which is not a final. It must not look like a normal double-DNF final.
    with open(os.path.join(ROOT, 'cup logs', 'eggy_96_full.log'), encoding='utf-8', errors='replace') as f:
        lines = P.filter_tracker_lines(f.readlines())
    out = P.detect_outcome(lines, MANIFEST['96']['exclude'])
    assert out['kind'] == 'double_dnf_final'
    assert len(out['finalists']) == 3 and out['third'] is None
