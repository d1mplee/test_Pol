"""Гипотеза 2: favorite-longshot bias на resolved-рынках.

Идея: сравнить рыночную вероятность (цену Yes) с фактической частотой победы Yes.
Если рынок эффективен и калиброван — точки лежат на диагонали. Systematic bias:
longshots (низкая цена) выигрывают РЕЖЕ, чем стоят; favourites (высокая цена) —
ЧАЩЕ. Это и есть эксплуатируемое смещение.

Метод (одна точка на рынок, чтобы не завышать N автокоррелированными свечами):
implied prob = медианная историческая цена Yes-токена; realized = победил ли Yes.
Ограничение: для resolved-рынков доступны только 12ч-свечи (fidelity>=720).

Запуск: python -m analysis.h2_longshot_bias
"""
from __future__ import annotations

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

import config
from pm import costs
from analysis._common import load_markets, load_history, winner_is_yes


def run() -> pd.DataFrame:
    df = load_markets()
    closed = df[df["closed"] == True].copy()  # noqa: E712

    rows = []
    for _, m in closed.iterrows():
        won = winner_is_yes(m)
        if won is None:
            continue
        hist = load_history(m["yes_token_id"])
        if hist is None or hist.empty:
            continue
        # implied = медиана ПЕРВОЙ ПОЛОВИНЫ истории: исключаем финальную сходимость
        # цены к 0/1 у резолва, иначе implied "подсматривает" исход (leakage).
        h = hist.sort_values("ts")
        early = h.iloc[: max(1, len(h) // 2)]
        implied = float(np.median(early["price"]))
        if not (0.02 < implied < 0.98):
            continue  # у краёв нет ставки и данные шумные
        rows.append({"slug": m["slug"], "category": m["category"],
                     "implied": implied, "won": int(won)})

    res = pd.DataFrame(rows)
    print("\n=== H2: favorite-longshot bias ===")
    if len(res) < 20:
        print(f"Мало resolved-рынков с историей ({len(res)}). "
              f"Собери больше: python -m pm.collect --closed closed --fidelity 720 --limit 800")
        return res

    # калибровка по децилям вероятности
    bins = np.linspace(0, 1, 11)
    res["bucket"] = pd.cut(res["implied"], bins, include_lowest=True)
    grp = res.groupby("bucket", observed=True).agg(
        implied_mean=("implied", "mean"),
        realized=("won", "mean"),
        n=("won", "size")).dropna()

    print(f"Рынков в выборке: {len(res)}")
    print(grp.assign(
        implied_mean=lambda d: (d["implied_mean"]*100).round(1),
        realized=lambda d: (d["realized"]*100).round(1)).to_string())

    # мера смещения: средний |realized - implied| и знак по краям
    grp2 = grp.reset_index()
    low = grp2[grp2["implied_mean"] < 0.2]
    high = grp2[grp2["implied_mean"] > 0.8]
    if len(low):
        d = (low["realized"] - low["implied_mean"]).mean()
        print(f"Longshots (implied<20%): realized - implied = {d*100:+.1f} п.п. "
              f"(<0 => longshots переоценены, классический bias)")
    if len(high):
        d = (high["realized"] - high["implied_mean"]).mean()
        print(f"Favourites (implied>80%): realized - implied = {d*100:+.1f} п.п. "
              f"(>0 => favourites недооценены)")

    # можно ли на этом заработать после издержек? Грубая оценка edge стратегии
    # "ставить на favourites": ожидаемый выигрыш на акцию против типичной комиссии.
    fee_ref = costs.taker_fee_per_share(0.85, "default")
    print(f"Ориентир taker-fee у favourites (p~0.85): {fee_ref*100:.2f} цента/акцию — "
          f"сравни с найденным смещением выше.")

    # график калибровки
    fig, ax = plt.subplots(figsize=(6, 6))
    ax.plot([0, 1], [0, 1], "k--", lw=1, label="идеальная калибровка")
    ax.scatter(grp["implied_mean"], grp["realized"], s=grp["n"]*3,
               color="#2b6cb0", zorder=3, label="дециль (размер ~ N)")
    ax.set_xlabel("Рыночная вероятность (implied)")
    ax.set_ylabel("Фактическая частота победы Yes")
    ax.set_title("H2: калибровка Polymarket (favorite-longshot bias)")
    ax.legend()
    out = config.OUTPUT_DIR / "h2_calibration.png"
    fig.savefig(out, dpi=110, bbox_inches="tight")
    print(f"График -> {out}")
    res.to_csv(config.OUTPUT_DIR / "h2_longshot.csv", index=False)
    return res


if __name__ == "__main__":
    run()
