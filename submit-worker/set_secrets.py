"""
set_secrets.py — set the eggy-submit Worker secrets without ever printing them.

Run from anywhere, with the Turnstile widget's SECRET key on the clipboard:
    python submit-worker/set_secrets.py

1. TURNSTILE_SECRET = the clipboard (trimmed), stored with `wrangler secret put`
   through stdin. Never pipe secrets in PowerShell 5: the pipe adds a trailing
   line break and the stored secret no longer matches (TyO deploy, 2026-10-22).
2. POLLER_TOKEN = a fresh random token (never reuse another comp's), stored the
   same way and written to the gitignored ../submit_config.json for catch_up.py.
3. Checks GET /pending with the token answers 200.
4. Leaves the token on the clipboard for the GitHub secret EGGY_POLLER_TOKEN.

--poller-only skips step 1 (e.g. to rotate the token).
"""
import json
import os
import secrets
import subprocess
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
WORKER_URL = 'https://eggy-submit.cotd-submit.workers.dev'
CONFIG = os.path.join(REPO, 'submit_config.json')


def put_secret(name, value):
    r = subprocess.run('npx wrangler secret put ' + name, input=value, text=True,
                       shell=True, cwd=HERE, capture_output=True)
    if r.returncode != 0:
        sys.exit(f'wrangler secret put {name} failed:\n{r.stderr[-800:]}')
    print(f'{name} stored in the Worker.')


def clipboard():
    r = subprocess.run(['powershell', '-NoProfile', '-Command', 'Get-Clipboard -Raw'],
                       capture_output=True, text=True)
    return r.stdout.strip()


def main():
    if '--poller-only' not in sys.argv:
        value = clipboard()
        if not value.startswith('0x') or len(value) < 20 or any(c.isspace() for c in value):
            sys.exit('The clipboard does not look like a Turnstile secret key (0x..., no spaces). '
                     'Copy it from Cloudflare > Turnstile > the widget > Settings, then rerun.')
        put_secret('TURNSTILE_SECRET', value)

    token = secrets.token_urlsafe(32)
    put_secret('POLLER_TOKEN', token)

    cfg = {}
    if os.path.exists(CONFIG):
        with open(CONFIG, encoding='utf-8') as f:
            cfg = json.load(f)
    cfg.update(worker_url=WORKER_URL, poller_token=token)
    with open(CONFIG, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, indent=2)
    print(f'Token written to {CONFIG} (gitignored).')

    req = urllib.request.Request(WORKER_URL + '/pending', headers={
        'Authorization': 'Bearer ' + token, 'User-Agent': 'eggy-set-secrets'})
    try:
        import certifi, ssl
        ctx = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        ctx = None
    try:
        with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
            print(f'/pending with the new token: HTTP {resp.status} (good).')
    except urllib.error.HTTPError as e:
        sys.exit(f'/pending answered HTTP {e.code}. A new secret can take a few seconds to '
                 f'reach the edge; rerun with --poller-only if it stays 401.')

    subprocess.run(['clip'], input=token, text=True, check=True)
    print('The poller token is now on your clipboard: paste it as the GitHub secret '
          'EGGY_POLLER_TOKEN (Settings > Secrets and variables > Actions).')


if __name__ == '__main__':
    main()
