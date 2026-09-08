"""
run_pipeline.py — точка входа. Запускает механический сбор
(fetch_sources) и затем смысловой анализ (analyze_signals) одним
вызовом.

Использование:
  python src/run_pipeline.py --mode weekly
  python src/run_pipeline.py --mode backfill --start 2026-06-01 --end 2026-08-31

Важно про backfill: RSS-ленты обычно хранят только последние ~20-50
записей, а не архив за несколько месяцев назад. Для источников без
глубокого архива этот режим соберёт только то, что ещё доступно в ленте
на момент запуска. Для полноценного ретроспективного сбора за уже
прошедший период (как июнь-август 2026) практичнее один раз пройтись
вручную (например, через обычный поиск) и внести найденное прямо в
data/backlog.csv по той же схеме колонок — дальше пайплайн будет только
дополнять этот файл.
"""
import argparse

from fetch_sources import fetch
from analyze_signals import analyze


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["weekly", "backfill"], default="weekly")
    p.add_argument("--start", default=None)
    p.add_argument("--end", default=None)
    args = p.parse_args()

    fetch(mode=args.mode, start=args.start or None, end=args.end or None)
    analyze()


if __name__ == "__main__":
    main()
