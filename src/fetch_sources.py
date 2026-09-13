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

В конце печатается таблица по каждому источнику отдельно (в ленте всего /
уже видели / отброшено по дате / мимо ключевых слов рынка / оставлено) —
по ней видно, на каком именно шаге источник перестал давать сигналы,
включая явную пометку, если feedparser вообще не смог разобрать ленту или
она вернула ноль записей (например, сайт блокирует запросы с IP GitHub
Actions, url в config устарел и т.п.) — раньше такой источник просто молча
не давал ни одного сигнала, неотличимо от "у него правда сейчас нет ничего
по теме".
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
    # Диагностика по КАЖДОМУ источнику отдельно — без этого молчаливый сбой
    # одного источника (лента не отдаёт записей, блокирует запросы с IP
    # GitHub Actions, битый URL и т.п.) неотличим от него же просто "у него
    # сейчас нет новых статей по теме": в обоих случаях источник тихо не
    # даёт ни одного сигнала. per_source ниже печатается в конце — по этой
    # таблице сразу видно, на каком именно шаге отсеялся конкретный источник
    # (feedparser вообще ничего не вернул / всё уже видели / всё старше
    # weekly_recency_days / ни одна статья не прошла по ключевым словам
    # рынка) — так эту причину можно найти без доступа к логам самого
    # workflow.
    per_source = {}
    for src in cfg["sources"]:
        if src.get("type") != "rss" or not src.get("url"):
            continue  # источник без подтверждённого RSS — впишите url в config
        stats = per_source.setdefault(src["name"], {
            "group": src.get("group"), "fetched": 0, "bozo": None,
            "skip_known": 0, "skip_date": 0, "skip_market": 0, "kept": 0,
        })
        parsed = feedparser.parse(src["url"])
        stats["fetched"] = len(parsed.entries)
        # bozo=1 — feedparser не смог нормально распарсить ответ (не тот
        # Content-Type, битый XML, HTTP-ошибка вместо ленты и т.п.) — сам
        # факт этого стоит напечатать, даже если entries при этом не пустой
        # (иногда feedparser всё равно восстанавливает часть записей).
        if getattr(parsed, "bozo", 0):
            stats["bozo"] = repr(parsed.get("bozo_exception"))
        for entry in parsed.entries:
            url = entry.get("link")
            if not url or url in known_urls:
                stats["skip_known"] += 1
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
                    stats["skip_date"] += 1
                    continue
            elif mode == "backfill":
                if not in_window(pub_dt, start, end):
                    skipped_by_date += 1
                    stats["skip_date"] += 1
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
                stats["skip_market"] += 1
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
            stats["kept"] += 1

    OUT_PATH.write_text(json.dumps(new_items, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Найдено новых сигналов: {len(new_items)} -> {OUT_PATH}")
    if skipped_by_date:
        print(f"Отброшено по дате (стоимостная защита, режим {mode}): {skipped_by_date}")

    print("По источникам (в ленте всего / уже видели / отброшено по дате / мимо рынка / оставлено):")
    for name, s in per_source.items():
        bozo_note = f" [ОШИБКА РАЗБОРА ЛЕНТЫ: {s['bozo']}]" if s["bozo"] else ""
        empty_note = " [лента вернула 0 записей — проверьте url в config или доступность сайта]" if s["fetched"] == 0 else ""
        print(
            f"  {name} ({s['group']}): {s['fetched']} / {s['skip_known']} / "
            f"{s['skip_date']} / {s['skip_market']} / {s['kept']}{bozo_note}{empty_note}"
        )

    return new_items


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--mode", default="weekly")
    p.add_argument("--start", default=None)
    p.add_argument("--end", default=None)
    args = p.parse_args()
    fetch(args.mode, args.start or None, args.end or None)
