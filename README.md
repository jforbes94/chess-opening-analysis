# Chess Opening Analysis — Project 1500

A data-driven chess training tool that identifies your worst opening weaknesses, finds the specific move decisions that are costing you games, and generates printable PDF lessons with board diagrams and Stockfish validation.

---

## How it works

### 1. Priority list
All games are loaded from your local game files and grouped by ECO code (standard 3-digit chess opening classification, e.g. `C53` = Giuoco Piano Main). Each opening is scored by:

```
priority = games × max(0, 50% − wilson_upper_win_rate)
```

The **Wilson score upper bound** (80th percentile) is used instead of raw win rate so small samples are penalised — 8 games at 20% win ranks far below 60 games at 20% win. Openings you're winning above 50% get priority = 0. A floor ensures that openings with ≥15 games and a win rate below 48% always score non-zero even if the Wilson bound crosses 50%.

### 2. Junction detection
For each priority opening, all games are replayed move by move. At each position where it's your turn (up to move 10), the tool tracks which move you played and the outcome. A **junction** is a position where:
- You reached it in ≥8 games
- At least two candidate moves each appear ≥3 times
- The win rate spread between best and worst move is ≥15%

### 3. Stockfish consistency filter
Every candidate move at every junction is evaluated by Stockfish at depth 16. A junction is **kept** only if the move with the best win rate is also within 20 centipawns of Stockfish's best move. This filters out statistical noise — positions where you happened to win with a bad move due to sample variance.

### 4. PDF lesson generation
One page per junction, landscape layout:
- **Bar chart** — win % per candidate move, colour-coded green/orange/red
- **Board diagram** — position with coloured arrows for each candidate move
- **Move sequence** — most common line from the opening leading to this position
- **Stats table** — win %, Stockfish delta, W/L/D record per move

All lessons are merged into a single report PDF with bookmarked chapters.

---

## Output structure

```
output/                           — committed to git as examples
  {username}_{source}/
    phase_{username}.pdf          — phase analysis (opening/middlegame/endgame loss breakdown)
    priority_{username}_*.csv     — full priority list with win rates
    run_summary_{username}_*.csv  — per-opening lesson results with junction counts and skip reasons
    report_{username}.pdf         — merged report (phase + all lessons)
    lessons/
      lesson_01_{username}_{eco}.pdf
      lesson_02_{username}_{eco}.pdf
      ...

games_{username}_{source}/        — downloaded game files (gitignored, re-fetch with cell 2)
  YYYY_MM.json                    — Chess.com format (one file per month)
  lichess_YYYY_MM.json            — Lichess format (WIP)
```

---

## Setup

### Requirements

```bash
pip install -r requirements.txt
pip install pypdf pillow
```

`requirements.txt` covers: `requests`, `chess`, `pandas`, `matplotlib`

### Stockfish

Download [Stockfish](https://stockfishchess.org/download/) and update the path in `lesson_runner.py`:

```python
STOCKFISH_PATH = r'path\to\stockfish.exe'
```

### Piece images

Board diagrams use PNG piece images. Place them at:
```
trainer/assets/pieces/alpha/wP.png  wN.png  wB.png  wR.png  wQ.png  wK.png
                              bP.png  bN.png  bB.png  bR.png  bQ.png  bK.png
```
Any standard alpha-style piece set (RGBA PNG, any size) works. If images are missing, the board falls back to letter notation.

---

## Usage

Open `runner.ipynb` and work through the cells top to bottom.

### Cell 1 — Configure player

```python
PLAYER = {
    'username':       'your_chesscom_username',
    'source':         'chesscom',       # 'chesscom' | 'lichess' (WIP) | 'both'
    'time_classes':   {'rapid'},        # any combo of: 'bullet','blitz','rapid','daily','classical'
    'exclude_months': set(),            # e.g. {'2025_01','2025_02'} to skip months
}
GROUP_BY   = 'eco'   # 'eco' = group by ECO code (recommended) | 'url' = group by Chess.com URL name
ECO_DEPTH  = 3       # 3 = standard ECO code (C53) — recommended
```

`games_dir` and `output_dir` are auto-derived from username and source:
- `games_quinnleventhal505_chesscom/`
- `output/quinnleventhal505_chesscom/`

### Cell 2 — Download games
Downloads all available months from Chess.com. Skips months already saved locally. Re-run any time to pick up new games.

### Cell 3 — Phase analysis
Shows where games are being decided (opening / middlegame / endgame) and saves `phase_{username}.pdf`.

### Cell 4 — Priority list
Prints and saves the ranked priority list. Openings with ≥10 games are included. The CSV captures the full list for external analysis.

### Cell 5 — Select lessons

```python
GENERATE_ALL = True   # run top TOP_N openings automatically
TOP_N        = 15

# Or pick manually:
GENERATE_ALL = False
LESSON_SELECTIONS = ['C53  Giuoco Piano Game Main', 'C41  Philidor Defense']
```

### Cell 6 — Generate lessons
Runs junction detection + Stockfish for each selected opening. Prints a summary showing raw junctions found, how many passed the SF filter, and why others were skipped.

### Cell 4b — Run summary
Saves `run_summary_*.csv` with per-opening diagnostics. Openings flagged **⚠ too diverse** had enough games but positions never repeated — a real weakness the junction approach can't address at the current ECO grouping level.

### Cell 7 — Merge report
Merges phase PDF + all lesson PDFs into a single bookmarked `report_{username}.pdf`.

---

## Key parameters

| Parameter | Location | Default | Effect |
|-----------|----------|---------|--------|
| `MIN_QUAL_GAMES` | `lesson_runner.py` | 6 | Min games across candidate moves before SF filter |
| `SF_DEPTH` | `lesson_runner.py` | 16 | Stockfish search depth per position |
| `CP_TOLERANCE` | `lesson_runner.py` | 20 | Max centipawn loss vs SF best before junction is rejected |
| `min_games` | cell-4 call | 10 | Min games to appear in priority list |
| `ECO_DEPTH` | cell-1 | 3 | ECO code grouping granularity (1–3) |
| `GROUP_BY` | cell-1 | `'eco'` | Grouping method: ECO code or Chess.com URL |

---

## Files

| File | Description |
|------|-------------|
| `runner.ipynb` | Main entry point — configure, download, analyse, generate |
| `lesson_runner.py` | Core engine: game loading, junction detection, Stockfish eval, PDF generation, phase analysis, priority scoring |
| `game_fetcher.py` | Game downloader for Chess.com (production) and Lichess (WIP) |
| `requirements.txt` | Python dependencies |
| `trainer/` | Piece image assets for PDF board diagrams |
| `outdated/` | Superseded notebooks from earlier iterations |

---

## Supported players (example presets in `runner.ipynb`)

| Username | Source | Time classes |
|----------|--------|--------------|
| `jf4bes` | Chess.com | rapid |
| `sfink37` | Chess.com | bullet, blitz |
| `quinnleventhal505` | Chess.com | rapid, daily, blitz |

---

## Roadmap

- **Lichess support** — normalization implemented, end-to-end pipeline not yet tested (`source: 'lichess'`)
- **Too-diverse openings** — openings with many games but 0 junctions (positions fan out too quickly for the ECO grouping level) have no lesson generated; a drill-based fallback for these is a future option
- **Middlegame/endgame lessons** — phase analysis shows most losses happen after move 10; junction detection currently limited to the opening (moves 1–10)

---

## Notes on daily games

Chess.com `daily` games (correspondence — days per move) can be included in `time_classes` but junctions found there are less actionable since opponents have time to look up theory. Consider analyzing them separately or weighting them differently.
