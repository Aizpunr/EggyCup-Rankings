"""Catch up the PC after merging an online Eggy Cup PR. Never commits or pushes."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

BASE = Path(__file__).resolve().parent
REFRESH = Path(r"C:\Users\rafa\Desktop\Claude\zeepkist holistic\refresh.py")


def pull(no_pull=False):
    r = subprocess.run(['git', 'rev-parse', '--abbrev-ref', 'HEAD'], cwd=BASE,
                       capture_output=True, text=True)
    branch = r.stdout.strip()
    if r.returncode or branch != 'main':
        print(f'ERROR: on branch {branch!r}, not main; switch first.')
        return False
    if not no_pull and subprocess.run(['git', 'pull', '--ff-only'], cwd=BASE).returncode:
        print('ERROR: git pull --ff-only failed; sort it out first.')
        return False
    return True


def fetch_raw_logs():
    import submissions_poll as sp
    cfg_path = BASE / 'submit_config.json'
    if not cfg_path.exists():
        print('SKIP: no submit_config.json; raw logs not fetched.')
        return
    with cfg_path.open(encoding='utf-8') as f:
        cfg = json.load(f)
    ctx = SimpleNamespace(worker=cfg['worker_url'].rstrip('/'), token=cfg['poller_token'])
    meta = sp.load_json(BASE / 'cup_meta.json', {})
    try:
        subs = sp.api(ctx, 'GET', '/status').get('submissions', [])
    except sp.WorkerError:
        print('WARNING: Worker unreachable; raw logs not fetched.')
        return
    for sub in subs:
        cup = sub.get('cup')
        if type(cup) is not int or sub.get('status') not in ('processed', 'published') or f'Eggy {cup}' not in meta:
            continue
        dest = BASE / 'cup logs' / f'eggy_{cup}.log'
        if dest.exists():
            continue
        try:
            data, _ = sp.api(ctx, 'GET', '/log/' + sub['id'], raw=True, timeout=120)
            if sub.get('sha256') and hashlib.sha256(data).hexdigest() != sub['sha256']:
                print(f'WARNING: Eggy {cup} log checksum mismatch; not saved.')
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            print(f'Saved cup logs/eggy_{cup}.log')
        except (sp.WorkerError, OSError):
            print(f'WARNING: Eggy {cup} log not fetched.')


def refresh_external():
    try:
        r = subprocess.run([sys.executable, str(REFRESH)], cwd=REFRESH.parent, timeout=1800)
        if r.returncode:
            print('WARNING: cross-comp refresh failed; rerun later.')
    except (OSError, subprocess.TimeoutExpired):
        print('WARNING: cross-comp refresh unavailable or timed out; rerun later.')
    print('Saturday evenings: Spanish La Liga ISP blocks can time out the refresh; rerun later.')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--no-pull', action='store_true')
    ap.add_argument('--no-external', action='store_true')
    args = ap.parse_args()
    if not pull(args.no_pull):
        return 1
    fetch_raw_logs()
    path = BASE / 'cups.json'
    before = path.read_bytes() if path.exists() else None
    env = dict(os.environ, PYTHONIOENCODING='utf-8')
    env.pop('EGGY_SOF_DIR', None)
    r = subprocess.run([sys.executable, str(BASE / 'build_cups.py')], cwd=BASE, env=env)
    after = path.read_bytes() if path.exists() else None
    print('cups.json changed: commit + push cups.json when ready' if before != after else 'cups.json unchanged.')
    if not args.no_external:
        refresh_external()
    print(r'After any alias change: run python build_players_master.py in C:\Users\rafa\Desktop\Claude\zeepkist cotd elo')
    print('Push the SOF repo (elo_pool.json + docs/allcompdata.json) when wanted.')
    return 1 if r.returncode else 0


if __name__ == '__main__':
    sys.exit(main())
