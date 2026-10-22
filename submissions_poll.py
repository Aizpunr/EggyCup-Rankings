"""Process Eggy Cup submissions locally or as one pull request per cup with --ci.
State, lock, PAUSE and private downloads live in cup logs/submissions/.
Credentials: EGGY_WORKER_URL / EGGY_POLLER_TOKEN or submit_config.json.
"""
import argparse
import base64
import datetime
import hashlib
import json
import logging
import logging.handlers
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
HEADER = 'Doing eliminations with leaderboard'
LOCK_STALE_S = 2 * 3600
NEW_CUP_TIMEOUT_S = 1800

log = logging.getLogger('submissions_poll')


# ── config and paths ─────────────────────────────────────────────────

class Ctx:
    def __init__(self, args):
        cfg_path = os.path.join(HERE, 'submit_config.json')
        cfg = {}
        if os.path.exists(cfg_path):
            with open(cfg_path, encoding='utf-8') as f:
                cfg = json.load(f)
        self.ci = args.ci
        self.worker = (args.worker or os.environ.get('EGGY_WORKER_URL') or cfg.get('worker_url') or '').rstrip('/')
        self.token = os.environ.get('EGGY_POLLER_TOKEN') or cfg.get('poller_token') or ''
        self.repo = os.path.abspath(args.repo or cfg.get('repo_dir') or HERE)
        self.python = cfg.get('python') or sys.executable
        # pythonw.exe (Task Scheduler) has no console; new_cup.py needs a real
        # interpreter only for its own children, and python.exe works either way.
        if os.path.basename(self.python).lower() == 'pythonw.exe':
            self.python = os.path.join(os.path.dirname(self.python), 'python.exe')
        self.toast = cfg.get('toast', True) and not args.ci
        self.remote = cfg.get('git_remote', 'origin')
        self.branch = cfg.get('git_branch', 'main')
        self.dry_run = args.dry_run
        self.no_git = args.no_git
        self.only_id = args.id
        self.rehearsal = args.rehearsal
        self.exit_code = 0
        self.subdir = os.path.join(self.repo, 'cup logs', 'submissions')
        os.makedirs(self.subdir, exist_ok=True)
        self.state_path = os.path.join(self.subdir, 'state.json')
        self.state = load_json(self.state_path, {})

    def save_state(self):
        if self.dry_run:
            return
        tmp = self.state_path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(self.state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.state_path)


def load_json(path, default):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def setup_logging(subdir, verbose):
    log.setLevel(logging.DEBUG)
    fmt = logging.Formatter('%(asctime)s %(levelname)s %(message)s', '%Y-%m-%d %H:%M:%S')
    fh = logging.handlers.RotatingFileHandler(os.path.join(subdir, 'poll.log'), maxBytes=1_000_000,
                                              backupCount=3, encoding='utf-8')
    fh.setFormatter(fmt)
    fh.setLevel(logging.DEBUG if verbose else logging.INFO)
    log.addHandler(fh)
    if sys.stdout is not None:  # None under pythonw.exe
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except (AttributeError, ValueError):
            pass
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        sh.setLevel(logging.DEBUG if verbose else logging.INFO)
        log.addHandler(sh)


# ── lock ─────────────────────────────────────────────────────────────

def acquire_lock(path):
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, f'{os.getpid()} {time.time():.0f}'.encode())
            os.close(fd)
            return True
        except FileExistsError:
            try:
                started = float(open(path).read().split()[1])
            except (OSError, IndexError, ValueError):
                started = 0
            if time.time() - started < LOCK_STALE_S:
                return False
            log.warning('removing stale lock (%s)', path)
            try:
                os.remove(path)
            except OSError:
                return False
    return False


# ── Worker API ───────────────────────────────────────────────────────

class WorkerError(Exception):
    pass


def _ssl_context():
    """Prefer certifi's CA bundle: this PC's Windows store only has an expired
    cross-signed path to Let's Encrypt's newer chains, which workers.dev uses,
    so the default context fails with 'certificate has expired'."""
    import ssl
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


SSL_CTX = _ssl_context()


