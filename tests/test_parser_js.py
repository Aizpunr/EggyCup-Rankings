"""Compare the JavaScript port against Python on every archived log."""
import json
import shutil
import subprocess
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import eggy_parser as P

NODE = shutil.which('node')
pytestmark = pytest.mark.skipif(NODE is None, reason='node not installed')


def run_node(*args):
    result = subprocess.run([NODE, '--preserve-symlinks', '--preserve-symlinks-main', *map(str, args)], cwd=ROOT, capture_output=True,
                            encoding='utf-8', errors='replace', timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr


def test_js_fixtures(tmp_path):
    manifest = json.loads((ROOT / 'tests/fixtures/manifest.json').read_text(encoding='utf-8'))
    entries = [(f'manifest/{key}', entry) for key, entry in manifest.items()]
    entries += [(f'raw/{p.name}', {'log': p.name, 'exclude': []})
                for p in sorted((ROOT / 'cup logs').glob('*.log'))
                if 'liveleaderboard' not in p.name]
    cases = []
    for name, entry in entries:
        path = ROOT / 'cup logs' / entry['log']
        with path.open(encoding='utf-8', errors='replace') as f:
            lines = P.filter_tracker_lines(f.readlines())
        if entry.get('lobby'):
            lines = P.split_lobbies(lines)[entry['lobby'] - 1]
        excluded = entry['exclude']
        try:
            parsed = P.parse_cup_log(lines, excluded, manual_winner=entry.get('winner'),
                                     finish_by_times=entry.get('finish_by_times', False))
            result = {'payload': P.cup_json_payload(parsed, 0, 'TBD')}
        except P.ParseError as e:
            result = {'error': str(e)}
        result['outcome'] = P.detect_outcome(lines, excluded)
        cases.append({'name': name, 'path': str(path), 'exclude': excluded,
                      'lobby': entry.get('lobby'),
                      'opts': {'manualWinner': entry.get('winner'),
                               'finishByTimes': entry.get('finish_by_times', False)},
                      'expected': result})
    case_file = tmp_path / 'cases.json'
    case_file.write_text(json.dumps(cases, ensure_ascii=False), encoding='utf-8')
    run_node('tests/js/run_fixtures.mjs', case_file)


def test_js_rules():
    run_node('tests/js/test_rules.mjs')
