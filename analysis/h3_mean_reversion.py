"""Гипотеза 3: mean-reversion в ценах Polymarket (репликация идеи QuantPedia).

QuantPedia сообщала о Sharpe ~2.97 БЕЗ издержек, но −2.60 уже при спреде 10 б.п.
Здесь проверяем на реальной истории: даёт ли контр-трендовый сигнал
положительную доходность ПОСЛЕ издержек.

Сигнал: если цена ушла ниже скользящего среднего больше чем на threshold —
покупаем (ставка на возврат вверх); держим holding баров; доход = изменение цены.
Каждую сделку штрафуем на round-trip издержки (2*taker_fee + 2*half_spread прокси).

Ограничение: минутная история есть только у активных рынков; у resolved — 12ч.
Это демонстрация метода на доступных данных, не финальный бэктест.

Запуск: python -m analysis.h3_mean_reversion [--window 20 --hold 5 --thr 0.03]
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

import config
from pm import costs
from analysis._common import load_markets, load_history


def backtest_market(hist: pd.DataFrame, category: str | None, *,
                    window: int, hold: int, thr: float) -> list[dict]:
    h = hist.sort_values("ts").reset_index(drop=True)
    if len(h) < window + hold + 2:
        return []
    p = h["price"].to_numpy(dtype=float)
    ma = pd.Series(p).rolling(window).mean().to_numpy()
    trades = []
    i = window
    while i < len(p) - hold:
        if not np.isnan(ma[i]) and 0.02 < p[i] < 0.98:
            deviation = p[i] - ma[i]
            if deviation < -thr:  # цена ниже среднего => ставим на возврат вверх (LONG)
                entry, exit_ = p[i], p[i + hold]
                gross = exit_ - entry
                cost = costs.round_trip_cost_per_share(entry, category)
                trades.append({"gross": gross, "net": gross - cost})
                i += hold  # не перекрываем сделки
                continue
        i += 1
    return trades


def run(window: int, hold: int, thr: float) -> pd.DataFrame:
    df = load_markets()
    all_trades: list[dict] = []
    used = 0
    for _, m in df.iterrows():
        hist = load_history(m["yes_token_id"])
        if hist is None or hist.empty:
            continue
        t = backtest_market(hist, m["category"], window=window, hold=hold, thr=thr)
        if t:
            all_trades.extend(t)
            used += 1

    res = pd.DataFrame(all_trades)
    print("\n=== H3: mean-reversion ===")
    print(f"Параметры: window={window}, hold={hold}, threshold={thr}")
    if len(res) < 30:
        print(f"Мало сделок ({len(res)}). Нужна более гранулярная история активных рынков: "
              f"python -m pm.collect --closed open --fidelity 60 --limit 500")
        return res

    def stats(col: str) -> str:
        x = res[col]
        mean = x.mean()
        sharpe = mean / x.std() * np.sqrt(len(x)) if x.std() > 0 else 0.0
        winrate = (x > 0).mean()
        return (f"сделок={len(x)}  ср.доход/сделку={mean*100:+.3f} цента  "
                f"winrate={winrate*100:.1f}%  t-стат~{sharpe:.2f}")

    print(f"Рынков задействовано: {used}")
    print("БЕЗ издержек: ", stats("gross"))
    print("ПОСЛЕ издержек:", stats("net"))
    net_mean = res["net"].mean()
    verdict = ("положительное" if net_mean > 0 else "ОТРИЦАТЕЛЬНОЕ")
    print(f">>> Мат.ожидание после издержек: {verdict} "
          f"({net_mean*100:+.3f} цента/сделку)")
    if net_mean <= 0 < res["gross"].mean():
        print("    Как и у QuantPedia: сырой сигнал есть, но издержки его съедают.")

    res.to_csv(config.OUTPUT_DIR / "h3_mean_reversion.csv", index=False)
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", type=int, default=20)
    ap.add_argument("--hold", type=int, default=5)
    ap.add_argument("--thr", type=float, default=0.03)
    a = ap.parse_args()
    run(a.window, a.hold, a.thr)
