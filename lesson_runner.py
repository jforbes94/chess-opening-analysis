"""
lesson_runner.py — analysis engine for chess opening lessons and phase analysis.

Usage from a notebook:
    from lesson_runner import run_lesson, run_phase_analysis
    run_lesson(lesson_config)
    run_phase_analysis(player_config)
"""

import asyncio
import math
import chess
import chess.engine
import chess.pgn
import chess.svg
import glob
import io
import json
import os
import re
from collections import Counter, defaultdict

import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages

STOCKFISH_PATH = r'E:\Github\Chesslatro\chesslatro\stockfish\stockfish.exe'
SF_DEPTH       = 16
CP_TOLERANCE   = 20
MIN_QUAL_GAMES = 6

# ── analysis ──────────────────────────────────────────────────────────────────

def _filter_files(files, start_date=None, end_date=None, exclude_months=None):
    """Filter game file list by date range and/or excluded months.

    start_date / end_date: 'YYYY_MM' strings, inclusive.
    exclude_months: set of 'YYYY_MM' strings for non-contiguous exclusions.
    """
    result = []
    for f in files:
        m = re.search(r'(\d{4}_\d{2})', f)
        if not m:
            result.append(f)
            continue
        key = m.group(1)
        if start_date and key < start_date:
            continue
        if end_date and key > end_date:
            continue
        if exclude_months and any(ex in f for ex in exclude_months):
            continue
        result.append(f)
    return result


def load_games(username, games_dir, time_classes, eco_keywords=None, eco_key=None,
               eco_code_prefix=None, color='white', exclude_months=None,
               start_date=None, end_date=None):
    files = sorted(glob.glob(f'{games_dir}/*.json'))
    files = _filter_files(files, start_date, end_date, exclude_months)
    games = []
    for f in files:
        with open(f, encoding='utf-8') as fh:
            month = json.load(fh)
        for g in month:
            if g.get('time_class') not in time_classes:
                continue
            white = g.get('white', {})
            black = g.get('black', {})
            my = white if color == 'white' else black
            if my.get('username', '').lower() != username.lower():
                continue
            result = my.get('result', '')
            if   result == 'win': outcome = 'win'
            elif result in ('checkmated','timeout','resigned','lose','abandoned'): outcome = 'loss'
            elif result in ('agreed','stalemate','repetition','insufficient',
                            'timevsinsufficient','50move'): outcome = 'draw'
            else: continue
            pgn_str = g.get('pgn', '')
            url_m  = re.search(r'\[ECOUrl "([^"]+)"\]', pgn_str)
            code_m = re.search(r'\[ECO "([^"]+)"\]',    pgn_str)
            eco      = url_m.group(1)  if url_m  else ''
            eco_code = code_m.group(1) if code_m else ''
            if eco_keywords and not all(kw in eco for kw in eco_keywords):
                continue
            if eco_key is not None:
                path = eco.split('/')[-1]
                if path != eco_key and not path.startswith(eco_key + '-'):
                    continue
            if eco_code_prefix is not None:
                if not eco_code.startswith(eco_code_prefix):
                    continue
            games.append({'outcome': outcome, 'pgn': pgn_str,
                          'eco': eco, 'eco_code': eco_code})
    return games


def find_junctions(games, color='white', max_halfmoves=20, min_pos_games=8,
                   min_move_games=3, min_wr_spread=15):
    my_turn = 0 if color == 'white' else 1  # half-move parity for this player
    move_tree = defaultdict(lambda: defaultdict(Counter))
    for g in games:
        try:
            game  = chess.pgn.read_game(io.StringIO(g['pgn']))
            board = game.board()
            for i, move in enumerate(game.mainline_moves()):
                if i >= max_halfmoves:
                    break
                if i % 2 == my_turn:
                    fp = board.fen().split(' ')
                    pos_key = fp[0] + ' ' + fp[1]  # piece placement + active color
                    move_tree[pos_key][board.san(move)][g['outcome']] += 1
                board.push(move)
        except Exception:
            pass

    junctions = []
    for pos_key, moves_dict in move_tree.items():
        total_pos = sum(sum(v.values()) for v in moves_dict.values())
        if total_pos < min_pos_games:
            continue
        stats = []
        for san, c in moves_dict.items():
            t = sum(c.values())
            if t < min_move_games:
                continue
            stats.append((san, t, round(100 * c['win'] / t, 1), c))
        if len(stats) < 2:
            continue
        wrs = [s[2] for s in stats]
        if max(wrs) - min(wrs) < min_wr_spread:
            continue
        junctions.append((total_pos, pos_key, stats))
    junctions.sort(reverse=True)
    return junctions


