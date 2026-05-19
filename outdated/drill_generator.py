"""
drill_generator.py

Reads rapid games (April 2025+) and generates opening_drills.json
for the Project 1500 Opening Trainer (Godot).

Drill format mirrors Chesslatro's puzzle format:
  { id, opening_name, color, fen, moves, description, stats }

where:
  fen   = starting FEN (always standard starting position)
  moves = full UCI move sequence for the recommended line (both sides)
          the trainer auto-plays opponent moves and checks yours
"""

import chess
import chess.pgn
import json
import glob
import io
import re
from collections import defaultdict, Counter

USERNAME   = "jf4bes"
GAMES_DIR  = "games"
EXCLUDE    = {"2025_01", "2025_02", "2025_03"}
DRILL_DEPTH = 12   # max half-moves per drill line
MIN_GAMES_FOR_MOVE = 3  # minimum occurrences to trust a move choice
OUTPUT        = "opening_drills.json"
OUTPUT_TRAINER = "trainer/opening_drills.json"  # also write directly into Godot project


# ── Parsing helpers ────────────────────────────────────────────────────────────

def get_eco_url(pgn_str):
    m = re.search(r'\[ECOUrl "([^"]+)"\]', pgn_str)
    return m.group(1) if m else ""

def parse_opening_levels(url):
    if not url or "/openings/" not in url:
        return "Unknown", "Unknown", "Unknown"
    slug  = url.split("/openings/")[-1].strip("/")
    parts = slug.split("-")
    boundary = {"Defense","Defence","Attack","Gambit","Game","Opening","Variation","System"}
    breaks   = [i for i, p in enumerate(parts) if p in boundary]
    human    = " ".join(parts)
    if   len(breaks) == 0: return human, human, human
    elif len(breaks) == 1:
        l1 = " ".join(parts[:breaks[0]+1])
        return l1, human, human
    else:
        l1 = " ".join(parts[:breaks[0]+1])
        l2 = " ".join(parts[:breaks[1]+1])
        return l1, l2, human


# ── Load games ─────────────────────────────────────────────────────────────────

def load_games():
    rows  = []
    files = sorted(
        glob.glob(f"{GAMES_DIR}/2025_*.json") +
        glob.glob(f"{GAMES_DIR}/2026_*.json")
    )
    files = [f for f in files if not any(ex in f for ex in EXCLUDE)]

    for f in files:
        with open(f, encoding="utf-8") as fh:
            month = json.load(fh)
        for g in month:
            if g.get("time_class") != "rapid":
                continue
            white = g.get("white", {})
            black = g.get("black", {})
            if   white.get("username","").lower() == USERNAME.lower():
                color, my = "white", white
            elif black.get("username","").lower() == USERNAME.lower():
                color, my = "black", black
            else:
                continue

            result = my.get("result", "")
            if   result == "win":
                outcome = "win"
            elif result in ("checkmated","timeout","resigned","lose","abandoned"):
                outcome = "loss"
            elif result in ("agreed","stalemate","repetition","insufficient","timevsinsufficient","50move"):
                outcome = "draw"
            else:
                continue

            pgn_str = g.get("pgn", "")
            url     = get_eco_url(pgn_str)
            _, l2, l3 = parse_opening_levels(url)

            rows.append({
                "color":     color,
                "outcome":   outcome,
                "pgn":       pgn_str,
                "l2":        l2,
                "l3":        l3,
                "my_rating": my.get("rating", 0),
            })

    return rows


# ── Line extraction ────────────────────────────────────────────────────────────

def get_uci_moves(pgn_str, max_halfmoves=DRILL_DEPTH):
    try:
        game = chess.pgn.read_game(io.StringIO(pgn_str))
        if not game:
            return []
        board, moves = game.board(), []
        for i, move in enumerate(game.mainline_moves()):
            if i >= max_halfmoves:
                break
            moves.append(board.uci(move))
            board.push(move)
        return moves
    except Exception:
        return []


