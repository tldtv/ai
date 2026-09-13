"""
fetch_sources.py — механический слой пайплайна.

Тянет RSS-источники, перечисленные в config/parameters.yaml, фильтрует по
дате, размечает рынок по ключевым словам и отбрасывает уже известные
ссылки (сверяясь с data/backlog.csv). К LLM не обращается — всё, что
здесь происходит, детерминировано и воспроизводимо, но именно этот файл
решает, СКОЛЬКО статей вообще дойдёт до платного анализа — см. фильтр по
дате ниже, это единственная стоимостная защита пайплайна.

Два режима, теперь строго разделённых по смыслу (и по отдельным workflow
— см. .github/workflows/weekly_signal_scan.yml и historical_backfill.yml):
  - "weekly" — обычный режим. Статья должна быть опубликована не раньше
    чем config -> weekly_recency_days дней назад, иначе отбрасывается ДО
    анализа, даже если по ссылке она ещё не встречалась в бэклоге. Это
    добавлено уже после первого инцидента с расходом (см. README,
    "Стоимость API") — раньше в этом режиме в анализ уходило вообще всё,
    что RSS-лента отдаёт на момент запуска, а это может быть много статей
    разом (например, при самом первом запуске, когда сверять ещё не с
    чем, или если запустить workflow вручную несколько раз подряд).
  - "backfill" — ретроспективный сбор строго внутри config -> target_window
    (используется только historical_backfill.yml, по расписанию не
    запускается). Статьи вне окна отбрасываются ДО анализа так же, как
    свежие статьи вне окна в режиме weekly.

Результат — data/_new_raw_signals.json: список новых, ещё не обработанных
сигналов, которые дальше передаются в analyze_signals.py.
"""
import argparse
import csv
import json
from datetime import date, datetime
from pathlib import Path

import feedparser
import yaml
from dateutil import parser as dateparser

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "parameters.yaml"
BACKLOG_PATH = ROOT / "data" / "backlog.csv"
OUT_PATH = ROOT / "data" / "_new_raw_signals.json"


def load_config():
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


def load_known_urls():
    if not BACKLOG_PATH.exists():
        return set()
    with BACKLOG_PATH.open(encoding="utf-8") as f:
        return {row["source_url"] for row in csv.DictReader(f)}


def tag_market(text, markets_cfg):
    text_low = text.lower()
    scores = {}
    for market, cfg in markets_cfg.items():
        kws = cfg.get("keywords", [])
        scores[market] = sum(1 for kw in kws if kw.lower() in text_low)
    best = max(scores, key=scores.get) if scores else None
    return best if best and scores[best] > 0 else "Не определено"


def in_window(pub_dt, start, end):
    if not pub_dt:
        return False
    try:
        d = pub_dt.date() if isinstance(pub_dt, datetime) else pub_dt
        return date.fromisoformat(start) <= d <= date.fromisoformat(end)
    except Exception:
        return False


def fetch(mode="weekly", start=None, end=None):
    cfg = load_config()
    window = cfg["target_window"]
    start = start or window["start"]
    end = end or window["end"]
    known_urls = load_known_urls()
    # По умолчанию включено — см. комментарий у места использования ниже
    # и config/parameters.yaml -> require_market_match.
    require_market_match = cfg.get("require_market_match", True)
    weekly_recency_days = int(cfg.get("weekly_recency_days", 10) or 10)
    today = date.today()

    new_items = []
    skipped_by_date = 0
    for src in cfg["sources"]:
        if src.get("type") != "rss" or not src.get("url"):
            continue  # источник без подтверждённого RSS — впишите url в config
        parsed = feedparser.parse(src["url"])
        for entry in parsed.entries:
            url = entry.get("link")
            if not url or url in known_urls:
                continue

            pub_raw = entry.get("published") or entry.get("updated")
            try:
                pub_dt = dateparser.parse(pub_raw) if pub_raw else None
            except Exception:
                pub_dt = None

            # Стоимостная защита (см. docstring выше и config ->
            # weekly_recency_days) — статья отбрасывается ДО обращения к
            # LLM, если не укладывается в разрешённое для этого режима
            # окно дат. known_urls.add() ниже до этой проверки не дошли
            # специально: если статью отбросили только по дате, а не
            # потому что видели раньше, в следующий запуск (когда она,
            # возможно, попадёт в окно, или после смены режима) её нужно
            # рассмотреть заново.
            if mode == "weekly":
                pub_date = pub_dt.date() if isinstance(pub_dt, datetime) else pub_dt
                if not pub_date or (today - pub_date).days > weekly_recency_days:
                    skipped_by_date += 1
                    continue
            elif mode == "backfill":
                if not in_window(pub_dt, start, end):
                    skipped_by_date += 1
                    continue

            title = entry.get("title", "")
            summary = entry.get("summary", "")
            market_guess = tag_market(f"{title} {summary}", cfg["markets"])
            known_urls.add(url)

            # Требуем совпадение хотя бы одного ключевого слова рынка ДО
            # обращения к LLM (см. require_market_match ниже) — общие ленты
            # вроде TechCrunch/CNews несут много статей мимо темы, и каждая
            # отправленная в analyze_signals.py статья — это платный вызов
            # Claude API. Экономит деньги ценой риска пропустить сигнал,
            # сформулированный необычными словами, мимо списка keywords в
            # config/parameters.yaml -> markets. Отключается одной строкой
            # в конфиге, если важнее не упустить ничего.
            if require_market_match and market_guess == "Не определено":
                continue

            item = {
                "source_name": src["name"],
                "group": src.get("group"),
                "url": url,
                "published": pub_dt.date().isoformat() if pub_dt else "",
                "title": title,
                "snippet": summary[:600],
                "market_guess": market_guess,
                "in_target_window": in_window(pub_dt, start, end),
            }
            new_items.append(item)

    OUT_PATH.write_text(json.dumps(new_items, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Найдено новых сигналов: {len(new_items)} -> {OUT_PATH}")
    if skipped_by_date:
        print(f"Отброшено по дате (стоимостная защита, режим {mode}): {skipped_by_date}")
    return new_items


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--mode", default="weekly")
    p.add_argument("--start", default=None)
    p.add_argument("--end", default=None)
    args = p.parse_args()
    fetch(args.mode, args.start or None, args.end or None)
