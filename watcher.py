"""
watcher.py — entrypoint do youtube-playlist-bot.

Chamado pelo GitHub Actions (a cada 3 horas):
    python watcher.py

Toda a lógica está em youtube_bot/. Este arquivo apenas configura
o logging e delega para youtube_bot.processor.main().
"""

import logging
import sys
import time

from youtube_bot.processor import main

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
# Timestamps em UTC de fato (o sufixo "Z" no formato exige isso)
logging.Formatter.converter = time.gmtime
# Silencia o aviso ruidoso de file_cache do googleapiclient
logging.getLogger("googleapiclient.discovery_cache").setLevel(logging.ERROR)

if __name__ == "__main__":
    sys.exit(main())
