# -*- coding: utf-8 -*-
"""H5: комбинаторный арбитраж связанных рынков одного матча.

Рынки одного спортивного события торгуются в изолированных стаканах,
но связаны строгой логикой:
  - Over(9.5) => Over(8.5)                  (лестницы тоталов/киллов)
  - cover(-2.5) => cover(-1.5)              (лестницы спредов/гандикапов)
  - cover(home -1.5) => home побеждает      (спред => moneyline)
  - cover(home) и cover(away) несовместны   (взаимоисключение спредов)

Если X => Y, то P(X) <= P(Y). Нарушение: bid(X) > ask(Y) — тогда
"купить Y-Yes + купить X-No" стоит < $1, а выплата гарантированно >= $1
(No-ask = 1 - Yes-bid: стакан Polymarket комплементарный).
Для несовместных X,Y: bid(X)+bid(Y) > 1 — купить оба No дешевле $1.

Лестницы группируются по "стему" слага (всё до числовой линии), поэтому
покрываются и нестандартные семейства: set-1-total-9pt5, game1-kill-over-25pt5,
set-handicap-home-1pt5 и т.п. Оценка — напрямую по живым стаканам CLOB
(батч POST /books), Gamma используется только для списка рынков.

Запуск (из корня проекта):
  python -m analysis.h5_combinatorial              # один скан
  python -m analysis.h5_combinatorial --watch 60   # скан каждые 60с (Ctrl+C)
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

import config
from pm.costs import taker_fee_per_share

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"

GAME_SLUG = re.compile(r"-\d{4}-\d{2}-\d{2}")
# лестницы: stem = всё до числовой линии в конце слага
LADDER_OVER = re.compile(r"^(?P<stem>.+-(?:totals?|kill-over|corners-over))-(?P<line>\d+)(?P<half>pt5)?$")
LADDER_COVER = re.compile(r"^(?P<stem>.+-(?:spread|handicap)-(?:home|away))-(?P<line>\d+)(?P<half>pt5)?$")
FULL_SPREAD = re.compile(r"-spread-(?P<side>home|away)-\d+(?:pt5)?$")

VIOLATIONS_CSV = Path(config.DATA_DIR) / "h5_violations.csv"
COLUMNS = [
    "ts", "event_slug", "check", "strong_slug", "weak_slug",
    "bid_strong", "ask_weak", "gross_gap", "net_gap", "exec_size",
]


# ---------------------------------------------------------------- сбор данных

def fetch_game_events(pages: int = 15, per_page: int = 100) -> list[dict]:
    """Открытые события-матчи (в слаге есть дата, 2+ живых рынка)."""
    out = []
    for i in range(pages):
        batch = None
        for attempt in range(3):  # глубокие offset'ы Gamma периодически таймаутят
            try:
                r = requests.get(
                    f"{GAMMA}/events",
                    params={"closed": "false", "limit": per_page, "offset": i * per_page,
                            "order": "volume", "ascending": "false"},
                    timeout=60,
                )
                r.raise_for_status()
                batch = r.json()
                break
            except requests.RequestException:
                time.sleep(2 * (attempt + 1))
        if not batch:
            break
        out.extend(batch)
        time.sleep(0.15)
    games = []
    for ev in out:
        if ev.get("negRisk") or not GAME_SLUG.search(ev.get("slug", "")):
            continue
        mkts = [m for m in ev.get("markets", [])
                if not m.get("closed") and m.get("acceptingOrders")]
        if len(mkts) >= 2:
            ev["_open_markets"] = mkts
            games.append(ev)
    return games


def get_books(token_ids: list[str]) -> dict[str, tuple]:
    """Живые стаканы CLOB батчем. -> {token_id: (bid, bid_sz, ask, ask_sz)}"""
    res: dict[str, tuple] = {}
    for i in range(0, len(token_ids), 50):
        chunk = token_ids[i:i + 50]
        try:
            r = requests.post(f"{CLOB}/books", json=[{"token_id": t} for t in chunk],
                              timeout=30)
        except requests.RequestException:
            continue
        if r.status_code != 200:
            continue
        for b in r.json():
            bids = [(float(x["price"]), float(x["size"])) for x in (b.get("bids") or [])]
            asks = [(float(x["price"]), float(x["size"])) for x in (b.get("asks") or [])]
            best_bid = max(bids) if bids else (None, 0.0)
            best_ask = min(asks) if asks else (None, 0.0)
            res[b["asset_id"]] = (best_bid[0], best_bid[1], best_ask[0], best_ask[1])
        time.sleep(0.1)
    return res


# ------------------------------------------------------------ разбор события

def yes_token(m: dict) -> str | None:
    try:
        return json.loads(m["clobTokenIds"])[0]
    except Exception:
        return None


def minus_line(m: dict) -> bool:
    """Спред/гандикап с '(-X)' в вопросе: большая линия = труднее (лестница валидна).
    Формат '(+X)' развернул бы монотонность — такие рынки пропускаем."""
    return "(+" not in (m.get("question") or "")


def parse_event(ev: dict) -> dict:
    """Раскладывает рынки матча на семейства лестниц по стему слага."""
    base = ev["slug"]
    parsed: dict = {"moneyline": None, "over": {}, "cover": {}, "unparsed": []}
    for m in ev["_open_markets"]:
        slug = m.get("slug", "")
        if slug == base:
            parsed["moneyline"] = m
            continue
        lo = LADDER_OVER.match(slug)
        if lo:
            line = float(lo["line"]) + (0.5 if lo["half"] else 0.0)
            parsed["over"].setdefault(lo["stem"], []).append((line, m))
            continue
        lc = LADDER_COVER.match(slug)
        if lc and minus_line(m):
            line = float(lc["line"]) + (0.5 if lc["half"] else 0.0)
            parsed["cover"].setdefault(lc["stem"], []).append((line, m))
            continue
        parsed["unparsed"].append(slug[len(base) + 1:] if slug.startswith(base + "-") else slug)
    return parsed


def ml_team_token(moneyline: dict, spread_q: str) -> str | None:
    """Токен исхода moneyline для команды из вопроса спреда 'Spread: TEAM (-1.5)'."""
    mt = re.match(r"Spread:\s*(.+?)\s*\(", spread_q or "")
    if not mt or not moneyline:
        return None
    team = mt.group(1).strip()
    try:
        outcomes = json.loads(moneyline["outcomes"])
        tokens = json.loads(moneyline["clobTokenIds"])
        return tokens[outcomes.index(team)]
    except (ValueError, KeyError, IndexError):
        return None


# -------------------------------------------------------------- сами проверки

def build_pairs(events: list[dict]) -> tuple[list[dict], list[str], dict]:
    """Все логические пары по всем матчам + список нужных токенов + диагностика."""
    pairs: list[dict] = []
    unparsed: dict[str, int] = {}

    def add(check, ev_slug, strong, weak, strong_tok, weak_tok):
        if strong_tok and weak_tok:
            pairs.append({"event": ev_slug, "check": check,
                          "strong_slug": strong.get("slug"), "weak_slug": weak.get("slug"),
                          "strong_tok": strong_tok, "weak_tok": weak_tok})

    for ev in events:
        p = parse_event(ev)
        for s in p["unparsed"]:
            key = re.sub(r"\d+", "N", s)
            unparsed[key] = unparsed.get(key, 0) + 1

        # лестницы: X(line_hi) => X(line_lo)
        for fam in ("over", "cover"):
            for stem, lst in p[fam].items():
                lst.sort(key=lambda t: t[0])
                for i, (lo_line, m_lo) in enumerate(lst):
                    for hi_line, m_hi in lst[i + 1:]:
                        add(f"{fam}_ladder", ev["slug"], m_hi, m_lo,
                            yes_token(m_hi), yes_token(m_lo))

        # спред полного матча => moneyline той же команды
        ml = p["moneyline"]
        if ml:
            for stem, lst in p["cover"].items():
                if not FULL_SPREAD.search(stem + "-1pt5") or "-f5-" in stem or "handicap" in stem:
                    continue
                for line, m_sp in lst:
                    tok = ml_team_token(ml, m_sp.get("question"))
                    if tok:
                        add("spread_implies_ml", ev["slug"], m_sp, ml, yes_token(m_sp), tok)

        # несовместные спреды home/away (пары стемов, отличающихся стороной)
        for stem_h, lst_h in p["cover"].items():
            if "-home" not in stem_h:
                continue
            lst_a = p["cover"].get(stem_h.replace("-home", "-away"))
            if not lst_a:
                continue
            for lh, mh in lst_h:
                for la, ma in lst_a:
                    add("spread_exclusive", ev["slug"], mh, ma, yes_token(mh), yes_token(ma))

    toks = sorted({q["strong_tok"] for q in pairs} | {q["weak_tok"] for q in pairs})
    return pairs, toks, unparsed


def evaluate(pairs: list[dict], books: dict[str, tuple]) -> list[dict]:
    """Проверка пар по живым стаканам, расчёт gross/net (комиссии обеих ног)."""
    rows = []
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for c in pairs:
        bs, bw = books.get(c["strong_tok"]), books.get(c["weak_tok"])
        if not bs or not bw:
            continue
        bid_s, bid_s_sz, _, _ = bs
        bid_w, bid_w_sz, ask_w, ask_w_sz = bw
        if c["check"] == "spread_exclusive":
            if bid_s is None or bid_w is None:
                continue
            gross = bid_s + bid_w - 1.0
            size = min(bid_s_sz, bid_w_sz)
            fees = taker_fee_per_share(1 - bid_s, "sports") + taker_fee_per_share(1 - bid_w, "sports")
            px = (bid_s, bid_w)
        else:
            if bid_s is None or ask_w is None:
                continue
            gross = bid_s - ask_w
            size = min(bid_s_sz, ask_w_sz)
            fees = taker_fee_per_share(ask_w, "sports") + taker_fee_per_share(1 - bid_s, "sports")
            px = (bid_s, ask_w)
        if gross > 0:
            rows.append({
                "ts": ts, "event_slug": c["event"], "check": c["check"],
                "strong_slug": c["strong_slug"], "weak_slug": c["weak_slug"],
                "bid_strong": round(px[0], 4), "ask_weak": round(px[1], 4),
                "gross_gap": round(gross, 4), "net_gap": round(gross - fees, 4),
                "exec_size": round(size, 1),
            })
    return rows


def append_csv(rows: list[dict]) -> None:
    VIOLATIONS_CSV.parent.mkdir(parents=True, exist_ok=True)
    new_file = not VIOLATIONS_CSV.exists()
    with VIOLATIONS_CSV.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        if new_file:
            w.writeheader()
        w.writerows(rows)


# ------------------------------------------------------------------------ CLI

def scan_once(cache: dict | None = None, verbose: bool = True) -> list[dict]:
    """cache (для watch-режима) хранит pairs/toks между сканами: список матчей
    обновляется раз в ~10 минут, стаканы — каждый скан."""
    now = time.time()
    if not cache or now - cache.get("ts", 0) > 600:
        events = fetch_game_events()
        pairs, toks, unparsed = build_pairs(events)
        if cache is not None:
            cache.update(ts=now, pairs=pairs, toks=toks, n_events=len(events))
        if verbose:
            print(f"матчей: {len(events)}, логических пар: {len(pairs)}, токенов: {len(toks)}")
            top = sorted(unparsed.items(), key=lambda kv: -kv[1])[:8]
            if top:
                print("непокрытые типы:", ", ".join(f"{k}×{v}" for k, v in top))
    else:
        pairs, toks = cache["pairs"], cache["toks"]

    books = get_books(toks)
    rows = evaluate(pairs, books)
    if rows:
        append_csv(rows)
    if verbose:
        print(f"стаканов получено: {len(books)}, нарушений: {len(rows)} "
              f"(из них net>0: {sum(1 for r in rows if r['net_gap'] > 0)})")
        for r in rows:
            print(f"  [{r['check']}] gross={r['gross_gap']:+.3f} net={r['net_gap']:+.3f} "
                  f"size={r['exec_size']:.0f}  {r['strong_slug']}  >  {r['weak_slug']}")
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description="H5: комбинаторный арбитраж внутри матча")
    ap.add_argument("--watch", type=int, default=0, metavar="SEC",
                    help="повторять скан каждые SEC секунд (0 = один раз)")
    a = ap.parse_args()
    if a.watch <= 0:
        scan_once()
        return
    cache: dict = {}
    while True:
        try:
            print(f"--- скан {datetime.now(timezone.utc).strftime('%H:%M:%SZ')} ---")
            scan_once(cache)
        except Exception as exc:
            print(f"[watch] скан упал: {exc}")
        time.sleep(a.watch)


if __name__ == "__main__":
    main()
