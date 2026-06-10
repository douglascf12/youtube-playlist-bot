"""
watcher.py — entrypoint do youtube-playlist-bot.

Chamado diretamente pelo GitHub Actions:
    python watcher.py

Toda a lógica está em youtube_bot/. Este arquivo apenas configura
o logging e delega para youtube_bot.processor.main().
"""

import logging

from youtube_bot.processor import main

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)

if __name__ == "__main__":
    main()
