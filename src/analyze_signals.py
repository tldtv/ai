"""
analyze_signals.py — смысловой слой пайплайна.

Берёт data/_new_raw_signals.json (результат fetch_sources.py) и для
каждого сигнала просит Claude отделить факт от интерпретации,
сформулировать гипотезу возможности для Авито.Работы, оценить её по
рубрике из config/parameters.yaml и указать уверенность и ключевые
неизвестные. Результат дописывается в data/backlog.csv.

Требует переменную окружения ANTHROPIC_API_KEY (в GitHub Actions —
секрет репозитория: Settings -> Secrets and variables -> Actions).
"""
import csv
import json
import os
from datetime import date
from pathlib import Path

import yaml
from anthropic import Anthropic

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "parameters.yaml"
NEW_SIGNALS_PATH = ROOT / "data" / "_new_raw_signals.json"
BACKLOG_PATH = ROOT / "data" / "backlog.csv"

FIELDNAMES = [
    "date_found", "source_url", "source_date", "in_target_window",
    "market_guess", "title", "fact", "interpretation", "cluster_id",
    "opportunity_hypothesis", "priority_score", "confidence",
    "key_unknowns", "status",
]

PROMPT_TEMPLATE = """Ты аналитик роста в Авито.Работе. Три рынка компании:
Классифайд/джоборды, Авито.Подработка (транзакционная платформенная
занятость), HR-tech (продукты HRmost и AIR). По сырому сигналу ниже
сформируй строго JSON-объект со следующими полями и ничем больше:

- "fact": только то, что буквально произошло, без оценок (1-2 предложения)
- "interpretation": твоя трактовка значения этого факта, явно как мнение,
  а не факт
- "opportunity_hypothesis": гипотеза продуктовой/бизнес-возможности для
  Авито.Работы, привязанная к одному из трёх рынков
- "priority_score": число от 1 до 5 — твоя усреднённая оценка по критериям
  {weights}
- "confidence": одно из "низкая", "средняя", "высокая"
- "key_unknowns": 2-3 главных открытых вопроса одной строкой через "; "

Верни ТОЛЬКО JSON, без пояснений вокруг и без markdown-обёртки.

Источник: {source_name}
Заголовок: {title}
Текст: {snippet}
"""


def load_config():
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


def call_llm(client, model, item, weights):
    prompt = PROMPT_TEMPLATE.format(
        weights=", ".join(weights.keys()),
        source_name=item["source_name"],
        title=item["title"],
        snippet=item["snippet"],
    )
    resp = client.messages.create(
        model=model,
        max_tokens=800,
        messages=[{"role": "user", "content": prompt}],
    )
    text = resp.content[0].text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.split("\n", 1)[1] if "\n" in text else text
        text = text.rsplit("```", 1)[0]
    return json.loads(text)


def analyze():
    if not NEW_SIGNALS_PATH.exists():
        print("Нет новых сигналов для анализа (сначала запустите fetch_sources.py)")
        return

    cfg = load_config()
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise SystemExit("Не задана переменная окружения ANTHROPIC_API_KEY")

    client = Anthropic(api_key=api_key)
    model = cfg.get("llm", {}).get("model", "claude-sonnet-4-5")

    items = json.loads(NEW_SIGNALS_PATH.read_text(encoding="utf-8"))
    if not items:
        print("Новых сигналов нет — бэклог не менялся.")
        return

    weights = cfg["priority_weights"]
    file_exists = BACKLOG_PATH.exists() and BACKLOG_PATH.stat().st_size > 0

    with BACKLOG_PATH.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if not file_exists:
            writer.writeheader()

        for item in items:
            try:
                result = call_llm(client, model, item, weights)
            except Exception as e:
                print(f"Пропуск '{item['title']}': ошибка LLM ({e})")
                continue

            writer.writerow({
                "date_found": date.today().isoformat(),
                "source_url": item["url"],
                "source_date": item["published"],
                "in_target_window": item["in_target_window"],
                "market_guess": item["market_guess"],
                "title": item["title"],
                "fact": result.get("fact", ""),
                "interpretation": result.get("interpretation", ""),
                "cluster_id": "",  # автокластеризация повторов — TODO, см. README
                "opportunity_hypothesis": result.get("opportunity_hypothesis", ""),
                "priority_score": result.get("priority_score", ""),
                "confidence": result.get("confidence", ""),
                "key_unknowns": result.get("key_unknowns", ""),
                "status": "new",
            })
            print(f"Добавлено: {item['title']}")


if __name__ == "__main__":
    analyze()
