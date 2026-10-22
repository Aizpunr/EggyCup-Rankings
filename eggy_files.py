"""
eggy_files.py — where the Eggy results workbook lives.

Exactly one "Eggy Cup A-B.xlsx" sits in the repo root; new_cup.py renames it
as the range grows. Readers find it here instead of new_cup.py rewriting a
filename literal inside elo_engine.py.
"""
import glob
import os
import re

ROOT = os.path.dirname(os.path.abspath(__file__))
XLSX_RE = re.compile(r'^Eggy Cup (\d+)-(\d+)\.xlsx$')


def xlsx_path():
    found = [p for p in glob.glob(os.path.join(ROOT, 'Eggy Cup *-*.xlsx'))
             if XLSX_RE.match(os.path.basename(p))]
    if len(found) != 1:
        raise SystemExit(f'ERROR: expected exactly one "Eggy Cup A-B.xlsx" in {ROOT}, '
                         f'found {len(found)}: {[os.path.basename(p) for p in found]}')
    return found[0]


def xlsx_range(path):
    m = XLSX_RE.match(os.path.basename(path))
    return int(m.group(1)), int(m.group(2))
