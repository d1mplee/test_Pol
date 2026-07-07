"""Paper-trading движок: виртуальные сделки по РЕАЛЬНЫМ котировкам, без денег.

Два сигнала из оффлайн-исследования (analysis/):
  H2-live "buy-No": на спортивных микро-рынках Yes с implied 30-70% переоценён
                    -> виртуально покупаем No по реальному ask, держим до резолва.
  H3-live "mean-rev": цена ушла ниже MA(10) на >=5 центов -> виртуально покупаем
                    Yes по реальному ask, выходим через HOLD_HOURS по реальной цене.

Журнал: data/paper_positions.csv. Все цены — реальные котировки CLOB на момент
входа/выхода; вход по ask (пересекаем спред честно), fee по модели pm.costs.
Один tick = один проход: настелить новые позиции + рассчитать созревшие.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd

import config
from pm import costs
from pm.client import PolymarketClient

JOURNAL = config.DATA_DIR / "paper_positions.csv"

COLUMNS = ["id", "strategy", "condition_id", "slug", "side", "token_id",
           "entry_ts", "entry_price", "fee", "status",
           "exit_ts_planned", "exit_ts", "exit_price", "pnl_net", "note"]

# паттерн спортивных микро-рынков (тот же, что дал сигнал в analysis/h2)
SPORTS_MICRO = re.compile(
    r"atp-|wta-|exact-score|spread-(home|away)|halftime|cs2-|corners|"
    r"-total-\d|first-set|team-total")

# параметры сигналов (из свипа analysis/h3: window=10, thr=0.05, hold=5)
H2_MIN, H2_MAX = 0.30, 0.70       # диапазон implied Yes для buy-No
H2_MAX_SPREAD = 0.05              # не входим в мёртвый стакан
H3_WINDOW = 10
H3_THR = 0.05
H3_HOLD_HOURS = 5
H3_MIN_LIQUIDITY = 1000           # $; чтобы котировки были живыми


def now_ts() -> int:
    return int(time.time())


def load_journal() -> pd.DataFrame:
    if JOURNAL.exists():
        return pd.read_csv(JOURNAL, dtype={"condition_id": str, "token_id": str})
    return pd.DataFrame(columns=COLUMNS)


def save_journal(df: pd.DataFrame) -> None:
    df.to_csv(JOURNAL, index=False)


def _next_id(df: pd.DataFrame) -> int:
    return int(df["id"].max()) + 1 if len(df) else 1


def scan_active_markets(client: PolymarketClient, target: int = 2000) -> list[dict]:
    """Пагинация по фактическому размеру батча (Gamma может отдавать <limit)."""
    markets: list[dict] = []
    offset = 0
    while len(markets) < target:
        batch = client.get_markets(closed=False, active=True, order="volume",
                                   limit=500, offset=offset)
        if not batch:
            break
        markets.extend(batch)
        offset += len(batch)
    return [m for m in markets if m["yes_token_id"] and m["no_token_id"]]


# ---------------------------------------------------------------- H2: buy-No
def open_h2_positions(client: PolymarketClient, journal: pd.DataFrame,
                      markets: list[dict], max_new: int) -> pd.DataFrame:
    have = set(journal.loc[journal["strategy"] == "h2_buy_no", "condition_id"])
    opened = 0
    for m in markets:
        if opened >= max_new:
            break
        if m["condition_id"] in have:
            continue
        if not SPORTS_MICRO.search(str(m["slug"])):
            continue
        try:
            yes_bid = client.get_price(m["yes_token_id"], "BUY")
            yes_ask = client.get_price(m["yes_token_id"], "SELL")
            if not yes_bid or not yes_ask or (yes_ask - yes_bid) > H2_MAX_SPREAD:
                continue
            implied = (yes_bid + yes_ask) / 2
            if not (H2_MIN <= implied <= H2_MAX):
                continue
            no_ask = client.get_price(m["no_token_id"], "SELL")
        except RuntimeError:
            continue  # сеть моргнула — пропускаем рынок, не роняем tick
        if not no_ask or no_ask >= 0.99:
            continue
        fee = costs.taker_fee_per_share(no_ask, "sports")
        row = {
            "id": _next_id(journal), "strategy": "h2_buy_no",
            "condition_id": m["condition_id"], "slug": m["slug"],
            "side": "NO", "token_id": m["no_token_id"],
            "entry_ts": now_ts(), "entry_price": no_ask, "fee": fee,
            "status": "open", "exit_ts_planned": "", "exit_ts": "",
            "exit_price": "", "pnl_net": "",
            "note": f"implied_yes={implied:.3f} spread={yes_ask-yes_bid:.3f}",
        }
        journal = pd.concat([journal, pd.DataFrame([row])], ignore_index=True)
        opened += 1
        print(f"  [H2 OPEN] No@{no_ask:.3f} implied_yes={implied:.2f}  {m['slug']}")
    return journal


def settle_h2(client: PolymarketClient, journal: pd.DataFrame) -> pd.DataFrame:
    mask = (journal["strategy"] == "h2_buy_no") & (journal["status"] == "open")
    for idx in journal.index[mask]:
        slug = journal.at[idx, "slug"]
        try:
            m = client.get_market_by_condition_id(journal.at[idx, "condition_id"])
        except RuntimeError:
            continue  # сеть моргнула — не роняем весь tick, попробуем в следующий
        if not m or not m.get("closed"):
            continue
        prices = m.get("outcome_prices") or []
        if len(prices) < 2 or prices[0] is None:
            continue
        yes_p, no_p = prices[0], prices[1]
        if yes_p >= 0.95 and no_p <= 0.05:
            payout = 0.0   # Yes выиграл -> наш No сгорел
        elif no_p >= 0.95 and yes_p <= 0.05:
            payout = 1.0   # No выиграл
        else:
            continue       # резолв неоднозначен, ждём
        entry = float(journal.at[idx, "entry_price"])
        fee = float(journal.at[idx, "fee"])
        pnl = payout - entry - fee
        journal.loc[idx, ["status", "exit_ts", "exit_price", "pnl_net"]] = \
            ["settled", now_ts(), payout, round(pnl, 4)]
        print(f"  [H2 SETTLE] pnl={pnl*100:+.1f}ц  {slug}")
    return journal


# ---------------------------------------------------------- H3: mean-reversion
def open_h3_positions(client: PolymarketClient, journal: pd.DataFrame,
                      markets: list[dict], max_new: int) -> pd.DataFrame:
    have = set(journal.loc[(journal["strategy"] == "h3_meanrev")
                           & (journal["status"] == "open"), "condition_id"])
    opened = 0
    for m in markets:
        if opened >= max_new:
            break
        if m["condition_id"] in have:
            continue
        if (m["liquidity"] or 0) < H3_MIN_LIQUIDITY:
            continue
        hist = client.get_prices_history(m["yes_token_id"], interval="1d", fidelity=60)
        if len(hist) < H3_WINDOW + 1:
            continue
        p = np.array([h["p"] for h in hist], dtype=float)
        ma = p[-H3_WINDOW:].mean()
        last = p[-1]
        if not (0.02 < last < 0.98) or (last - ma) >= -H3_THR:
            continue
        yes_ask = client.get_price(m["yes_token_id"], "SELL")
        if not yes_ask or yes_ask >= 0.99:
            continue
        fee = costs.taker_fee_per_share(yes_ask, m["category"])
        row = {
            "id": _next_id(journal), "strategy": "h3_meanrev",
            "condition_id": m["condition_id"], "slug": m["slug"],
            "side": "YES", "token_id": m["yes_token_id"],
            "entry_ts": now_ts(), "entry_price": yes_ask, "fee": fee,
            "status": "open",
            "exit_ts_planned": now_ts() + H3_HOLD_HOURS * 3600,
            "exit_ts": "", "exit_price": "", "pnl_net": "",
            "note": f"last={last:.3f} ma{H3_WINDOW}={ma:.3f} dev={last-ma:+.3f}",
        }
        journal = pd.concat([journal, pd.DataFrame([row])], ignore_index=True)
        opened += 1
        print(f"  [H3 OPEN] Yes@{yes_ask:.3f} dev={last-ma:+.3f}  {m['slug']}")
    return journal


def settle_h3(client: PolymarketClient, journal: pd.DataFrame) -> pd.DataFrame:
    mask = (journal["strategy"] == "h3_meanrev") & (journal["status"] == "open")
    for idx in journal.index[mask]:
        planned = float(journal.at[idx, "exit_ts_planned"] or 0)
        if now_ts() < planned:
            continue
        token = journal.at[idx, "token_id"]
        # выходим по реальному bid (продаём как taker)
        try:
            bid = client.get_price(token, "BUY")
            if bid is None:
                # рынок мог зарезолвиться — пробуем по финальной цене
                m = client.get_market_by_condition_id(journal.at[idx, "condition_id"])
                if m and m.get("closed") and m.get("outcome_prices"):
                    bid = m["outcome_prices"][0]
                else:
                    continue
        except RuntimeError:
            continue  # сеть моргнула — попробуем в следующий tick
        entry = float(journal.at[idx, "entry_price"])
        fee_in = float(journal.at[idx, "fee"])
        fee_out = costs.taker_fee_per_share(bid, None) if 0 < bid < 1 else 0.0
        pnl = bid - entry - fee_in - fee_out
        journal.loc[idx, ["status", "exit_ts", "exit_price", "pnl_net"]] = \
            ["settled", now_ts(), bid, round(pnl, 4)]
        print(f"  [H3 SETTLE] exit@{bid:.3f} pnl={pnl*100:+.1f}ц  {journal.at[idx,'slug']}")
    return journal


# ------------------------------------------------------------------- tick
# H3 ВЫКЛЮЧЕН по итогам paper-теста 2026-07-07: 265 сделок, -7.7ц/акцию,
# winrate 17%, t=-4.5 — статистически подтверждённый убыток. Легаси-позиции
# продолжают рассчитываться через settle_h3, новые не открываются.
def tick(max_new_h2: int = 15, max_new_h3: int = 0) -> None:
    client = PolymarketClient()
    journal = load_journal()
    print(f"[tick {datetime.now(timezone.utc):%Y-%m-%d %H:%M}Z] "
          f"позиций в журнале: {len(journal)}")

    print("— расчёт созревших позиций —")
    journal = settle_h2(client, journal)
    journal = settle_h3(client, journal)

    print("— поиск новых входов —")
    markets = scan_active_markets(client)
    print(f"  активных рынков просканировано: {len(markets)}")
    journal = open_h2_positions(client, journal, markets, max_new_h2)
    journal = open_h3_positions(client, journal, markets, max_new_h3)

    save_journal(journal)
    n_open = (journal["status"] == "open").sum()
    n_set = (journal["status"] == "settled").sum()
    print(f"итого: open={n_open} settled={n_set} -> {JOURNAL}")


def report() -> None:
    journal = load_journal()
    if journal.empty:
        print("Журнал пуст — запусти сначала: python -m paper.run tick")
        return
    print(f"Журнал: {len(journal)} позиций "
          f"(open={(journal['status']=='open').sum()}, "
          f"settled={(journal['status']=='settled').sum()})")
    # сводка по открытым: когда ждать расчётов
    open_pos = journal[journal["status"] == "open"]
    if len(open_pos):
        ts = now_ts()
        for strat, g in open_pos.groupby("strategy"):
            line = f"открыто {strat}: {len(g)}"
            if strat == "h3_meanrev":
                ep = pd.to_numeric(g["exit_ts_planned"], errors="coerce")
                due = (ep <= ts).sum()
                nxt = (ep[ep > ts].min() - ts) / 60 if (ep > ts).any() else None
                line += f"  (созрело: {due}"
                line += f", ближайший выход через {nxt:.0f} мин)" if nxt else ")"
            else:
                line += "  (расчёт по мере резолва рынков — спорт обычно в день матча)"
            print(line)
    settled = journal[journal["status"] == "settled"].copy()
    if settled.empty:
        print("Рассчитанных позиций пока нет — жди резолвов, tick рассчитает их сам.")
        return
    settled["pnl_net"] = settled["pnl_net"].astype(float)
    for strat, g in settled.groupby("strategy"):
        pnl = g["pnl_net"]
        t = pnl.mean() / pnl.std() * np.sqrt(len(pnl)) if pnl.std() > 0 else 0.0
        print(f"\n{strat}: сделок={len(g)}  "
              f"ср.pnl={pnl.mean()*100:+.2f}ц/акцию  "
              f"winrate={(pnl>0).mean()*100:.0f}%  t~{t:.2f}  "
              f"сумма (на 100 акций/сделку)=${pnl.sum()*100:+.2f}")
