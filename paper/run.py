"""CLI paper-трейдинга.

  python -m paper.run tick     # один проход: рассчитать созревшее + найти входы
  python -m paper.run report   # сводка P&L по журналу
  python -m paper.run loop --minutes 30   # tick каждые N минут (Ctrl+C для выхода)
"""
from __future__ import annotations

import argparse
import time

from paper import engine


def main() -> None:
    ap = argparse.ArgumentParser(description="Polymarket paper trading")
    sub = ap.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("tick")
    t.add_argument("--max-h2", type=int, default=15)
    t.add_argument("--max-h3", type=int, default=10)

    sub.add_parser("report")

    lp = sub.add_parser("loop")
    lp.add_argument("--minutes", type=int, default=30)
    lp.add_argument("--max-h2", type=int, default=15)
    lp.add_argument("--max-h3", type=int, default=10)

    a = ap.parse_args()
    if a.cmd == "tick":
        engine.tick(a.max_h2, a.max_h3)
    elif a.cmd == "report":
        engine.report()
    elif a.cmd == "loop":
        while True:
            try:
                engine.tick(a.max_h2, a.max_h3)
            except Exception as exc:  # сеть моргнула — не падаем, ждём следующий tick
                print(f"[loop] tick упал: {exc}")
            time.sleep(a.minutes * 60)


if __name__ == "__main__":
    main()
