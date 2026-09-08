"""
fetch_sources.py — механический слой пайплайна.

Тянет RSS-источники, перечисленные в config/parameters.yaml, фильтрует по
целевому окну дат, размечает рынок по ключевым словам и отбрасывает уже
известные ссылки (сверяясь с data/backlog.csv). К LLM не обращается —
всё, что здесь происходит, детерминировано и воспроизводимо.

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

    new_items = []
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

            title = entry.get("title", "")
            summary = entry.get("summary", "")
            item = {
                "source_name": src["name"],
                "tier": src.get("tier"),
                "url": url,
                "published": pub_dt.date().isoformat() if pub_dt else "",
                "title": title,
                "snippet": summary[:600],
                "market_guess": tag_market(f"{title} {summary}", cfg["markets"]),
                "in_target_window": in_window(pub_dt, start, end),
            }
            new_items.append(item)
            known_urls.add(url)

    OUT_PATH.write_text(json.dumps(new_items, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Найдено новых сигналов: {len(new_items)} -> {OUT_PATH}")
    return new_items


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--mode", default="weekly")
    p.add_argument("--start", default=None)
    p.add_argument("--end", default=None)
    args = p.parse_args()
    fetch(args.mode, args.start or None, args.end or None)
