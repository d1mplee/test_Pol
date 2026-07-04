"""Точка входа для Windows Task Scheduler: один tick, вывод в data/tick.log.

Запускается через pythonw.exe (без консольного окна), поэтому stdout/stderr
перенаправляются в лог-файл — иначе print в pythonw падает (stdout=None).
"""
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

log_path = ROOT / "data" / "tick.log"
log = open(log_path, "a", encoding="utf-8", buffering=1)
sys.stdout = sys.stderr = log

try:
    from paper import engine
    engine.tick()
except Exception:
    import traceback
    print(f"--- tick упал {datetime.now():%Y-%m-%d %H:%M} ---")
    traceback.print_exc()
