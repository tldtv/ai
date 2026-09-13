"""
backfill_funnel_stages.py — одноразовый скрипт: размечает funnel_stages
(см. config/parameters.yaml -> funnel_stages) на СУЩЕСТВУЮЩИХ "главных"
строках data/backlog.csv, у которых это поле ещё пустое — то есть на
сигналах, собранных ДО того, как funnel_stages появился в обычном
пайплайне. Обычный пайплайн (fetch_sources.py -> analyze_signals.py) эту
колонку уже проставляет всем НОВЫМ сигналам сам, этим скриптом размечать
их не нужно — он нужен только один раз, чтобы задним числом разметить то,
что уже накоплено.

Не трогает: source_url и любые другие поля существующих строк (только
дописывает funnel_stages), строки-дубли (signal_id.is_primary(row) ==
False — они не показываются на дашборде отдельными карточками, поэтому
им funnel_stages не нужен), строки, у которых funnel_stages уже заполнен
(значит, повторный запуск после первого успешного — бесплатный, платит
только за реально недоразмеченные строки).

Требует переменную окружения ANTHROPIC_API_KEY — как и analyze_signals.py.
Стоимость заметно ниже обычного анализа: промпт короче (использует уже
готовые title/opportunity_hypothesis, не пересчитывает факт/интерпретацию/
оценку заново) и ответ — только список из 1-2 названий этапов.

Запускается вручную через .github/workflows/backfill_funnel_stages.yml —
можно оставить в репозитории (безопасно перезапускать — повторный прогон
почти ничего не стоит, см. выше) или удалить после однократного
использования, он не обязателен постоянно, в отличие от
weekly_signal_scan.yml.
"""
import csv
import json
import os
from pathlib import Path

import yaml
from anthropic import Anthropic

from signal_id import is_primary
from analyze_signals import _format_funnel_stages_block, _resolve_funnel_stages, _extract_text

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "parameters.yaml"
BACKLOG_PATH = ROOT / "data" / "backlog.csv"

PROMPT_TEMPLATE = """Ты аналитик роста в Авито.Работе. Ниже — уже проанализированный сигнал:
заголовок и готовая гипотеза продуктовой/бизнес-возможности для одного из
трёх рынков компании. Определи, к какому этапу воронки ключевых метрик
Авито.Работы он относится сильнее всего.

Верни строго JSON-объект с ОДНИМ полем "funnel_stages" — список из 1-2
названий этапов, ТОЛЬКО из списка ниже, дословно как написано. Ничего
больше вокруг, без пояснений и без markdown-обёртки.

Этапы воронки:
{funnel_stages_block}

Заголовок: {title}
Гипотеза возможности: {opportunity_hypothesis}
"""


def load_config():
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


def classify(client, model, row, funnel_stages_cfg):
    prompt = PROMPT_TEMPLATE.format(
        funnel_stages_block=_format_funnel_stages_block(funnel_stages_cfg),
        title=row.get("title", ""),
        opportunity_hypothesis=row.get("opportunity_hypothesis", ""),
    )
    resp = client.messages.create(
        model=model,
        max_tokens=150,
        thinking={"type": "disabled"},  # см. analyze_signals._extract_text —
        # без этого claude-sonnet-5 может сам включить "размышление", и
        # тогда resp.content[0] окажется не текстовым блоком
        messages=[{"role": "user", "content": prompt}],
    )
    text = _extract_text(resp)
    if text.startswith("```"):
        text = text.strip("`")
        text = text.split("\n", 1)[1] if "\n" in text else text
        text = text.rsplit("```", 1)[0]
    result = json.loads(text)
    return _resolve_funnel_stages(result.get("funnel_stages"), funnel_stages_cfg)


def backfill():
    if not BACKLOG_PATH.exists() or BACKLOG_PATH.stat().st_size == 0:
        print("data/backlog.csv не найден или пуст — нечего размечать.")
        return

    cfg = load_config()
    funnel_stages_cfg = cfg.get("funnel_stages", {}) or {}
    if not funnel_stages_cfg:
        print("config/parameters.yaml -> funnel_stages пуст — нечего размечать.")
        return

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise SystemExit("Не задана переменная окружения ANTHROPIC_API_KEY")
    client = Anthropic(api_key=api_key)
    model = cfg.get("llm", {}).get("model", "claude-sonnet-4-5")

    with BACKLOG_PATH.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    if "funnel_stages" not in fieldnames:
        fieldnames = fieldnames + ["funnel_stages"]

    todo = [
        row for row in rows
        if is_primary(row) and not (row.get("funnel_stages") or "").strip()
    ]
    print(f"Строк для разметки: {len(todo)} из {len(rows)} (дубли и уже размеченные пропущены).")

    tagged = 0
    failed = 0
    last_error = None
    for row in todo:
        try:
            stages = classify(client, model, row, funnel_stages_cfg)
        except Exception as e:
            failed += 1
            last_error = e
            print(f"  Пропуск '{row.get('title', '')}': ошибка LLM ({type(e).__name__}: {e})")
            continue
        row["funnel_stages"] = stages
        if stages:
            tagged += 1
        title_preview = (row.get("title", "") or "")[:70]
        print(f"  {title_preview!r} -> {stages or '(не распознано, оставлено пустым)'}")

    with BACKLOG_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Готово: размечено {tagged} из {len(todo)} строк.")

    # См. analyze_signals.analyze() — та же защита: если ошиблись АБСОЛЮТНО
    # все строки, это системная проблема (ключ/лимит/формат ответа), а не
    # "не повезло с одной строкой" — роняем прогон явно, а не тихо пишем
    # файл без единой новой разметки под видом успеха.
    if failed and failed == len(todo):
        raise SystemExit(
            f"Все {failed} строк не прошли разметку — ни одна не размечена. "
            f"Последняя ошибка ({type(last_error).__name__}): {last_error}"
        )


if __name__ == "__main__":
    backfill()