def evaluate_junctions(junctions, stockfish_path=STOCKFISH_PATH, depth=SF_DEPTH):
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    sf_evals = {}
    with chess.engine.SimpleEngine.popen_uci(stockfish_path) as engine:
        for _, pos_key, stats in junctions:
            sf_evals[pos_key] = {}
            try:
                board = chess.Board(pos_key + ' KQkq - 0 1')
            except Exception:
                continue
            for san, _t, _wr, _c in stats:
                try:
                    b = board.copy()
                    b.push(board.parse_san(san))
                    info = engine.analyse(b, chess.engine.Limit(depth=depth))
                    # score from the perspective of the side making the move
                    sf_evals[pos_key][san] = info['score'].pov(board.turn).score(mate_score=1000)
                except Exception:
                    pass
    return sf_evals


def filter_consistent(junctions, sf_evals,
                      cp_tolerance=CP_TOLERANCE, min_qual_games=MIN_QUAL_GAMES):
    consistent, skipped = [], []
    for tp, pk, stats in junctions:
        cp_map      = sf_evals.get(pk, {})
        best_cp     = max(cp_map.values()) if cp_map else None
        qual_games  = sum(s[1] for s in stats)
        best_wr_san = max(stats, key=lambda x: x[2])[0]
        best_wr_val = max(stats, key=lambda x: x[2])[2]
        move_cp     = cp_map.get(best_wr_san)

        if qual_games < min_qual_games:
            reason = f'too few games ({qual_games} < {min_qual_games})'
        elif (best_cp is not None and move_cp is not None
              and (best_cp - move_cp) > cp_tolerance):
            delta  = best_cp - move_cp
            reason = (f'SF contradicts data — {best_wr_san} '
                      f'({best_wr_val:.0f}% win) is -{delta}cp vs SF best')
        else:
            reason = None

        if reason:
            skipped.append((tp, pk, stats, reason))
        else:
            consistent.append((tp, pk, stats))
    return consistent, skipped


def most_common_sequence(games, pos_key):
    sequences = Counter()
    for g in games:
        try:
            game  = chess.pgn.read_game(io.StringIO(g['pgn']))
            board = game.board()
            played = []
            for move in game.mainline_moves():
                played.append(board.san(move))
                board.push(move)
                fp = board.fen().split(' ')
                if fp[0] + ' ' + fp[1] == pos_key:
                    sequences[tuple(played)] += 1
                    break
        except Exception:
            pass
    return list(sequences.most_common(1)[0][0]) if sequences else []


# ── matplotlib board renderer (used only in PDF) ──────────────────────────────

_PIECE_FILENAMES = {
    (chess.PAWN,   chess.WHITE): 'wP', (chess.PAWN,   chess.BLACK): 'bP',
    (chess.KNIGHT, chess.WHITE): 'wN', (chess.KNIGHT, chess.BLACK): 'bN',
    (chess.BISHOP, chess.WHITE): 'wB', (chess.BISHOP, chess.BLACK): 'bB',
    (chess.ROOK,   chess.WHITE): 'wR', (chess.ROOK,   chess.BLACK): 'bR',
    (chess.QUEEN,  chess.WHITE): 'wQ', (chess.QUEEN,  chess.BLACK): 'bQ',
    (chess.KING,   chess.WHITE): 'wK', (chess.KING,   chess.BLACK): 'bK',
}

_piece_cache = {}

def _get_piece_dir():
    candidates = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     'trainer', 'assets', 'pieces', 'alpha'),
        os.path.join(os.getcwd(), 'trainer', 'assets', 'pieces', 'alpha'),
    ]
    for p in candidates:
        if os.path.isdir(p):
            return p
    return None

def _load_pieces():
    if _piece_cache:
        return
    piece_dir = _get_piece_dir()
    if piece_dir is None:
        print('WARNING: piece image directory not found — using letter fallback')
        return
    try:
        from PIL import Image
        import numpy as np
        loaded = 0
        for key, name in _PIECE_FILENAMES.items():
            path = os.path.join(piece_dir, f'{name}.png')
            if os.path.exists(path):
                img = np.array(Image.open(path).convert('RGBA'))
                _piece_cache[key] = img[::-1]  # flip vertically for origin='lower'
                loaded += 1
        if loaded == 0:
            print(f'WARNING: no piece PNGs found in {piece_dir}')
    except Exception as e:
        print(f'WARNING: could not load piece images ({e}) — using letter fallback')