def api(ctx, method, path, body=None, raw=False, timeout=30):
    url = ctx.worker + path
    data = None
    headers = {'Authorization': 'Bearer ' + ctx.token, 'User-Agent': 'eggy-submissions-poll'}
    if body is not None:
        data = json.dumps(body).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as r:
            payload = r.read()
            if raw:
                return payload, dict(r.headers)
            return json.loads(payload.decode('utf-8'))
    except urllib.error.HTTPError as e:
        detail = e.read().decode('utf-8', 'replace')[:300]
        raise WorkerError(f'{method} {path}: HTTP {e.code} {detail}')
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise WorkerError(f'{method} {path}: {e}')


def post_status(ctx, sid, status, note='', summary=None):
    """Record the outcome locally first, then tell the Worker. A failed POST
    stays in state.json with posted=False and is retried next pass."""
    entry = ctx.state.setdefault(sid, {})
    entry.update({'status': status, 'note': note, 'summary': summary, 'posted': False,
                  'at': datetime.datetime.now().isoformat(timespec='seconds')})
    if ctx.dry_run:
        log.info('[dry-run] would POST /status/%s %s %s', sid, status, note)
        return
    ctx.save_state()
    body = {'status': status, 'note': note}
    if summary is not None:
        body['summary'] = summary
    try:
        api(ctx, 'POST', '/status/' + sid, body)
        entry['posted'] = True
        ctx.save_state()
        log.info('status %s -> %s %s', sid, status, note)
    except WorkerError as e:
        log.warning('could not post status for %s (will retry): %s', sid, e)
        ctx.exit_code = 1


def retry_unposted(ctx):
    for sid, entry in ctx.state.items():
        if sid.startswith('_'):
            continue
        if entry.get('posted') is False and entry.get('status'):
            try:
                body = {'status': entry['status'], 'note': entry.get('note', '')}
                if entry.get('summary') is not None:
                    body['summary'] = entry['summary']
                if ctx.dry_run:
                    log.info('[dry-run] would retry status %s', sid)
                    continue
                api(ctx, 'POST', '/status/' + sid, body)
                entry['posted'] = True
                log.info('retried status %s -> %s', sid, entry['status'])
            except WorkerError as e:
                log.warning('retry failed for %s: %s', sid, e)
                ctx.exit_code = 1
    ctx.save_state()


# ── git: which cups are published ────────────────────────────────────

def published_cups(ctx):
    if ctx.no_git:
        return None
    try:
        subprocess.run(['git', 'fetch', ctx.remote, '--quiet'], cwd=ctx.repo, timeout=60,
                       capture_output=True, check=True)
        r = subprocess.run(['git', 'show', f'{ctx.remote}/{ctx.branch}:cup_meta.json'], cwd=ctx.repo,
                           timeout=30, capture_output=True, check=True)
        return set(json.loads(r.stdout.decode('utf-8')).keys())
    except (subprocess.SubprocessError, OSError, ValueError) as e:
        log.warning('publish detection skipped: %s', e)
        ctx.exit_code = 1
        return None


# ── helpers ──────────────────────────────────────────────────────────

def count_blocks(path):
    try:
        with open(path, encoding='utf-8', errors='replace') as f:
            return sum(1 for line in f if 'COTDTracker' in line and HEADER in line)
    except OSError:
        return 0


def unsafe_args(sub):
    vals = [sub.get('mapper', '')] + ([sub['map']] if sub.get('map') else []) + list(sub.get('exclude') or [])
    for v in vals:
        if not isinstance(v, str) or not v.strip() or v.startswith('--') or re.search(r'[\r\n\0]', v):
            return f'unsafe argument {v!r}'
    for v in sub.get('exclude') or []:
        if ',' in v:
            return f'excluded name {v!r} contains a comma'
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(sub.get('date', ''))):
        return 'bad date'
    if type(sub.get('cup')) is not int or not 87 <= sub['cup'] <= 999:
        return 'bad cup number'
    return validate_ruling(sub.get('ruling'))


