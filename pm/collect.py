"""Сбор данных Polymarket на диск (идемпотентно, с кэшем).

Скачивает:
  1. Метаданные рынков (Gamma) -> data/markets.parquet
  2. Историю цен по Yes-токену каждого рынка (CLOB) -> data/history/<token>.parquet

Запуск:
    python -m pm.collect --limit 300 --closed both --fidelity 60
"""
from __future__ import annotations

import argparse
import sys

import pandas as pd
from tqdm import tqdm

import config
from pm.client import PolymarketClient


def collect_markets(client: PolymarketClient, *, target: int, which: str) -> pd.DataFrame:
    """Собирает до `target` рынков. which: 'open' | 'closed' | 'both'."""
    rows: list[dict] = []
    specs: list[tuple[bool | None, bool | None]] = []
    if which in ("open", "both"):
        specs.append((False, True))     # closed=False, active=True
    if which in ("closed", "both"):
        specs.append((True, None))      # closed=True (resolved)

    per_spec = target // len(specs)
    for closed_flag, active_flag in specs:
        offset = 0
        got = 0
        pbar = tqdm(total=per_spec, desc=f"markets closed={closed_flag}")
        while got < per_spec:
            batch = client.get_markets(closed=closed_flag, active=active_flag,
                                       order="volume", limit=500, offset=offset)
            if not batch:
                break
            for m in batch:
                # нужен бинарный рынок с двумя токенами
                if m["yes_token_id"] and m["no_token_id"]:
                    rows.append(m)
                    got += 1
                    pbar.update(1)
                    if got >= per_spec:
                        break
            offset += 500
        pbar.close()

    df = pd.DataFrame(rows).drop_duplicates(subset=["condition_id"])
    # списки -> строки, чтобы parquet не ругался на смешанные типы
    for col in ("outcomes", "outcome_prices", "resolved_prices"):
        if col in df.columns:
            df[col] = df[col].apply(lambda v: v if isinstance(v, list) else [])
    df.to_parquet(config.MARKETS_FILE, index=False)
    print(f"Сохранено рынков: {len(df)} -> {config.MARKETS_FILE}")
    return df


def collect_history(client: PolymarketClient, df: pd.DataFrame, *,
                    fidelity: int, interval: str) -> None:
    """Скачивает историю цен по Yes-токену каждого рынка (с кэшем на диске)."""
    for _, row in tqdm(df.iterrows(), total=len(df), desc="price history"):
        token = row["yes_token_id"]
        if not token:
            continue
        out = config.HISTORY_DIR / f"{token}.parquet"
        if out.exists():
            continue  # уже скачано — идемпотентность
        # для resolved-рынков минутная история недоступна: fidelity>=720
        fid = fidelity
        if row.get("closed") and fidelity < 720:
            fid = 720
        try:
            hist = client.get_prices_history(token, interval=interval, fidelity=fid)
        except RuntimeError:
            continue
        if not hist:
            continue
        h = pd.DataFrame(hist)
        h = h.rename(columns={"t": "ts", "p": "price"})
        h["condition_id"] = row["condition_id"]
        h.to_parquet(out, index=False)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Сбор публичных данных Polymarket")
    ap.add_argument("--limit", type=int, default=300, help="сколько рынков собрать")
    ap.add_argument("--closed", choices=["open", "closed", "both"], default="both")
    ap.add_argument("--fidelity", type=int, default=60, help="бакет истории в минутах")
    ap.add_argument("--interval", default="max", help="max|all|1w|1d|6h|1h")
    ap.add_argument("--no-history", action="store_true", help="только метаданные")
    args = ap.parse_args(argv)

    client = PolymarketClient()
    df = collect_markets(client, target=args.limit, which=args.closed)
    if not args.no_history:
        collect_history(client, df, fidelity=args.fidelity, interval=args.interval)
        print(f"История сохранена в {config.HISTORY_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