def _draw_board_mpl(ax, board, arrows=None, flip=False):
    _load_pieces()
    LIGHT, DARK = '#f0d9b5', '#b58863'

    def dx(f): return (7 - f) if flip else f
    def dy(r): return (7 - r) if flip else r

    for rank in range(8):
        for file in range(8):
            color = LIGHT if (rank + file) % 2 == 1 else DARK
            ax.add_patch(plt.Rectangle([dx(file), dy(rank)], 1, 1, color=color, zorder=0))

    for sq in chess.SQUARES:
        piece = board.piece_at(sq)
        if not piece:
            continue
        f   = chess.square_file(sq)
        r   = chess.square_rank(sq)
        px, py = dx(f), dy(r)
        key = (piece.piece_type, piece.color)
        if key in _piece_cache:
            ax.imshow(_piece_cache[key],
                      extent=[px, px + 1, py, py + 1],
                      origin='lower', zorder=2)
        else:
            letters = {chess.PAWN:'P', chess.KNIGHT:'N', chess.BISHOP:'B',
                       chess.ROOK:'R', chess.QUEEN:'Q', chess.KING:'K'}
            ltr = letters[piece.piece_type]
            if piece.color == chess.BLACK:
                ltr = ltr.lower()
            ax.text(px + 0.5, py + 0.5, ltr,
                    ha='center', va='center', fontsize=16, fontweight='bold',
                    color='white' if piece.color == chess.WHITE else '#111',
                    zorder=2)

    if arrows:
        for arrow in arrows:
            ff = dx(chess.square_file(arrow.tail)) + 0.5
            fr = dy(chess.square_rank(arrow.tail)) + 0.5
            tf = dx(chess.square_file(arrow.head)) + 0.5
            tr = dy(chess.square_rank(arrow.head)) + 0.5
            ax.annotate('', xy=(tf, tr), xytext=(ff, fr),
                        arrowprops=dict(arrowstyle='->', color=arrow.color,
                                        lw=3, mutation_scale=20),
                        zorder=3)

    file_labels = 'hgfedcba' if flip else 'abcdefgh'
    for i in range(8):
        ax.text(i + 0.5, -0.35, file_labels[i], ha='center', fontsize=7, color='#666')
        ax.text(-0.35, i + 0.5, str(8 - i) if flip else str(i + 1),
                va='center', fontsize=7, color='#666')
    ax.set_xlim(-0.5, 8)
    ax.set_ylim(-0.5, 8.1)
    ax.set_aspect('equal')
    ax.axis('off')


def _make_arrows(board, jstats_sorted):
    arrows = []
    for san, _t, wr, _c in jstats_sorted:
        col = '#27ae60' if wr >= 45 else '#c0392b' if wr < 35 else '#e67e22'
        try:
            move_obj = board.parse_san(san)
            arrows.append(chess.svg.Arrow(move_obj.from_square,
                                          move_obj.to_square, color=col))
        except Exception:
            pass
    return arrows


# ── PDF generation ────────────────────────────────────────────────────────────

