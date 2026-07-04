"""Тонкий read-only HTTP-клиент к публичным API Polymarket.

Никакой авторизации, подписи EIP-712 или ключей — только GET/POST на публичные
эндпоинты. Обрабатывает 429/5xx через exponential backoff и разбирает
"строковые" поля Gamma (outcomes, outcomePrices, clobTokenIds), которые API
отдаёт как JSON-строки внутри JSON.
"""
from __future__ import annotations

import json
import time
from typing import Any

import requests

import config


class PolymarketClient:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": config.USER_AGENT})

    # --- низкоуровневый запрос с retry/backoff ---
    def _request(self, method: str, url: str, **kwargs: Any) -> Any:
        kwargs.setdefault("timeout", config.REQUEST_TIMEOUT)
        last_exc: Exception | None = None
        for attempt in range(config.MAX_RETRIES):
            try:
                resp = self.session.request(method, url, **kwargs)
                if resp.status_code == 429 or resp.status_code >= 500:
                    wait = config.BACKOFF_BASE ** attempt
                    time.sleep(wait)
                    continue
                if 400 <= resp.status_code < 500:
                    # 404 и прочие 4xx = "данных нет" (токен без стакана и т.п.)
                    return None
                time.sleep(config.INTER_REQUEST_DELAY)
                return resp.json()
            except (requests.RequestException, ValueError) as exc:
                last_exc = exc
                time.sleep(config.BACKOFF_BASE ** attempt)
        raise RuntimeError(f"Запрос не удался после {config.MAX_RETRIES} попыток: {url}") from last_exc

    def get(self, base: str, path: str, params: dict | None = None) -> Any:
        return self._request("GET", f"{base}{path}", params=params)

    def post(self, base: str, path: str, payload: Any) -> Any:
        return self._request("POST", f"{base}{path}", json=payload)

    # --- Gamma API: метаданные рынков ---
    def get_markets(self, *, closed: bool | None = None, active: bool | None = None,
                    order: str = "volume", ascending: bool = False,
                    limit: int = 500, offset: int = 0,
                    extra: dict | None = None) -> list[dict]:
        params: dict[str, Any] = {"limit": limit, "offset": offset,
                                  "order": order, "ascending": str(ascending).lower()}
        if closed is not None:
            params["closed"] = str(closed).lower()
        if active is not None:
            params["active"] = str(active).lower()
        if extra:
            params.update(extra)
        data = self.get(config.GAMMA_BASE, "/markets", params)
        # Gamma может вернуть список либо dict с ключом data
        if isinstance(data, dict):
            data = data.get("data", [])
        return [parse_market(m) for m in (data or [])]

    def get_market_by_slug(self, slug: str) -> dict | None:
        data = self.get(config.GAMMA_BASE, "/markets", {"slug": slug})
        if isinstance(data, dict):
            data = data.get("data", [])
        return parse_market(data[0]) if data else None

    def get_market_by_condition_id(self, condition_id: str) -> dict | None:
        """Ищет рынок включая закрытые: Gamma по умолчанию прячет closed,
        поэтому резолвнутые рынки надо запрашивать с closed=true отдельно."""
        for closed in ("false", "true"):
            data = self.get(config.GAMMA_BASE, "/markets",
                            {"condition_ids": condition_id, "closed": closed})
            if isinstance(data, dict):
                data = data.get("data", [])
            if data:
                return parse_market(data[0])
        return None

    # --- CLOB API: цены и стакан (публично) ---
    def get_price(self, token_id: str, side: str) -> float | None:
        # side: BUY (лучший bid) или SELL (лучший ask)
        data = self.get(config.CLOB_BASE, "/price", {"token_id": token_id, "side": side})
        return _to_float(data.get("price")) if isinstance(data, dict) else None

    def get_midpoint(self, token_id: str) -> float | None:
        data = self.get(config.CLOB_BASE, "/midpoint", {"token_id": token_id})
        return _to_float(data.get("mid")) if isinstance(data, dict) else None

    def get_book(self, token_id: str) -> dict:
        return self.get(config.CLOB_BASE, "/book", {"token_id": token_id})

    def get_prices_history(self, token_id: str, *, interval: str = "max",
                           fidelity: int = 60,
                           start_ts: int | None = None,
                           end_ts: int | None = None) -> list[dict]:
        """Таймсерия цены токена: [{"t": unix, "p": price}, ...].

        ВАЖНО: для resolved-рынков минутная история недоступна — API вернёт []
        при fidelity < 720. Для активных рынков работает любой fidelity.
        interval и startTs/endTs взаимоисключающи.
        """
        params: dict[str, Any] = {"market": token_id, "fidelity": fidelity}
        if start_ts is not None or end_ts is not None:
            if start_ts is not None:
                params["startTs"] = start_ts
            if end_ts is not None:
                params["endTs"] = end_ts
        else:
            params["interval"] = interval
        data = self.get(config.CLOB_BASE, "/prices-history", params)
        return data.get("history", []) if isinstance(data, dict) else []

    # --- Data API: сделки, holders, leaderboard ---
    def get_trades(self, *, market_condition_id: str | None = None,
                   user: str | None = None, limit: int = 500,
                   offset: int = 0) -> list[dict]:
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if market_condition_id:
            params["market"] = market_condition_id
        if user:
            params["user"] = user
        data = self.get(config.DATA_BASE, "/trades", params)
        return data if isinstance(data, list) else data.get("data", [])

    def get_holders(self, condition_id: str, limit: int = 100) -> Any:
        return self.get(config.DATA_BASE, "/holders", {"market": condition_id, "limit": limit})


# --- вспомогательные функции ---
def _to_float(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _maybe_json_list(v: Any) -> list:
    """Gamma отдаёт outcomes/outcomePrices/clobTokenIds как JSON-строки."""
    if isinstance(v, list):
        return v
    if isinstance(v, str) and v.strip():
        try:
            parsed = json.loads(v)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


def parse_market(m: dict) -> dict:
    """Приводит сырой рынок Gamma к чистому dict с распарсенными полями."""
    outcomes = _maybe_json_list(m.get("outcomes"))
    prices = [_to_float(p) for p in _maybe_json_list(m.get("outcomePrices"))]
    token_ids = _maybe_json_list(m.get("clobTokenIds"))

    return {
        "id": m.get("id"),
        "condition_id": m.get("conditionId"),
        "slug": m.get("slug"),
        "question": m.get("question"),
        "category": m.get("category") or _first_tag(m),
        "outcomes": outcomes,
        "outcome_prices": prices,
        "yes_token_id": token_ids[0] if len(token_ids) > 0 else None,
        "no_token_id": token_ids[1] if len(token_ids) > 1 else None,
        "volume": _to_float(m.get("volume")),
        "volume_24hr": _to_float(m.get("volume24hr")),
        "liquidity": _to_float(m.get("liquidity")),
        "best_bid": _to_float(m.get("bestBid")),
        "best_ask": _to_float(m.get("bestAsk")),
        "last_trade_price": _to_float(m.get("lastTradePrice")),
        "active": m.get("active"),
        "closed": m.get("closed"),
        "start_date": m.get("startDate"),
        "end_date": m.get("endDate"),
        # исход для resolved-рынков: индекс выигравшего исхода, если доступен
        "resolved_prices": prices,  # для закрытых рынков ~ [1.0, 0.0] или [0.0, 1.0]
    }


def _first_tag(m: dict) -> str | None:
    tags = m.get("tags")
    if isinstance(tags, list) and tags:
        t0 = tags[0]
        if isinstance(t0, dict):
            return t0.get("label") or t0.get("slug")
    return None