def download(ctx, sub):
    path = os.path.join(ctx.subdir, f"{sub['id']}_eggy_{sub['cup']}.log")
    if os.path.exists(path) and sha256_file(path) == sub.get('sha256'):
        return path
    data, headers = api(ctx, 'GET', '/log/' + sub['id'], raw=True, timeout=120)
    got = hashlib.sha256(data).hexdigest()
    want = sub.get('sha256') or headers.get('X-Sha256') or headers.get('x-sha256')
    if want and got != want:
        raise WorkerError(f"sha256 mismatch for {sub['id']}: {got} != {want}")
    with open(path, 'wb') as f:
        f.write(data)
    return path


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def toast(ctx, title, body):
    if not ctx.toast or ctx.dry_run:
        return
    ps = (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null;"
        "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null;"
        "$t = [System.Security.SecurityElement]::Escape('" + title.replace("'", "''") + "');"
        "$b = [System.Security.SecurityElement]::Escape('" + body.replace("'", "''") + "');"
        "$x = New-Object Windows.Data.Xml.Dom.XmlDocument;"
        "$x.LoadXml(\"<toast><visual><binding template='ToastGeneric'><text>$t</text><text>$b</text></binding></visual></toast>\");"
        "$n = [Windows.UI.Notifications.ToastNotification]::new($x);"
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
        "'{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe').Show($n);"
    )
    try:
        enc = base64.b64encode(ps.encode('utf-16-le')).decode('ascii')
        subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-EncodedCommand', enc],
                       timeout=20, capture_output=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except (subprocess.SubprocessError, OSError) as e:
        log.debug('toast failed: %s', e)


# ── CI: a branch and a pull request per cup ─────────────────────────

def git(ctx, *a, check=True):
    r = subprocess.run(['git', *a], cwd=ctx.repo, capture_output=True, text=True,
                       encoding='utf-8', errors='replace', timeout=120)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(a)}: {r.stderr.strip() or r.stdout.strip()}")
    return r.stdout.strip()


def cup_branch(cup):
    return f'cup-{cup}'


def remote_branch_exists(ctx, cup):
    return bool(git(ctx, 'ls-remote', '--heads', ctx.remote, cup_branch(cup)))


def open_cup_prs(ctx):
    r = subprocess.run(['gh', 'pr', 'list', '--state', 'open', '--base', ctx.branch,
                        '--json', 'headRefName'], cwd=ctx.repo, capture_output=True,
                       text=True, encoding='utf-8', timeout=60)
    if r.returncode:
        raise RuntimeError('could not check open cup pull requests')
    return [p['headRefName'] for p in json.loads(r.stdout) if re.fullmatch(r'cup-\d+', p['headRefName'])]


def fold_name(name):
    return ''.join(c for c in re.sub(r'\[[^\]]*\]', '', name.lower()) if c.isalnum())


def alldata_names(data):
    return {p['n'] for key in ('glicko', 'weighted') for p in data.get(key, []) if isinstance(p.get('n'), str)}


def names_to_check(old, new, players=None):
    before = alldata_names(old)
    lines = ['Names to check', '']
    for name in sorted(alldata_names(new) - before):
        hints = [f'possible alias of {n}' for n in sorted(before) if fold_name(n) == fold_name(name)]
        for canonical, info in (players or {}).items():
            if any(fold_name(n) == fold_name(name) for n in [canonical] + info.get('aliases', [])):
                hints.append(f'known in other comps as {canonical}')
        lines.append('- ' + name + (': ' + '; '.join(hints) if hints else ''))
    if len(lines) == 2:
        lines.append('- No new canonical names.')
    if players is None:
        lines.append('players.json not reachable')
    return '\n'.join(lines)


def collect_names(ctx):
    old = json.loads(git(ctx, 'show', f'{ctx.remote}/main:alldata.json'))
    new = load_json(os.path.join(ctx.repo, 'alldata.json'), {})
    try:
        url = 'https://raw.githubusercontent.com/Aizpunr/Zeepkist-COTD-Elo-Rankings/main/players.json'
        with urllib.request.urlopen(url, timeout=15, context=SSL_CTX) as response:
            players = json.load(response)
    except (OSError, ValueError):
        players = None
    return names_to_check(old, new, players)