def generate_pdf(consistent, sf_evals, games, config, output_path):
    username      = config['username']
    eco_keywords  = config.get('eco_keywords') or []
    eco_key       = config.get('eco_key', '')
    eco_code_pfx  = config.get('eco_code_prefix', '')
    eco_display   = eco_code_pfx or eco_key or ' + '.join(eco_keywords)
    lesson_title  = config.get('lesson_title', eco_display)
    lesson_number = config.get('lesson_number', 1)
    total         = config.get('total', 0)
    wr            = config.get('wr', 0.0)

    with PdfPages(output_path) as pdf:

        # Title page
        fig = plt.figure(figsize=(11, 8.5))
        fig.text(0.5, 0.62, lesson_title,
                 ha='center', fontsize=22, fontweight='bold')
        fig.text(0.5, 0.54, username,
                 ha='center', fontsize=14, color='#555')
        fig.text(0.5, 0.47, f'{total} games  |  {wr:.1f}% win rate',
                 ha='center', fontsize=13, color='#555')
        fig.text(0.5, 0.40, f'ECO filter: {eco_display}',
                 ha='center', fontsize=11, color='#888')
        fig.text(0.5, 0.34, f'{len(consistent)} consistent junction(s) found',
                 ha='center', fontsize=11, color='#888')
        pdf.savefig(fig, bbox_inches='tight')
        plt.close(fig)

        color = config.get('color', 'white')
        flip  = (color == 'black')

        for idx, (jt, jfen, jstats) in enumerate(consistent):
            jstats_sorted = sorted(jstats, key=lambda x: -x[2])
            jcp     = sf_evals.get(jfen, {})
            best_cp = max(jcp.values()) if jcp else None
            seq     = most_common_sequence(games, jfen)
            jboard  = chess.Board(jfen + ' KQkq - 0 1')
            arrows  = _make_arrows(jboard, jstats_sorted)

            # One page per junction: 2×2 grid
            fig = plt.figure(figsize=(11, 8.5))
            fig.suptitle(f'Junction {idx+1} of {len(consistent)} — {jt} games',
                         fontsize=13, y=0.98)
            gs = fig.add_gridspec(2, 2, height_ratios=[3, 2],
                                  hspace=0.35, wspace=0.25,
                                  left=0.06, right=0.97, top=0.94, bottom=0.04)
            ax_c    = fig.add_subplot(gs[0, 0])  # bar chart
            ax_b    = fig.add_subplot(gs[0, 1])  # board
            ax_seq  = fig.add_subplot(gs[1, 0])  # move sequence
            ax_stat = fig.add_subplot(gs[1, 1])  # stats table

            # Bar chart
            labels = [s[0] for s in jstats_sorted]
            wrs    = [s[2] for s in jstats_sorted]
            ns     = [s[1] for s in jstats_sorted]
            colors = ['#27ae60' if w >= 45 else '#c0392b' if w < 35
                      else '#e67e22' for w in wrs]
            bars   = ax_c.bar(labels, wrs, color=colors, width=0.5, edgecolor='white')
            ax_c.axhline(50, color='gray', linestyle='--', linewidth=1)
            ax_c.set_ylabel('Win %')
            ax_c.set_ylim(0, 90)
            for bar, wv, n in zip(bars, wrs, ns):
                ax_c.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 1.5,
                          f'{wv:.0f}%\n({n}g)', ha='center', va='bottom', fontsize=8)
            ax_c.legend(handles=[
                mpatches.Patch(color='#27ae60', label='>= 45%  good'),
                mpatches.Patch(color='#e67e22', label='35-44%  ok'),
                mpatches.Patch(color='#c0392b', label='< 35%  avoid'),
            ], fontsize=7)

            # Board
            _draw_board_mpl(ax_b, jboard, arrows, flip=flip)
            ax_b.set_title('green good | orange ok | red avoid', fontsize=8, color='#555')

            # Move sequence
            ax_seq.axis('off')
            y = 0.95
            if seq:
                ax_seq.text(0, y, 'Route to junction:',
                            fontsize=9, fontweight='bold', transform=ax_seq.transAxes)
                y -= 0.15
                for i in range(0, len(seq), 2):
                    mn = i // 2 + 1
                    wm = seq[i]
                    bm = seq[i+1] if i+1 < len(seq) else ''
                    ax_seq.text(0, y, f'{mn}.  {wm:<10}  {bm}',
                                fontsize=9, fontfamily='monospace',
                                transform=ax_seq.transAxes)
                    y -= 0.14
                ax_seq.text(0, y, f'-> {color} to move', fontsize=8, color='#888',
                            transform=ax_seq.transAxes)

            # Stats table
            ax_stat.axis('off')
            y = 0.95
            col_x = [0, 0.20, 0.38, 0.55, 0.65, 0.73, 0.81, 0.89]
            hdrs  = ['Move', 'Win%', 'SF delta', 'Games', 'W', 'L', 'D', 'Rating']
            for hx, h in zip(col_x, hdrs):
                ax_stat.text(hx, y, h, fontsize=8, fontweight='bold',
                             color='#666', transform=ax_stat.transAxes)
            y -= 0.15
            for san, t, wr2, c in jstats_sorted:
                cp    = jcp.get(san)
                delta = (cp - best_cp) if (cp is not None and best_cp is not None) else None
                ds    = f'{delta:+d}cp' if delta is not None else '---'
                tag   = '[GOOD]' if wr2 >= 45 else '[AVOID]' if wr2 < 35 else '[OK]'
                tcol  = '#27ae60' if wr2 >= 45 else '#c0392b' if wr2 < 35 else '#e67e22'
                vals  = [san, f'{wr2:.0f}%', ds, str(t),
                         str(c['win']), str(c['loss']), str(c.get('draw', 0)), tag]
                for vx, v in zip(col_x, vals):
                    ax_stat.text(vx, y, v, fontsize=9, fontfamily='monospace',
                                 color=tcol if v == tag else 'black',
                                 transform=ax_stat.transAxes)
                y -= 0.16

            pdf.savefig(fig, bbox_inches='tight')
            plt.close(fig)

    print(f'PDF saved: {output_path}')