def build_line(games_for_opening, color, depth=DRILL_DEPTH):
    """
    Build a recommended opening line from your game data.

    - Your turns:    pick the move with the highest win rate (min MIN_GAMES_FOR_MOVE occurrences).
    - Opponent turns: pick their most common response.
    """
    parsed = [
        {"moves": get_uci_moves(g["pgn"]), "outcome": g["outcome"]}
        for g in games_for_opening
    ]
    parsed = [p for p in parsed if p["moves"]]

    if not parsed:
        return []

    board = chess.Board()
    line  = []

    for step in range(depth):
        is_my_turn = (color == "white" and step % 2 == 0) or \
                     (color == "black" and step % 2 == 1)

        # Games that still match the line so far
        matching = [
            p for p in parsed
            if len(p["moves"]) > step and p["moves"][:step] == line
        ]

        if len(matching) < MIN_GAMES_FOR_MOVE:
            break

        move_counts = Counter(
            p["moves"][step] for p in matching if len(p["moves"]) > step
        )
        if not move_counts:
            break

        if is_my_turn:
            # Pick move with best win rate among moves seen >= MIN_GAMES_FOR_MOVE times
            best_move, best_score = None, -1.0
            for move, count in move_counts.items():
                if count < MIN_GAMES_FOR_MOVE:
                    continue
                wins  = sum(
                    1 for p in matching
                    if len(p["moves"]) > step
                    and p["moves"][step] == move
                    and p["outcome"] == "win"
                )
                score = wins / count
                if score > best_score or (
                    score == best_score and count > move_counts.get(best_move or "", 0)
                ):
                    best_score, best_move = score, move

            # Fall back to most common if none meet the threshold
            if best_move is None:
                best_move = move_counts.most_common(1)[0][0]
        else:
            best_move = move_counts.most_common(1)[0][0]

        # Validate legality
        try:
            chess_move = chess.Move.from_uci(best_move)
            if chess_move not in board.legal_moves:
                break
            line.append(best_move)
            board.push(chess_move)
        except Exception:
            break

    return line


# ── Drill builder ──────────────────────────────────────────────────────────────

def build_drills(games):
    # Group by (opening_name_l2, color)
    groups = defaultdict(list)
    for g in games:
        groups[(g["l2"], g["color"])].append(g)

    # Score each group
    scored = []
    for (opening, color), group in groups.items():
        if len(group) < 5:
            continue
        total  = len(group)
        wins   = sum(1 for g in group if g["outcome"] == "win")
        win_pct = 100 * wins / total
        priority = total * (50 - win_pct)
        if priority > 0:
            scored.append((priority, opening, color, group, total, win_pct))

    scored.sort(reverse=True)

    drills, drill_id = [], 1

    for priority, opening_name, color, group, total, win_pct in scored[:20]:
        print(f"  Building: {opening_name} ({color})  {total} games  {win_pct:.1f}% win")

        line = build_line(group, color)

        if len(line) < 4:
            print(f"    [skip] Line too short ({len(line)} moves)")
            continue

        drill = {
            "id":           f"drill_{drill_id:03d}",
            "opening_name": opening_name,
            "color":        color,
            "fen":          chess.STARTING_FEN,
            "moves":        line,
            "description":  f"{opening_name} — practice as {color}",
            "stats": {
                "games":          total,
                "win_rate":       round(win_pct, 1),
                "priority_score": round(priority, 0),
            },
        }
        drills.append(drill)
        drill_id += 1

        preview = " ".join(line[:8]) + ("..." if len(line) > 8 else "")
        print(f"    [ok] {len(line)} moves: {preview}")

    return drills


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    print("Loading games...")
    games = load_games()
    print(f"Loaded {len(games)} rapid games (Apr 2025+)\n")

    print("Building drills...")
    drills = build_drills(games)

    out = {"version": 1, "drills": drills}
    for path in (OUTPUT, OUTPUT_TRAINER):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2)

    print(f"\nDone: {len(drills)} drills written to {OUTPUT} and {OUTPUT_TRAINER}")


if __name__ == "__main__":
    main()
