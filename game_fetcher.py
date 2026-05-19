"""
game_fetcher.py — download games into the local games directory.

STATUS
------
Chess.com  ✓  production — saves as YYYY_MM.json, skips cached months
Lichess    ⚠  WIP — normalization implemented, not yet end-to-end tested;
               username validation and ECOUrl injection are in place but
               the full analysis pipeline has not been verified against
               real Lichess data

Directory convention (auto-derived by games_dir_for):
  games_{username}_{source}
  e.g.  games_jf4bes_chesscom/   games_jforbes94_lichess/

Both file formats are read transparently by load_games in lesson_runner.py.
exclude_months matches on the YYYY_MM portion so it works for both prefixes.
"""

import json
import os
import re
import time
from datetime import datetime, timezone

try:
    import requests
except ImportError:
    requests = None


def _require_requests():
    if requests is None:
        raise ImportError("pip install requests")


# ── Chess.com ─────────────────────────────────────────────────────────────────

_CC_HEADERS = {'User-Agent': 'chess-analysis/1.0'}


def fetch_chesscom(config, games_dir=None):
    _require_requests()
    games_dir = games_dir or config['games_dir']
    os.makedirs(games_dir, exist_ok=True)
    username = config['username']

    archives_url = f"https://api.chess.com/pub/player/{username}/games/archives"
    r = requests.get(archives_url, headers=_CC_HEADERS)
    r.raise_for_status()
    archives = r.json().get('archives', [])

    new_count = 0
    for archive_url in archives:
        parts = archive_url.rstrip('/').split('/')
        year, month = parts[-2], parts[-1]
        fname = os.path.join(games_dir, f'{year}_{month}.json')
        if os.path.exists(fname):
            continue
        print(f'  Chess.com: {year}/{month} ...', end=' ')
        resp = requests.get(archive_url, headers=_CC_HEADERS)
        if resp.status_code == 404:
            print('(not found)')
            continue
        resp.raise_for_status()
        games = resp.json().get('games', [])
        with open(fname, 'w', encoding='utf-8') as f:
            json.dump(games, f)
        print(f'{len(games)} games')
        new_count += 1
        time.sleep(0.3)

    cached = len(archives) - new_count
    print(f'Chess.com done: {new_count} new month(s), {cached} already cached')


# ── Lichess (WIP — not yet end-to-end tested) ────────────────────────────────

_LICHESS_PERF = {
    'bullet': 'bullet', 'blitz': 'blitz',
    'rapid': 'rapid',   'classical': 'classical',
}


def _lichess_result(status, winner, is_white):
    """Map Lichess status/winner to a Chess.com-style result string for one player."""
    my_color = 'white' if is_white else 'black'
    if winner == my_color:
        return 'win'
    if winner is None:  # draw
        return {
            'stalemate': 'stalemate', 'repetition': 'repetition',
            'insufficient': 'insufficient', 'fiftyMoves': '50move',
        }.get(status, 'agreed')
    # loss
    return {
        'outoftime': 'timeout', 'aborted': 'abandoned',
        'noStart': 'abandoned', 'mate': 'checkmated',
    }.get(status, 'resigned')


def _opening_to_eco_url(opening):
    """Convert Lichess opening dict to a Chess.com ECOUrl-style path string."""
    if not opening:
        return ''
    name = opening.get('name', '')
    slug = re.sub(r"[',.]", '', name)
    slug = re.sub(r'[:\s/]+', '-', slug)
    slug = re.sub(r'-+', '-', slug).strip('-')
    return f"https://www.chess.com/openings/{slug}"


def _inject_eco_url(pgn_str, eco_url):
    """Insert [ECOUrl "..."] into PGN if not already present."""
    if not eco_url or '[ECOUrl' in pgn_str:
        return pgn_str
    tag = f'[ECOUrl "{eco_url}"]\n'
    if '[ECO "' in pgn_str:
        return re.sub(r'(\[ECO "[^"]*"\]\n?)', r'\1' + tag, pgn_str, count=1)
    return tag + pgn_str


