"""Конфигурация: базовые URL публичных API Polymarket и параметры сети.

Всё read-only. Ни один из этих эндпоинтов не требует авторизации, кошелька
или денег — только для чтения рыночных данных, истории цен и сделок.
"""
from pathlib import Path

# --- Базовые URL (проверено research-агентом, docs.polymarket.com) ---
GAMMA_BASE = "https://gamma-api.polymarket.com"   # метаданные рынков/событий
CLOB_BASE = "https://clob.polymarket.com"          # стакан, цены, история цен
DATA_BASE = "https://data-api.polymarket.com"      # сделки, holders, leaderboard

# --- Сеть / rate-limit ---
# Доки заявляют высокий лимит (~15k/10с), но закладываемся консервативно.
REQUEST_TIMEOUT = 20          # секунд на один HTTP-запрос
MAX_RETRIES = 5               # попыток при 429/5xx
BACKOFF_BASE = 1.5            # exponential backoff: BACKOFF_BASE ** attempt секунд
INTER_REQUEST_DELAY = 0.05    # пауза между запросами, чтобы не долбить API
USER_AGENT = "polymarket-research/0.1 (read-only research)"

# --- Хранилище на диске ---
ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
HISTORY_DIR = DATA_DIR / "history"      # по файлу истории цен на token_id
MARKETS_FILE = DATA_DIR / "markets.parquet"
OUTPUT_DIR = ROOT / "output"           # отчёты и графики

for _d in (DATA_DIR, HISTORY_DIR, OUTPUT_DIR):
    _d.mkdir(parents=True, exist_ok=True)