def ruling_words(r):
    outcome = r['outcome']
    words = {
        'confirmed': 'Submitter confirmed the detected winner',
        'third_wins': f"Both finalists DNF, submitter says 3rd place {r.get('winner')} won (house rule)",
        'replay_winner': f"Both finalists DNF, submitter says {r.get('winner')} won the replay",
        'finish_by_times': 'Cup stopped with survivors left; rank by last-round times',
        'review': 'Needs manual review',
    }[outcome]
    if r.get('lobby') is not None:
        words += f"; lobby {r['lobby']}"
    if r.get('note'):
        words += '; ' + r['note']
    return words


def pr_body(sub, summary, names_section='Names to check\n\n- Not checked.'):
    lines = [f"Submitted by {sub.get('submitter') or 'someone'} (submission `{sub['id']}`).", '',
             f"- Date: {sub['date']}", f"- Map: {sub.get('map') or 'TBD'}",
             f"- Mapper: {sub['mapper']}", '- Exclusions: ' + (', '.join(sub.get('exclude') or []) or 'none'),
             '- Ruling: ' + ruling_words(sub['ruling']),
             '- Top 3: ' + ', '.join(p['name'] for p in summary.get('podium', [])),
             f"- Player count: {summary.get('players')}"]
    lines += ['', 'Warnings', ''] + (['- ' + w for w in summary.get('warnings', [])] or ['- None.'])
    lines += ['', names_section, '', 'No livelog: no Steam ID check and no left-the-game pass. Raw log stays private in the Worker.']
    return '\n'.join(lines)


def staging_paths(repo, cup):
    import glob
    return ['cup_meta.json', f'cup_{cup}.json', 'tracker_logs/'] + [os.path.basename(p) for p in sorted(glob.glob(os.path.join(repo, 'Eggy Cup *.xlsx')))]


def open_pr(ctx, sub, summary):
    """Commit the pipeline's output on cup-<N>, push it, open the PR. Returns its URL."""
    cup, branch = sub['cup'], cup_branch(sub['cup'])
    title = f"Eggy {cup}: {sub.get('map') or 'TBD'} by {sub['mapper']}"
    if ctx.rehearsal or ctx.dry_run:
        raise RuntimeError('publication disabled in rehearsal or dry-run')
    names_section = collect_names(ctx)
    git(ctx, 'checkout', '-b', branch)
    # Exclude even accidentally tracked private raw logs from update staging.
    git(ctx, 'add', '-u', '--', '.', ':(exclude)cup logs/**')
    git(ctx, 'add', '--', *staging_paths(ctx.repo, cup))
    msg = (f"{title}\n\n{summary.get('winner')} wins, {summary.get('players')} players. "
           f"Processed online from a log submitted by {sub.get('submitter') or 'someone'}.")
    git(ctx, 'commit', '-m', msg)
    git(ctx, 'push', ctx.remote, branch)
    body_path = os.path.join(ctx.subdir, sub['id'] + '.pr-body.txt')
    with open(body_path, 'w', encoding='utf-8', newline='\n') as body:
        body.write(pr_body(sub, summary, names_section))
    r = subprocess.run(['gh', 'pr', 'create', '--base', ctx.branch, '--head', branch,
                        '--title', title, '--body-file', body_path],
                       cwd=ctx.repo, capture_output=True, text=True, encoding='utf-8', timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f'gh pr create: {r.stderr.strip()}')
    return r.stdout.strip().splitlines()[-1]


# ── running new_cup.py ───────────────────────────────────────────────

