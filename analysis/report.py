"""Сводный отчёт: прогоняет три гипотезы и печатает итог go/no-go.

Запуск: python -m analysis.report
Предполагает, что данные уже собраны (python -m pm.collect).
"""
from __future__ import annotations

from analysis import h1_yesno_arb, h2_longshot_bias, h3_mean_reversion


def main() -> None:
    print("#" * 60)
    print("# ИТОГОВЫЙ ОТЧЁТ: есть ли эксплуатируемый edge на Polymarket?")
    print("#" * 60)

    h1 = h1_yesno_arb.run(max_markets=200)
    h2 = h2_longshot_bias.run()
    h3 = h3_mean_reversion.run(window=20, hold=5, thr=0.03)

    print("\n" + "=" * 60)
    print("ВЫВОД")
    print("=" * 60)

    signals = []
    if not h1.empty and (h1["net_edge"] > 0).any():
        n = int((h1["net_edge"] > 0).sum())
        signals.append(f"H1: найдено {n} рынков с net-арбитражем > 0 после комиссий.")
    else:
        signals.append("H1: чистого арбитража Yes+No после комиссий не найдено.")

    if not h2.empty and "won" in h2.columns and len(h2) >= 20:
        signals.append("H2: калибровочная кривая построена — см. output/h2_calibration.png "
                       "(ищи отклонение от диагонали на краях).")
    else:
        signals.append("H2: недостаточно resolved-данных для вывода.")

    if not h3.empty and "net" in h3.columns and len(h3) >= 30:
        sign = "ПОЛОЖИТЕЛЬНОЕ" if h3["net"].mean() > 0 else "отрицательное"
        signals.append(f"H3: мат.ожидание mean-reversion после издержек {sign}.")
    else:
        signals.append("H3: недостаточно гранулярной истории для вывода.")

    for s in signals:
        print(" •", s)

    print("\nНапоминание: edge на бумаге != деньги. Следующий шаг только если хотя бы "
          "одна гипотеза даёт устойчивый положительный net-edge — тогда paper-trading.")


if __name__ == "__main__":
    main()
