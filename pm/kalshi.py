# -*- coding: utf-8 -*-
"""Минимальный read-only клиент Kalshi (trade-api v2, без ключа).

Публичные эндпоинты market data не требуют авторизации (проверено
эмпирически 2026-07-19, в т.ч. из РФ: 200 без геоблока). Котировки
yes_bid/yes_ask приходят прямо в карточке рынка (поля *_dollars).

Комиссия Kalshi (taker): fee = 0.07 * P * (1-P) на контракт,
maker = 1/4 от taker. У Polymarket политика/экономика — 0%.
"""
from __future__ import annotations

import time

import requests

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
_SESSION = requests.Session()

TAKER_FEE_RATE = 0.07


def taker_fee_per_share(price: float) -> float:
    p = min(max(price, 0.0), 1.0)
    return TAKER_FEE_RATE * p * (1 - p)


def get_markets(event_ticker: str | None = None, series_ticker: str | None = None,
                status: str | None = None, limit: int = 200) -> list[dict]:
    """Рынки события/серии (пагинация по cursor)."""
    out: list[dict] = []
    cursor = None
    while True:
        params: dict = {"limit": limit}
        if event_ticker:
            params["event_ticker"] = event_ticker
        if series_ticker:
            params["series_ticker"] = series_ticker
        if status:
            params["status"] = status
        if cursor:
            params["cursor"] = cursor
        for attempt in range(3):
            try:
                r = _SESSION.get(f"{KALSHI}/markets", params=params, timeout=30)
                if r.status_code == 200:
                    break
            except requests.RequestException:
                pass
            time.sleep(attempt + 1)
        else:
            return out
        data = r.json()
        out.extend(data.get("markets") or [])
        cursor = data.get("cursor")
        if not cursor:
            return out


def quotes(m: dict) -> tuple[float | None, float | None]:
    """(yes_bid, yes_ask) в долларах; None если стороны нет."""
    def f(key):
        v = m.get(key)
        try:
            v = float(v)
        except (TypeError, ValueError):
            return None
        # пустая сторона у Kalshi выглядит как 0.00 (bid) / 1.00 (ask)
        if key.startswith("yes_bid") and v <= 0.0:
            return None
        if key.startswith("yes_ask") and v >= 1.0:
            return None
        return v
    return f("yes_bid_dollars"), f("yes_ask_dollars")