# ── orchestrator ──────────────────────────────────────────────────────────────

def run_lesson(config, output_dir='.'):
    username     = config['username']
    games_dir    = config['games_dir']
    time_classes = config['time_classes']
    eco_keywords = config.get('eco_keywords')
    sf_path      = config.get('stockfish_path', STOCKFISH_PATH)
    lesson_n     = config.get('lesson_number', 1)

    color            = config.get('color', 'white')
    eco_key          = config.get('eco_key')
    eco_code_prefix  = config.get('eco_code_prefix')
    label            = eco_code_prefix or eco_key or ' + '.join(eco_keywords or [])
    print(f'Loading {label} games for {username} (as {color}) ...')
    games  = load_games(username, games_dir, time_classes,
                        eco_keywords=eco_keywords, eco_key=eco_key,
                        eco_code_prefix=eco_code_prefix, color=color,
                        exclude_months=config.get('exclude_months'),
                        start_date=config.get('start_date'),
                        end_date=config.get('end_date'))
    counts = Counter(g['outcome'] for g in games)
    total  = len(games)
    wr     = 100 * counts['win'] / total if total else 0
    print(f'  {total} games  W:{counts["win"]} L:{counts["loss"]} D:{counts["draw"]}  Win%: {wr:.1f}%')

    print('Finding junctions ...')
    junctions = find_junctions(games, color=color)
    print(f'  {len(junctions)} raw junctions')

    print(f'Running Stockfish depth {SF_DEPTH} ...')
    sf_evals = evaluate_junctions(junctions, sf_path)

    print('Filtering (win-rate vs SF consistency) ...')
    consistent, skipped = filter_consistent(junctions, sf_evals)
    for tp, _pk, _st, reason in skipped:
        print(f'  SKIP ({tp:3d}x): {reason}')
    for tp, _pk, stats in consistent:
        best = max(stats, key=lambda x: x[2])
        print(f'  KEEP ({tp:3d}x): {best[0]} leads at {best[2]:.0f}% win')
    print(f'  {len(consistent)} consistent junction(s)')

    if not consistent:
        print(f'  No consistent junctions — skipping PDF')
        return {
            'games': games, 'total': total, 'wr': wr,
            'junctions': junctions, 'consistent': consistent, 'skipped': skipped,
            'sf_evals': sf_evals, 'pdf_path': None,
        }

    lessons_dir = os.path.join(output_dir, 'lessons')
    os.makedirs(lessons_dir, exist_ok=True)
    slug = (eco_code_prefix or eco_key or '_'.join(eco_keywords or [])).replace('-', '_')
    pdf_path = os.path.join(lessons_dir, f'lesson_{lesson_n:02d}_{username}_{slug}.pdf')
    generate_pdf(consistent, sf_evals, games,
                 {**config, 'total': total, 'wr': wr}, pdf_path)

    return {
        'games': games, 'total': total, 'wr': wr,
        'junctions': junctions, 'consistent': consistent, 'skipped': skipped,
        'sf_evals': sf_evals, 'pdf_path': pdf_path,
    }


# ── opening priority ──────────────────────────────────────────────────────────

_COMMON_WORDS = {'Defense', 'Variation', 'Opening', 'Game', 'Attack',
                 'System', 'Line', 'Setup', 'with', 'The', 'and'}


def _wilson_upper(wins, n, z=0.84):
    """Upper bound of Wilson score CI (z=0.84 ≈ 80th percentile).
    Returns the highest plausible win rate — small samples get pushed toward 50%,
    reducing their priority score relative to well-sampled openings."""
    if n == 0:
        return 1.0
    p = wins / n
    denom  = 1 + z**2 / n
    center = p + z**2 / (2 * n)
    spread = z * math.sqrt(p * (1 - p) / n + z**2 / (4 * n**2))
    return (center + spread) / denom


def _eco_truncate(path, depth=4):
    """Stop at first move-notation segment (starts with digit), then cap at depth."""
    segments = path.split('-')
    named = []
    for seg in segments:
        if seg and seg[0].isdigit():
            break
        named.append(seg)
    return '-'.join(named[:depth])