def validate_ruling(r):
    if not isinstance(r, dict):
        return 'missing or invalid ruling'
    if r.get('outcome') not in ('confirmed', 'third_wins', 'replay_winner', 'finish_by_times', 'review'):
        return 'invalid ruling outcome'
    lobby = r.get('lobby')
    if 'lobby' not in r or (lobby is not None and (type(lobby) is not int or not 1 <= lobby <= 9)):
        return 'invalid ruling lobby'
    winner = r.get('winner')
    if r['outcome'] in ('third_wins', 'replay_winner'):
        if not isinstance(winner, str) or not 1 <= len(winner) <= 40 or winner.startswith('--') or re.search(r'[\r\n\0,]', winner):
            return 'invalid ruling winner'
    elif winner is not None:
        return 'unexpected ruling winner'
    for key, limit in [('note', 200), ('detected', 20)]:
        value = r.get(key)
        if value is not None and (not isinstance(value, str) or len(value) > limit):
            return 'invalid ruling ' + key
        if key == 'note' and value is not None and (value.startswith('--') or re.search(r'[\r\n\0]', value)):
            return 'invalid ruling note'
    return None


def check_ruling(sub, log_path):
    """Return (status, note), or (None, '') when safe to process."""
    import eggy_parser as P
    r = sub.get('ruling')
    problem = validate_ruling(r)
    if problem:
        return 'failed', problem
    if r['outcome'] == 'review':
        return 'review', r.get('note') or 'submitter requested review'
    with open(log_path, encoding='utf-8', errors='replace') as f:
        lines = P.filter_tracker_lines(f.readlines())
    lobbies = P.split_lobbies(lines)
    if r['lobby'] is None and len(lobbies) > 1:
        return 'failed', f'log has {len(lobbies)} cup starts; the submitter must pick one'
    if r['lobby'] is not None:
        if r['lobby'] > len(lobbies):
            return 'failed', f"lobby {r['lobby']} does not exist; log has {len(lobbies)} cup starts"
        lines = lobbies[r['lobby'] - 1]
    excluded = {sub['mapper']} | set(sub.get('exclude') or [])
    detected = P.detect_outcome(lines, excluded)
    kind, outcome = detected['kind'], r['outcome']
    matches = ((outcome == 'confirmed' and kind in ('single', 'marker')) or
               (outcome == 'third_wins' and kind == 'double_dnf_final' and r['winner'] == detected.get('third')) or
               (outcome == 'replay_winner' and kind == 'double_dnf_final' and r['winner'] in detected.get('finalists', [])) or
               (outcome == 'finish_by_times' and kind == 'survivors_left'))
    if not matches:
        return 'review', f'ruling {outcome} does not match the log ({kind})'
    return None, ''


def compare_with_published(sub, log_path, published):
    """Parse a second log for an already processed cup with ITS submitter's
    mapper, exclusions and ruling, and compare with the published cup_N.json.
    Returns (agrees, note). A disagreement is how mistakes and abuse surface."""
    import eggy_parser as P
    if not published:
        return False, 'published result not found to compare'
    r = sub.get('ruling') or {}
    if validate_ruling(r):
        return False, 'could not compare: invalid ruling'
    if r['outcome'] == 'review':
        return False, 'submitter asked for a review: ' + (r.get('note') or 'no note')
    try:
        with open(log_path, encoding='utf-8', errors='replace') as f:
            lines = P.filter_tracker_lines(f.readlines())
        lobbies = P.split_lobbies(lines)
        if r['lobby'] is not None:
            lines = lobbies[r['lobby'] - 1]
        elif len(lobbies) > 1:
            return False, f'could not compare: {len(lobbies)} cup starts and no lobby picked'
        parsed = P.parse_cup_log(lines, {sub['mapper']} | set(sub.get('exclude') or []),
                                 manual_winner=r.get('winner'),
                                 finish_by_times=r['outcome'] == 'finish_by_times')
    except (OSError, IndexError, P.ParseError) as e:
        return False, f'could not compare: {e}'
    got = [row[0] for row in parsed.leaderboard]
    want = [p['name'] for p in published]
    diffs = []
    if got[:1] != want[:1]:
        diffs.append(f"winner {got[0]} (published {want[0]})")
    elif got[:3] != want[:3]:
        diffs.append(f"top 3 {', '.join(got[:3])} (published {', '.join(want[:3])})")
    if len(got) != len(want):
        diffs.append(f'{len(got)} players (published {len(want)})')
    if not diffs:
        return True, 'matches the published result'
    return False, 'DIFFERS from the published result: ' + '; '.join(diffs)