def fetch_lichess(config, games_dir=None):
    _require_requests()
    games_dir = games_dir or config['games_dir']
    os.makedirs(games_dir, exist_ok=True)

    username    = config.get('lichess_username') or config['username']
    time_classes = config.get('time_classes', {'rapid'})
    perf_types  = [_LICHESS_PERF[tc] for tc in time_classes if tc in _LICHESS_PERF]

    params = {
        'perfType':  ','.join(perf_types),
        'pgnInJson': 'true',
        'opening':   'true',
        'rated':     'true',
        'clocks':    'false',
        'evals':     'false',
    }
    url     = f"https://lichess.org/api/user/{username}/games"
    headers = {'Accept': 'application/x-ndjson'}

    # Verify user exists before streaming
    check = requests.get(f"https://lichess.org/api/user/{username}", headers={'Accept': 'application/json'})
    if check.status_code == 404:
        print(f'Lichess: user "{username}" not found — check the lichess_username in your config')
        print(f'  Verify at: https://lichess.org/@/{username}')
        return
    check.raise_for_status()

    print(f'Lichess: streaming games for {username} ({", ".join(perf_types)}) ...')
    r = requests.get(url, params=params, headers=headers, stream=True)
    r.raise_for_status()

    monthly = {}
    count   = 0

    for line in r.iter_lines():
        if not line:
            continue
        g = json.loads(line)

        ts_ms = g.get('lastMoveAt') or g.get('createdAt', 0)
        dt    = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
        key   = f'{dt.year}_{dt.month:02d}'

        players    = g.get('players', {})
        wp, bp     = players.get('white', {}), players.get('black', {})
        white_user = wp.get('user', {}).get('name', '')
        black_user = bp.get('user', {}).get('name', '')
        status     = g.get('status', '')
        winner     = g.get('winner')
        opening    = g.get('opening', {})
        eco_url    = _opening_to_eco_url(opening)
        pgn_str    = _inject_eco_url(g.get('pgn', ''), eco_url)

        monthly.setdefault(key, []).append({
            'time_class': g.get('speed', ''),
            'white': {
                'username': white_user,
                'rating':   wp.get('rating', 0),
                'result':   _lichess_result(status, winner, True),
            },
            'black': {
                'username': black_user,
                'rating':   bp.get('rating', 0),
                'result':   _lichess_result(status, winner, False),
            },
            'pgn':      pgn_str,
            'end_time': ts_ms // 1000,
        })
        count += 1

    saved = 0
    for key, games in monthly.items():
        fname = os.path.join(games_dir, f'lichess_{key}.json')
        if os.path.exists(fname):
            with open(fname, encoding='utf-8') as f:
                existing = json.load(f)
            seen = {g['pgn'][:80] for g in existing}
            games = existing + [g for g in games if g['pgn'][:80] not in seen]
        with open(fname, 'w', encoding='utf-8') as f:
            json.dump(games, f)
        saved += 1

    print(f'Lichess done: {count} games → {saved} month file(s) in {games_dir}/')


# ── unified entry point ───────────────────────────────────────────────────────

def games_dir_for(config):
    """Return the games directory for a player config, deriving it if not set.

    Convention: games_{username}_{source}
      jf4bes   + chesscom → games_jf4bes_chesscom
      jforbes94 + lichess  → games_jforbes94_lichess
    Explicit 'games_dir' in config always wins.
    """
    if config.get('games_dir'):
        return config['games_dir']
    source   = config.get('source', 'chesscom')
    username = config.get('lichess_username') or config['username'] if source == 'lichess' \
               else config['username']
    return f'games_{username}_{source}'


def fetch_games(config):
    """Download games for the player. source='chesscom'|'lichess'|'both'.

    Chess.com is production-ready. Lichess support is WIP — confirm username
    and validate output before running the full analysis pipeline.
    """
    source    = config.get('source', 'chesscom')
    games_dir = games_dir_for(config)
    if source in ('chesscom', 'both'):
        fetch_chesscom(config, games_dir)
    if source in ('lichess', 'both'):
        # WIP: verify lichess_username in config before running
        fetch_lichess(config, games_dir)