def eco_to_keywords(eco_url, depth=4):
    """Derive distinctive ECO keywords using the same truncation as the priority list."""
    path = eco_url.split('/')[-1]
    key  = _eco_truncate(path, depth)
    words = key.split('-')
    distinctive = [w for w in words if w not in _COMMON_WORDS and len(w) > 2]
    return distinctive[:3]


def compute_opening_priority(config, min_games=5, depth=4, color='white',
                             group_by='url', eco_depth=3):
    """Return openings ranked by Wilson-weighted priority, for the given color.

    group_by='url'  — groups by ECO URL truncated to `depth` named segments (default).
    group_by='eco'  — groups by ECO code prefix of length `eco_depth` (e.g. 'B12').

    Priority = games × max(0, 50 - wilson_upper_win%) so small samples are
    penalised — a 20% win rate in 8 games ranks far below 20% in 60 games.
    """
    games = load_games(config['username'], config['games_dir'],
                       config['time_classes'], color=color,
                       exclude_months=config.get('exclude_months'),
                       start_date=config.get('start_date'),
                       end_date=config.get('end_date'))
    opening_stats = defaultdict(Counter)
    opening_eco   = {}                     # representative URL per group
    opening_eco_url_counts = defaultdict(Counter)  # for deriving name in eco mode

    for g in games:
        eco      = g.get('eco', '')
        eco_code = g.get('eco_code', '')
        if group_by == 'eco':
            if not eco_code: continue
            key = eco_code[:eco_depth]
            opening_eco_url_counts[key][eco] += 1
        else:
            if not eco: continue
            key = _eco_truncate(eco.split('/')[-1], depth)
            if not key: continue
        opening_stats[key][g['outcome']] += 1
        opening_eco[key] = eco

    results = []
    for key, counts in opening_stats.items():
        t       = sum(counts.values())
        if t < min_games: continue
        wr      = 100 * counts['win'] / t
        w_upper = 100 * _wilson_upper(counts['win'], t)
        # Floor: ≥15 games below 48% win always gets non-zero priority
        effective_upper = min(w_upper, 48.0) if (t >= 15 and wr < 48.0) else w_upper
        priority = t * max(0, 50 - effective_upper)

        if group_by == 'eco':
            # Derive a readable name from the most common URL in this ECO group
            best_url  = max(opening_eco_url_counts[key], key=opening_eco_url_counts[key].get)
            url_path  = best_url.split('/')[-1]
            url_name  = _eco_truncate(url_path, 4).replace('-', ' ')
            name      = f'{key}  {url_name}'
            entry = {
                'name':             name,
                'eco_code':         key,       # used as eco_code_prefix in load_games
                'eco_key':          None,
                'lesson_eco_key':   None,
                'eco_code_prefix':  key,       # same level — ECO code is already specific
                'keywords':         [key],
                'lesson_keywords':  [key],
                'eco_url':          best_url,
                'group_by':         'eco',
            }
        else:
            eco_url = opening_eco[key]
            path    = eco_url.split('/')[-1]
            entry = {
                'name':             key.replace('-', ' '),
                'eco_code':         None,
                'eco_key':          key,
                'lesson_eco_key':   _eco_truncate(path, depth + 1),
                'eco_code_prefix':  None,
                'keywords':         eco_to_keywords(eco_url, depth),
                'lesson_keywords':  eco_to_keywords(eco_url, depth + 1),
                'eco_url':          eco_url,
                'group_by':         'url',
            }
        entry.update({'color': color, 'games': t, 'wr': wr,
                      'priority': priority, 'counts': dict(counts)})
        results.append(entry)

    results.sort(key=lambda x: -x['priority'])
    return results


# ── phase analysis ─────────────────────────────────────────────────────────────

_OPENING_END    = 20   # half-moves (move 10)
_MIDDLEGAME_END = 50   # half-moves (move 25)
_PHASES = ['Opening (<=move 10)', 'Middlegame (move 11-25)', 'Endgame (move 26+)']


