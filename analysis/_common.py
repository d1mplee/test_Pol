"""Общие загрузчики данных для скриптов гипотез."""
from __future__ import annotations

import pandas as pd

import config


def load_markets() -> pd.DataFrame:
    if not config.MARKETS_FILE.exists():
        raise SystemExit("Нет data/markets.parquet — сначала запусти: python -m pm.collect")
    return pd.read_parquet(config.MARKETS_FILE)


def load_history(token_id: str) -> pd.DataFrame | None:
    path = config.HISTORY_DIR / f"{token_id}.parquet"
    if not path.exists():
        return None
    return pd.read_parquet(path)


def winner_is_yes(row) -> bool | None:
    """Для resolved-рынка определяет, победил ли Yes, по финальным ценам.

    outcome_prices у закрытого рынка ~ [1.0, 0.0] (Yes) или [0.0, 1.0] (No).
    Возвращает None, если рынок не разрешён однозначно.
    """
    prices = row.get("outcome_prices")
    # после parquet это может быть numpy-массив, а не list — берём по длине
    if prices is None or len(prices) < 2:
        return None
    yes_p, no_p = prices[0], prices[1]
    if yes_p is None or no_p is None:
        return None
    if yes_p >= 0.95 and no_p <= 0.05:
        return True
    if no_p >= 0.95 and yes_p <= 0.05:
        return False
    return None  # неоднозначно / ещё торгуется
