"""Модель транзакционных издержек Polymarket.

Ключевой модуль всего исследования: любая "прибыльная" стратегия на бумаге
обязана пережить издержки, иначе это иллюзия. Здесь считаются:
  1. Taker-fee по официальной формуле Polymarket с категорийными кэпами.
  2. Стоимость пересечения спреда (half-spread как прокси проскальзывания).

Maker-ордера на Polymarket = 0% комиссии. Комиссию платит только taker.
Формула fee (docs.polymarket.com/trading/fees):
    fee_per_share = C * feeRate * p * (1 - p)
где p — вероятность (цена), пик при p=0.5. C и кэпы зависят от категории.
"""
from __future__ import annotations

# Категорийные кэпы taker-fee ($ на 100 акций) — из docs.polymarket.com/trading/fees.
# Значения консервативные/округлённые; при уточнении в доках правь здесь.
CATEGORY_FEE_CAP_PER_100 = {
    "sports": 0.75,      # спорт: max $0.75 / 100 акций
    "crypto": 1.80,      # крипто: max $1.80 / 100 акций
    "geopolitics": 0.0,  # геополитика: 0%
    "politics": 0.0,     # политика часто 0%; уточнить под конкретный рынок
    "default": 1.80,     # неизвестная категория — берём худший (консервативно)
}

# feeRate внутри формулы; кэп всё равно ограничивает сверху.
BASE_FEE_RATE = 0.10


def taker_fee_per_share(price: float, category: str | None) -> float:
    """Комиссия тейкера на 1 акцию (в долларах) при данной цене и категории.

    Возвращает величину в [0, cap/100]. price ожидается в (0, 1).
    """
    p = min(max(price, 1e-6), 1 - 1e-6)
    key = category.lower() if isinstance(category, str) else "default"
    cap_per_100 = CATEGORY_FEE_CAP_PER_100.get(key, CATEGORY_FEE_CAP_PER_100["default"])
    cap_per_share = cap_per_100 / 100.0
    raw = BASE_FEE_RATE * p * (1 - p)
    return min(raw, cap_per_share)


def half_spread_cost(best_bid: float | None, best_ask: float | None) -> float:
    """Стоимость пересечения спреда на 1 акцию = (ask - bid) / 2.

    Прокси проскальзывания для taker-исполнения по рыночной цене.
    Если стакан неизвестен — возвращает 0 (стратегия сама решает, что делать).
    """
    if best_bid is None or best_ask is None:
        return 0.0
    spread = best_ask - best_bid
    return max(spread, 0.0) / 2.0


def round_trip_cost_per_share(price: float, category: str | None,
                              best_bid: float | None = None,
                              best_ask: float | None = None) -> float:
    """Полная издержка round-trip (вход taker + выход taker) на 1 акцию.

    2 * taker_fee + 2 * half_spread. Именно это должен перекрыть edge стратегии.
    """
    fee = taker_fee_per_share(price, category)
    slip = half_spread_cost(best_bid, best_ask)
    return 2 * fee + 2 * slip


if __name__ == "__main__":
    # sanity-чек: пик fee при p=0.5, геополитика = 0, кэп ограничивает
    print("p=0.5 crypto  :", round(taker_fee_per_share(0.5, "crypto"), 4))
    print("p=0.1 crypto  :", round(taker_fee_per_share(0.1, "crypto"), 4))
    print("p=0.5 geopol  :", round(taker_fee_per_share(0.5, "geopolitics"), 4))
    print("p=0.5 default :", round(taker_fee_per_share(0.5, None), 4))
    print("half-spread 0.53/0.55:", half_spread_cost(0.53, 0.55))