def _load_all_games(username, games_dir, time_classes, exclude_months=None,
                    start_date=None, end_date=None):
    """Load games for both colors — used by phase analysis."""
    files = sorted(glob.glob(f'{games_dir}/*.json'))
    files = _filter_files(files, start_date, end_date, exclude_months)
    games = []
    for f in files:
        with open(f, encoding='utf-8') as fh:
            month = json.load(fh)
        for g in month:
            if g.get('time_class') not in time_classes: continue
            white = g.get('white', {})
            black = g.get('black', {})
            if white.get('username', '').lower() == username.lower():
                color, my = 'white', white
            elif black.get('username', '').lower() == username.lower():
                color, my = 'black', black
            else:
                continue
            result = my.get('result', '')
            if   result == 'win': outcome = 'win'
            elif result in ('checkmated','timeout','resigned','lose','abandoned'): outcome = 'loss'
            elif result in ('agreed','stalemate','repetition','insufficient',
                            'timevsinsufficient','50move'): outcome = 'draw'
            else: continue
            games.append({
                'outcome':    outcome,
                'color':      color,
                'end_reason': result,
                'time_class': g.get('time_class'),
                'pgn':        g.get('pgn', ''),
                'my_rating':  my.get('rating', 0),
                'end_time':   g.get('end_time', 0),
            })
    return sorted(games, key=lambda x: x['end_time'])


def _count_halfmoves(pgn_str):
    try:
        import chess.pgn as _pgn
        game = _pgn.read_game(io.StringIO(pgn_str))
        return len(list(game.mainline_moves()))
    except Exception:
        return 0


def _assign_phase(hm):
    if hm <= _OPENING_END:    return _PHASES[0]
    if hm <= _MIDDLEGAME_END: return _PHASES[1]
    return _PHASES[2]


def _classify_end(reason):
    if reason == 'checkmated':             return 'Checkmate'
    if reason in ('resigned', 'lose'):     return 'Resignation'
    if reason in ('timeout', 'abandoned'): return 'Timeout'
    return 'Other'