def new_cup_command(ctx, sub, log_path):
    r = sub['ruling']
    if r['outcome'] == 'review':
        raise ValueError('review rulings cannot be processed')
    cmd = [ctx.python, os.path.join(ctx.repo, 'new_cup.py'), str(sub['cup']), sub['mapper'],
           '--date', sub['date'], '--log', log_path]
    if sub.get('map'):
        cmd += ['--map', sub['map']]
    if sub.get('exclude'):
        cmd += ['--exclude', ','.join(sub['exclude'])]
    if r.get('lobby') is not None:
        cmd += ['--lobby', str(r['lobby'])]
    if r['outcome'] in ('third_wins', 'replay_winner'):
        cmd += ['--winner', r['winner']]
    if r['outcome'] == 'finish_by_times':
        cmd += ['--finish-by-times']
    return cmd


def summarize(ctx, cup, out_text):
    players = load_json(os.path.join(ctx.repo, f'cup_{cup}.json'), {}).get('players', [])
    return {'winner': players[0]['name'] if players else None,
            'podium': [{'name': p['name'], 'time': p.get('time')} for p in players[:3]],
            'players': len(players), 'rounds': max((p.get('round') or 0 for p in players), default=0),
            'warnings': [line for line in out_text.splitlines() if line.startswith('WARNING')][:20]}


