import requests
import os
import time
import json

USERNAME = "jf4bes"
BASE_URL = "https://api.chess.com/pub/player"
HEADERS = {"User-Agent": "chessanalysis/1.0 jeffreyforbes01@gmail.com"}
OUTPUT_DIR = "games"


def get_archives():
    url = f"{BASE_URL}/{USERNAME}/games/archives"
    r = requests.get(url, headers=HEADERS)
    r.raise_for_status()
    return r.json()["archives"]


def get_games_for_month(archive_url):
    r = requests.get(archive_url, headers=HEADERS)
    r.raise_for_status()
    return r.json()["games"]


def save_pgn(games, year, month):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    filepath = os.path.join(OUTPUT_DIR, f"{year}_{month:02d}.pgn")
    with open(filepath, "w", encoding="utf-8") as f:
        for game in games:
            pgn = game.get("pgn", "")
            if pgn:
                f.write(pgn + "\n\n")
    return filepath


def save_json(games, year, month):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    filepath = os.path.join(OUTPUT_DIR, f"{year}_{month:02d}.json")
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(games, f, indent=2)
    return filepath


def main():
    print(f"Fetching archives for {USERNAME}...")
    archives = get_archives()
    print(f"Found {len(archives)} months of games.\n")

    total_games = 0

    for archive_url in archives:
        # URL format: .../games/2023/05
        parts = archive_url.rstrip("/").split("/")
        year, month = int(parts[-2]), int(parts[-1])

        print(f"Downloading {year}-{month:02d}...", end=" ", flush=True)
        try:
            games = get_games_for_month(archive_url)
            pgn_path = save_pgn(games, year, month)
            save_json(games, year, month)
            print(f"{len(games)} games -> {pgn_path}")
            total_games += len(games)
        except Exception as e:
            print(f"ERROR: {e}")

        time.sleep(0.5)  # be polite to the API

    print(f"\nDone. {total_games} total games saved to ./{OUTPUT_DIR}/")


if __name__ == "__main__":
    main()