def _generate_phase_pdf(games, config, output_path):
    import datetime

    username = config['username']
    losses   = [g for g in games if g['outcome'] == 'loss']
    total    = len(games)
    wr       = 100 * sum(1 for g in games if g['outcome'] == 'win') / total
    total_losses  = len(losses)
    phase_counts  = Counter(g['phase'] for g in losses)
    loss_lengths  = [g['halfmoves'] for g in losses]
    win_lengths   = [g['halfmoves'] for g in games if g['outcome'] == 'win']
    unique_tc     = sorted(set(g['time_class'] for g in games))
    tc_colors     = {'bullet': '#c0392b', 'blitz': '#2980b9', 'rapid': '#27ae60'}
    end_types     = ['Checkmate', 'Resignation', 'Timeout', 'Other']
    colors_end    = ['#c0392b', '#e67e22', '#8e44ad', '#888']

    with PdfPages(output_path) as pdf:

        # Title page
        fig = plt.figure(figsize=(11, 8.5))
        fig.text(0.5, 0.62, f'Game Phase Analysis — {username}',
                 ha='center', fontsize=22, fontweight='bold')
        fig.text(0.5, 0.54, f'{total} games  |  {wr:.1f}% win rate  |  {total_losses} losses',
                 ha='center', fontsize=13, color='#555')
        fig.text(0.5, 0.44,
                 '  |  '.join(f'{p}: {100*phase_counts[p]/total_losses:.0f}%' for p in _PHASES),
                 ha='center', fontsize=11, color='#888')
        pdf.savefig(fig, bbox_inches='tight')
        plt.close(fig)

        # Rating progression
        if any(g['end_time'] > 0 for g in games):
            fig, axes = plt.subplots(1, max(len(unique_tc), 1),
                                     figsize=(7 * len(unique_tc), 4))
            if len(unique_tc) == 1:
                axes = [axes]
            for ax, tc in zip(axes, unique_tc):
                tc_g = [g for g in games if g['time_class'] == tc and g['my_rating'] > 0]
                if not tc_g: continue
                dates   = [datetime.datetime.fromtimestamp(g['end_time']) for g in tc_g]
                ratings = [g['my_rating'] for g in tc_g]
                ax.plot(dates, ratings, linewidth=0.7, alpha=0.8,
                        color=tc_colors.get(tc, '#888'))
                ax.set_title(f'{username} — {tc.capitalize()} rating')
                ax.set_ylabel('Rating')
                ax.set_ylim(min(ratings) - 50, max(ratings) + 50)
                ax.tick_params(axis='x', rotation=20)
            plt.suptitle(f'{username}: Rating progression', fontsize=13)
            plt.tight_layout()
            pdf.savefig(fig, bbox_inches='tight')
            plt.close(fig)

        # Loss distribution + wins vs losses
        fig, axes = plt.subplots(1, 2, figsize=(14, 4))
        axes[0].hist(loss_lengths, bins=40, color='#c0392b', alpha=0.8, edgecolor='none')
        axes[0].axvline(_OPENING_END,    color='yellow',     linestyle='--', linewidth=1.5, label='Move 10')
        axes[0].axvline(_MIDDLEGAME_END, color='lightyellow', linestyle='--', linewidth=1.5, label='Move 25')
        axes[0].set_xlabel('Half-moves played')
        axes[0].set_ylabel('Losses')
        axes[0].set_title(f'{username}: Loss distribution by game length')
        axes[0].legend()
        bins = range(0, max(loss_lengths + win_lengths) + 5, 3)
        axes[1].hist(win_lengths,  bins=bins, color='#27ae60', alpha=0.6, label='Wins',   edgecolor='none')
        axes[1].hist(loss_lengths, bins=bins, color='#c0392b', alpha=0.6, label='Losses', edgecolor='none')
        axes[1].set_xlabel('Half-moves played')
        axes[1].set_ylabel('Games')
        axes[1].set_title('Wins vs Losses by game length')
        axes[1].legend()
        plt.tight_layout()
        pdf.savefig(fig, bbox_inches='tight')
        plt.close(fig)

        # Win rate by bucket
        bucket_size  = 10
        bucket_stats = defaultdict(Counter)
        for g in games:
            b = (g['halfmoves'] // bucket_size) * bucket_size
            bucket_stats[b][g['outcome']] += 1
        xs, wrs_b, totals = [], [], []
        for b in sorted(bucket_stats):
            c = bucket_stats[b]
            t = sum(c.values())
            if t < 5: continue
            xs.append(b // 2)
            wrs_b.append(100 * c['win'] / t)
            totals.append(t)
        fig, ax = plt.subplots(figsize=(13, 4))
        bar_colors = ['#27ae60' if w >= 50 else '#c0392b' for w in wrs_b]
        bars = ax.bar(xs, wrs_b, width=4, color=bar_colors, edgecolor='none', alpha=0.85)
        ax.axhline(50, color='gray', linestyle='--', linewidth=1)
        ax.axvline(10, color='yellow',     linestyle=':', linewidth=1.5, label='Move 10')
        ax.axvline(25, color='lightyellow', linestyle=':', linewidth=1.5, label='Move 25')
        ax.set_xlabel('Full move number (5-move windows)')
        ax.set_ylabel('Win %')
        ax.set_title(f'{username}: Win rate by game length')
        ax.legend()
        for bar, w, n in zip(bars, wrs_b, totals):
            if n >= 10:
                ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 1.5,
                        f'{w:.0f}%', ha='center', fontsize=8)
        plt.tight_layout()
        pdf.savefig(fig, bbox_inches='tight')
        plt.close(fig)

        # How losses end by phase
        phase_end = {ph: Counter() for ph in _PHASES}
        for g in losses:
            phase_end[g['phase']][_classify_end(g['end_reason'])] += 1
        fig, axes = plt.subplots(1, 3, figsize=(14, 4))
        for ax, phase in zip(axes, _PHASES):
            c = phase_end[phase]
            ax.bar(end_types, [c[et] for et in end_types], color=colors_end, edgecolor='none')
            ax.set_title(f'{phase}\n({sum(c.values())} losses)')
            ax.set_ylabel('Count')
            ax.tick_params(axis='x', rotation=20)
        plt.suptitle(f'{username}: How do losses end — by phase?', fontsize=13)
        plt.tight_layout()
        pdf.savefig(fig, bbox_inches='tight')
        plt.close(fig)

    print(f'PDF saved: {output_path}')


def run_phase_analysis(config, output_dir='.'):
    username       = config['username']
    games_dir      = config['games_dir']
    time_classes   = config['time_classes']
    print(f'Phase analysis: loading all games for {username} ...')
    games = _load_all_games(username, games_dir, time_classes,
                            exclude_months=config.get('exclude_months'),
                            start_date=config.get('start_date'),
                            end_date=config.get('end_date'))
    for g in games:
        g['halfmoves'] = _count_halfmoves(g['pgn'])
        g['phase']     = _assign_phase(g['halfmoves'])

    losses = [g for g in games if g['outcome'] == 'loss']
    total  = len(games)
    wr     = 100 * sum(1 for g in games if g['outcome'] == 'win') / total
    print(f'  {total} games  {wr:.1f}% win  {len(losses)} losses')

    pdf_path = os.path.join(output_dir, f'phase_{username}.pdf')
    _generate_phase_pdf(games, config, pdf_path)

    return {'games': games, 'total': total, 'wr': wr,
            'losses': losses, 'pdf_path': pdf_path}