def run_cup(ctx, sub, log_path):
    sid, cup = sub['id'], sub['cup']
    try:
        status, note = check_ruling(sub, log_path)
    except (OSError, ValueError) as e:
        status, note = 'failed', f'could not check ruling: {e}'
    if status:
        post_status(ctx, sid, status, note)
        if status == 'failed':
            ctx.exit_code = 1
        return
    cmd = new_cup_command(ctx, sub, log_path)
    log.info('running: %s', subprocess.list2cmdline(cmd))
    if ctx.dry_run:
        log.info('[dry-run] new_cup.py not run')
        return
    out_path = os.path.join(ctx.subdir, f'{sid}.out')
    env = dict(os.environ, PYTHONIOENCODING='utf-8')
    ctx.state.setdefault(sid, {})['started'] = datetime.datetime.now().isoformat(timespec='seconds')
    ctx.save_state()
    try:
        with open(out_path, 'wb') as out:
            r = subprocess.run(cmd, cwd=ctx.repo, stdout=out, stderr=subprocess.STDOUT, env=env,
                               timeout=NEW_CUP_TIMEOUT_S,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        rc = r.returncode
    except subprocess.TimeoutExpired:
        post_status(ctx, sid, 'failed', f'new_cup.py timed out after {NEW_CUP_TIMEOUT_S // 60} min; aizpun must check it')
        toast(ctx, f'Eggy {cup} FAILED', 'new_cup.py timed out')
        ctx.exit_code = 1
        return
    with open(out_path, encoding='utf-8', errors='replace') as f:
        out_text = f.read()

    if rc == 0 and f'Eggy {cup} COMPLETE' in out_text.splitlines():
        summary = summarize(ctx, cup, out_text)
        if ctx.ci and not ctx.rehearsal:
            try:
                url = open_pr(ctx, sub, summary)
            except (RuntimeError, subprocess.SubprocessError, OSError) as e:
                post_status(ctx, sid, 'failed', f'processed, but the pull request failed: {e}'[:500])
                ctx.exit_code = 1
                return
            post_status(ctx, sid, 'processed', f'awaiting publish: {url}', summary)
            log.info('pull request: %s', url)
            return
        post_status(ctx, sid, 'processed', 'awaiting publish', summary)
        who = sub.get('submitter') or 'someone'
        toast(ctx, f'Eggy {cup} processed',
              f"Sent by {who}. Winner {summary['winner']}, {summary['players']} players. Verify localhost:9004, then push.")
        return

    errors = [l.strip() for l in out_text.splitlines() if l.strip().startswith('ERROR')]
    if errors:
        note = errors[-1]
    else:
        tail = [l.strip() for l in out_text.splitlines() if l.strip()][-3:]
        note = ' | '.join(tail) or f'new_cup.py exited with code {rc}'
    note = note[:500]
    if 'is already processed' in note and ctx.state.get(sid, {}).get('status') == 'processed':
        post_status(ctx, sid, 'processed', 'awaiting publish', ctx.state[sid].get('summary'))
        return
    post_status(ctx, sid, 'failed', note)
    toast(ctx, f'Eggy {cup} FAILED', note[:200])
    ctx.exit_code = 1


# ── one pass ─────────────────────────────────────────────────────────

def one_pass(ctx, args):
    if os.path.exists(os.path.join(ctx.subdir, 'PAUSE')):
        log.info('PAUSE marker present, skipping this pass')
        return 0
    if not ctx.worker or not ctx.token:
        log.error('worker_url / poller_token missing: create submit_config.json (see submit-worker/README.md)')
        return 1
    lock = os.path.join(ctx.subdir, '.lock')
    if not ctx.dry_run and not acquire_lock(lock):
        log.info('another pass is running, exiting')
        return 0
    try:
        return _pass(ctx, args)
    finally:
        if not ctx.dry_run:
            try:
                os.remove(lock)
            except OSError:
                pass


def _pass(ctx, args):
    # Rebuild the Worker's index once a day (after 04:00) as a sweep for
    # records a concurrent write may have dropped from it; or on --reindex.
    meta = ctx.state.setdefault('_meta', {})
    today = datetime.date.today().isoformat()
    if args.reindex or (meta.get('last_reindex') != today and datetime.datetime.now().hour >= 4):
        try:
            if ctx.dry_run:
                log.info('[dry-run] would POST /reindex')
            else:
                log.info('reindex: %s', api(ctx, 'POST', '/reindex'))
                meta['last_reindex'] = today
                ctx.save_state()
        except WorkerError as e:
            log.warning('reindex failed: %s', e)
            ctx.exit_code = 1

    retry_unposted(ctx)
    try:
        pending = api(ctx, 'GET', '/pending').get('submissions', [])
    except WorkerError as e:
        log.warning('worker unreachable, nothing done: %s', e)
        return 1
    if ctx.only_id:
        pending = [s for s in pending if s['id'] == ctx.only_id]
    log.debug('%d pending submission(s)', len(pending))
    if not pending:
        return 0

    local_meta = set(load_json(os.path.join(ctx.repo, 'cup_meta.json'), {}).keys())
    remote_meta = published_cups(ctx)
    if ctx.ci and not ctx.no_git and remote_meta is None:
        return 1
    try:
        open_prs = open_cup_prs(ctx) if ctx.ci else []
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as e:
        log.error('PR check failed: %s', e)
        return 1

    # processed -> published once the cup is on the remote branch
    for sub in pending:
        if sub['status'] == 'processed' and remote_meta is not None and f"Eggy {sub['cup']}" in remote_meta:
            post_status(ctx, sub['id'], 'published', '', sub.get('summary'))

    received = [s for s in pending if s['status'] == 'received'
                and ctx.state.get(s['id'], {}).get('status') not in ('processed', 'failed', 'duplicate', 'superseded', 'review')]
    if not received:
        return 0

    received = [s for s in received if (s.get('ruling') or {}).get('outcome') != 'review']
    by_cup = {}
    for sub in sorted(received, key=lambda s: s['created']):
        by_cup.setdefault(sub['cup'], []).append(sub)

    ran = False
    for cup in sorted(by_cup):
        subs = by_cup[cup]
        blocks = {}
        paths = {}
        for sub in subs:
            try:
                paths[sub['id']] = download(ctx, sub)
            except (WorkerError, OSError) as e:
                log.warning('download failed for %s: %s', sub['id'], e)
                ctx.exit_code = 1
                paths.pop(sub['id'], None)
                continue
            blocks[sub['id']] = count_blocks(paths[sub['id']])
        subs = [s for s in subs if s['id'] in paths]
        if not subs:
            continue

        cup_id = f'Eggy {cup}'
        if cup_id in local_meta or (remote_meta is not None and cup_id in remote_meta):
            existing = os.path.join(ctx.repo, 'tracker_logs', f'eggy_{cup}.log')
            if not os.path.exists(existing):
                existing = os.path.join(ctx.repo, 'cup logs', f'eggy_{cup}.log')
            have = count_blocks(existing) if os.path.exists(existing) else 0
            published = load_json(os.path.join(ctx.repo, f'cup_{cup}.json'), {}).get('players')
            for sub in subs:
                n = blocks[sub['id']]
                agrees, comparison = compare_with_published(sub, paths[sub['id']], published)
                note = (f'{cup_id} is already processed ({n} leaderboards in this log vs {have} '
                        f'in the one used); {comparison}')
                if n > have or not agrees:
                    note = 'REVIEW: ' + note
                    toast(ctx, f'{cup_id}: a second log needs a look', note)
                post_status(ctx, sub['id'], 'duplicate', note[:500])
            continue

        best = max(subs, key=lambda s: (blocks[s['id']], -subs.index(s)))
        for sub in subs:
            if sub is not best:
                post_status(ctx, sub['id'], 'superseded',
                            f"superseded by {best['id']} ({blocks[best['id']]} vs {blocks[sub['id']]} leaderboards)")

        problem = unsafe_args(best)
        if problem:
            post_status(ctx, best['id'], 'failed', f'{problem}; aizpun must process this cup by hand')
            ctx.exit_code = 1
            continue

        if ran:
            log.info('%s waits for the next pass (one cup per pass)', cup_id)
            continue
        if ctx.ci:
            if open_prs:
                log.info('%s waits for open pull request(s): %s', cup_id, ', '.join(open_prs))
                continue
            if remote_branch_exists(ctx, cup):
                log.warning('%s: branch %s already exists; its pull request is the result', cup_id, cup_branch(cup))
                continue
            waiting_on = sorted(s['cup'] for s in pending if s['status'] == 'processed' and (remote_meta is None or f"Eggy {s['cup']}" not in remote_meta))
            if waiting_on:
                log.info('%s waits: merge the pull request for Eggy %s first', cup_id, waiting_on[0])
                continue
        run_cup(ctx, best, paths[best['id']])
        ran = True
        local_meta = set(load_json(os.path.join(ctx.repo, 'cup_meta.json'), {}).keys())
    return 0


def main():
    ap = argparse.ArgumentParser(description='Process cups submitted on submit.html.')
    ap.add_argument('--once', action='store_true', help='one pass (the default)')
    ap.add_argument('--loop', type=int, metavar='SECONDS', help='repeat every N seconds (development)')
    ap.add_argument('--dry-run', action='store_true', help='download and decide, print the commands, change nothing')
    ap.add_argument('--repo', help='repo to run new_cup.py in (default: this folder)')
    ap.add_argument('--worker', help='override worker_url')
    ap.add_argument('--id', help='only handle this submission id')
    ap.add_argument('--no-git', action='store_true', help='skip git fetch / publish detection')
    ap.add_argument('--reindex', action='store_true', help='ask the Worker to rebuild its index first')
    ap.add_argument('--rehearsal', action='store_true',
                    help='process locally; never push or open a pull request, including with --ci')
    ap.add_argument('--ci', action='store_true',
                    help='GitHub Action mode: config from the environment, a pull request per cup')
    ap.add_argument('-v', '--verbose', action='store_true')
    args = ap.parse_args()

    ctx = Ctx(args)
    setup_logging(ctx.subdir, args.verbose)
    if args.rehearsal and os.path.isdir(os.path.join(ctx.repo, '.git')):
        log.warning('--rehearsal in a git checkout (%s): the cup is still '
                    'processed in this repo; no branches are pushed or PRs opened', ctx.repo)

    if args.loop:
        while True:
            one_pass(ctx, args)
            time.sleep(args.loop)
    try:
        rc = one_pass(ctx, args)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as e:
        log.error('poll failed: %s', e)
        rc = 1
    sys.exit(rc or ctx.exit_code)


if __name__ == '__main__':
    main()
