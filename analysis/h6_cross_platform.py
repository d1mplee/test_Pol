# -*- coding: utf-8 -*-
"""H6: расхождение цен Polymarket <-> Kalshi (law of one price).

Экономически идентичные контракты на двух площадках торгуются в изолированных
стаканах без общего клиринга. arXiv 2601.01706: медианное расхождение 2-4%,
структурно устойчиво (для арбитража нужен капитал на обеих биржах — боты
это не выедают). Мы read-only: меряем величину и стойкость расхождений.

Сид-набор пар — заседания ФРС (одинаковые взаимоисключающие исходы,
сопоставление по стабильным суффиксам тикеров Kalshi, а не по тексту).
Семантические ловушки типа "коснётся X" (touch) vs "цена на дату >= X"
(terminal) в сид-набор сознательно не включаются.

Метрики на исход:
  mid_gap   = mid_kalshi - mid_pm (знак: где дороже)
  edge_буду = исполнимое расхождение: купить Yes на дешёвой площадке по ask,
              купить No на дорогой по (1 - bid), минус комиссия Kalshi
              (taker 0.07*p*(1-p); Polymarket политика = 0%).
              edge > 0 => арбитраж для того, у кого есть счета на обеих.

Запуск: python -m analysis.h6_cross_platform
"""
from __future__ import annotations

import csv
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

import config
from pm import kalshi

GAMMA = "https://gamma-api.polymarket.com"
_SESSION = requests.Session()

CSV_PATH = Path(config.DATA_DIR) / "h6_divergences.csv"
COLUMNS = ["ts", "pair", "outcome", "pm_bid", "pm_ask", "k_bid", "k_ask",
           "mid_gap", "edge_buy_pm", "edge_buy_k"]

# суффикс тикера Kalshi -> groupItemTitle Polymarket (серия fed-decision)
FED_OUTCOMES = {
    "H0": "No change",
    "H25": "25 bps increase",
    "H26": "50+ bps increase",
    "C25": "25 bps decrease",
    "C26": "50+ bps decrease",
}

PAIRS = [
    {"name": "fed-jul26", "pm_event": "fed-decision-in-july-181",
     "kalshi_event": "KXFEDDECISION-26JUL", "outcomes": FED_OUTCOMES},
    {"name": "fed-sep26", "pm_event": "fed-decision-in-september-762",
     "kalshi_event": "KXFEDDECISION-26SEP", "outcomes": FED_OUTCOMES},
    {"name": "fed-oct26", "pm_event": "fed-decision-in-october-20260617190323537",
     "kalshi_event": "KXFEDDECISION-26OCT", "outcomes": FED_OUTCOMES},
]


def pm_event_quotes(slug: str) -> dict[str, tuple[float, float]]:
    """{groupItemTitle: (bid, ask)} по открытым рынкам события Polymarket."""
    r = _SESSION.get(f"{GAMMA}/events", params={"slug": slug}, timeout=30)
    r.raise_for_status()
    evs = r.json()
    out: dict[str, tuple[float, float]] = {}
    if not evs:
        return out
    for m in evs[0].get("markets", []):
        if m.get("closed") or not m.get("acceptingOrders"):
            continue
        if m.get("bestBid") is None or m.get("bestAsk") is None:
            continue
        title = m.get("groupItemTitle") or m.get("question") or ""
        out[title] = (float(m["bestBid"]), float(m["bestAsk"]))
    return out


def scan_pair(pair: dict) -> list[dict]:
    pm_q = pm_event_quotes(pair["pm_event"])
    k_markets = {m["ticker"].rsplit("-", 1)[-1]: m
                 for m in kalshi.get_markets(event_ticker=pair["kalshi_event"])
                 if m.get("status") == "active"}
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = []
    for suffix, pm_title in pair["outcomes"].items():
        km = k_markets.get(suffix)
        pm = pm_q.get(pm_title)
        if not km or not pm:
            continue
        k_bid, k_ask = kalshi.quotes(km)
        pm_bid, pm_ask = pm
        if k_bid is None or k_ask is None:
            continue
        mid_gap = (k_bid + k_ask) / 2 - (pm_bid + pm_ask) / 2
        # купить Yes на PM по ask + No на Kalshi по (1-bid): выплата $1 ровно
        # одному из них; комиссия Kalshi считается от цены её ноги
        edge_buy_pm = (1.0 - (pm_ask + (1.0 - k_bid))) - kalshi.taker_fee_per_share(1.0 - k_bid)
        edge_buy_k = (1.0 - (k_ask + (1.0 - pm_bid))) - kalshi.taker_fee_per_share(k_ask)
        rows.append({
            "ts": ts, "pair": pair["name"], "outcome": pm_title,
            "pm_bid": pm_bid, "pm_ask": pm_ask, "k_bid": k_bid, "k_ask": k_ask,
            "mid_gap": round(mid_gap, 4),
            "edge_buy_pm": round(edge_buy_pm, 4),
            "edge_buy_k": round(edge_buy_k, 4),
        })
    return rows


def append_csv(rows: list[dict]) -> None:
    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    new_file = not CSV_PATH.exists()
    with CSV_PATH.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        if new_file:
            w.writeheader()
        w.writerows(rows)


def main() -> None:
    all_rows = []
    for pair in PAIRS:
        try:
            rows = scan_pair(pair)
        except requests.RequestException as exc:
            print(f"[{pair['name']}] сеть: {exc}")
            continue
        all_rows.extend(rows)
        print(f"--- {pair['name']} ({len(rows)} исходов) ---")
        for r in rows:
            flag = ""
            if r["edge_buy_pm"] > 0:
                flag = f"  <<< АРБ: PM-Yes@{r['pm_ask']} + K-No@{1-r['k_bid']:.2f}"
            elif r["edge_buy_k"] > 0:
                flag = f"  <<< АРБ: K-Yes@{r['k_ask']} + PM-No@{1-r['pm_bid']:.3f}"
            print(f"  {r['outcome']:18s} PM {r['pm_bid']:.3f}/{r['pm_ask']:.3f}  "
                  f"K {r['k_bid']:.2f}/{r['k_ask']:.2f}  mid_gap={r['mid_gap']:+.3f}{flag}")
    if all_rows:
        append_csv(all_rows)
        n_arb = sum(1 for r in all_rows if r["edge_buy_pm"] > 0 or r["edge_buy_k"] > 0)
        big = max(all_rows, key=lambda r: abs(r["mid_gap"]))
        print(f"\nисходов сравнено: {len(all_rows)}, исполнимых арбитражей: {n_arb}, "
              f"макс |mid_gap|: {big['mid_gap']:+.3f} ({big['pair']}/{big['outcome']})")


if __name__ == "__main__":
    main()
