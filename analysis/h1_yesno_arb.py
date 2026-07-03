"""Гипотеза 1: внутренний арбитраж Yes+No.

Если купить Yes по ask и No по ask, при разрешении рынка гарантированно
получаешь $1 за пару. Значит при ask(Yes)+ask(No) < 1 есть арбитраж —
ЕСЛИ он переживает taker-комиссии на обе ноги.

Берём живой снапшот стакана по активным рынкам (CLOB /price side=SELL).
Edge на пару = 1 - ask_yes - ask_no - fee_yes - fee_no.

Запуск: python -m analysis.h1_yesno_arb [--max-markets 200]
"""
from __future__ import annotations

import argparse

import pandas as pd
from tqdm import tqdm

import config
from pm import costs
from pm.client import PolymarketClient
from analysis._common import load_markets


def run(max_markets: int) -> pd.DataFrame:
    df = load_markets()
    active = df[(df["active"] == True) & (df["closed"] == False)].copy()  # noqa: E712
    active = active.head(max_markets)
    client = PolymarketClient()

    rows = []
    for _, m in tqdm(active.iterrows(), total=len(active), desc="H1 arb snapshot"):
        yes_ask = client.get_price(m["yes_token_id"], "SELL")
        no_ask = client.get_price(m["no_token_id"], "SELL")
        if yes_ask is None or no_ask is None or yes_ask <= 0 or no_ask <= 0:
            continue
        gross_edge = 1.0 - (yes_ask + no_ask)  # >0 => сырой арбитраж
        fee = (costs.taker_fee_per_share(yes_ask, m["category"])
               + costs.taker_fee_per_share(no_ask, m["category"]))
        net_edge = gross_edge - fee
        rows.append({
            "slug": m["slug"], "category": m["category"],
            "yes_ask": yes_ask, "no_ask": no_ask,
            "sum_ask": yes_ask + no_ask,
            "gross_edge": gross_edge, "fee": fee, "net_edge": net_edge,
            "liquidity": m["liquidity"], "volume": m["volume"],
        })

    res = pd.DataFrame(rows)
    if res.empty:
        print("H1: нет данных стакана (рынки без ликвидности?)")
        return res

    gross_pos = res[res["gross_edge"] > 0]
    net_pos = res[res["net_edge"] > 0]
    print("\n=== H1: Yes+No арбитраж ===")
    print(f"Рынков проверено:            {len(res)}")
    print(f"Сырой арбитраж (sum_ask<1):  {len(gross_pos)} "
          f"({100*len(gross_pos)/len(res):.1f}%)")
    print(f"После taker-комиссий:        {len(net_pos)} "
          f"({100*len(net_pos)/len(res):.1f}%)")
    if len(net_pos):
        print(f"Медианный net-edge (там где >0): {net_pos['net_edge'].median()*100:.2f} центов/пару")
        print("Топ-5 возможностей после издержек:")
        cols = ["slug", "sum_ask", "net_edge", "liquidity"]
        print(net_pos.sort_values("net_edge", ascending=False)[cols].head().to_string(index=False))

    out = config.OUTPUT_DIR / "h1_yesno_arb.csv"
    res.sort_values("net_edge", ascending=False).to_csv(out, index=False)
    print(f"Детали -> {out}")
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-markets", type=int, default=200)
    a = ap.parse_args()
    run(a.max_markets)
