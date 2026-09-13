"""
analyze_signals.py — смысловой слой пайплайна.

Берёт data/_new_raw_signals.json (результат fetch_sources.py) и для
каждого сигнала просит Claude отделить факт от интерпретации,
сформулировать гипотезу возможности для Авито.Работы, оценить её по
каждому критерию рубрики из config/parameters.yaml отдельно (не одним
числом — чтобы на дашборде был виден брейкдаун) и указать уверенность
и ключевые неизвестные. Итоговый priority_score считается здесь, в
Python, как взвешенное среднее по весам из конфига — так результат
воспроизводим и не зависит от того, умеет ли модель складывать числа.

Тем же вызовом модель также относит сигнал к 1-2 этапам воронки ключевых
метрик Авито.Работы (config/parameters.yaml -> funnel_stages) — отдельное
от "рынка" измерение: рынок это ЧТО за продукт, этап воронки — на КАКУЮ
бизнес-метрику сигнал должен повлиять (рост соискателей/работодателей,
вовлечённость, конверсия в наём, монетизация).

Тем же вызовом модель также определяет, не является ли сигнал дублем
уже существующей в бэклоге истории (см. _dedup_candidates/_resolve_
cluster_id ниже) — раньше дедуп в пайплайне был только по точному
совпадению URL (fetch_sources.py), из-за чего одна и та же новость с
разных источников (например, vc.ru и TechCrunch про одно и то же
событие) заводила отдельные строки в бэклоге. Теперь такие дубли
получают общий cluster_id — build_dashboard.py схлопывает их в одну
карточку, а send_digest.py не шлёт дубли в рассылке.

Результат дописывается в data/backlog.csv (точнее — весь файл
перезаписывается целиком на каждый запуск, а не дописывается построчно,
как раньше: это нужно, чтобы при обнаружении дубля можно было задним
числом проставить cluster_id и уже существующей строке, если та ещё не
была частью кластера).

Требует переменную окружения ANTHROPIC_API_KEY (в GitHub Actions —
секрет репозитория: Settings -> Secrets and variables -> Actions).
"""
import csv
import json
import os
from datetime import date, timedelta
from pathlib import Path

import yaml
from anthropic import Anthropic

from signal_id import hash_key, card_key

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "parameters.yaml"
NEW_SIGNALS_PATH = ROOT / "data" / "_new_raw_signals.json"
BACKLOG_PATH = ROOT / "data" / "backlog.csv"

# Окно и лимит кандидатов для проверки на дубль (см. _dedup_candidates) —
# ограничены, чтобы промпт не раздувался по мере роста бэклога: повтор
# истории месячной давности маловероятен, а лишние токены на КАЖДЫЙ
# анализируемый сигнал складываются в заметную сумму.
DEDUP_LOOKBACK_DAYS = 45
DEDUP_MAX_CANDIDATES = 80

# ВАЖНО: source_tier, criteria_scores и funnel_stages дописаны в КОНЕЦ
# списка, а не вставлены между старыми полями. Если у вас уже есть
# накопленный data/backlog.csv из прошлых запусков — правьте только
# первую строку (заголовок), добавив недостающие имена в конец (например
# ",funnel_stages", если остальные уже были дописаны раньше); сами строки
# с данными трогать не нужно. Если вставить новые поля в середину списка,
# новые и старые строки в одном файле начнут читаться со сдвигом колонок
# — так делать не надо.
FIELDNAMES = [
    "date_found", "source_url", "source_date", "in_target_window",
    "market_guess", "title", "fact", "interpretation", "cluster_id",
    "opportunity_hypothesis", "priority_score", "confidence",
    "key_unknowns", "status", "source_tier", "criteria_scores",
    "funnel_stages",
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
- "criteria_scores": JSON-объект с оценкой от 1 до 5 (целое число) по
  КАЖДОМУ из следующих критериев, все ключи обязательны: {criteria_keys}
- "funnel_stages": список из 1-2 названий этапов воронки Авито.Работы (взять
  ТОЛЬКО из списка ниже, дословно, как написано) — на какую метрику бизнеса
  этот сигнал влияет сильнее всего. Это отдельное измерение от рынка: рынок
  — это ЧТО за продукт, этап воронки — на КАКУЮ метрику он должен повлиять.
  Этапы воронки:
{funnel_stages_block}
- "confidence": одно из "низкая", "средняя", "высокая"
- "key_unknowns": 2-3 главных открытых вопроса одной строкой через "; "
- "duplicate_of_index": номер сигнала из списка "Уже в бэклоге" ниже,
  если этот сигнал — явно про ТО ЖЕ САМОЕ событие/историю, что и один
  из них (даже если заголовок сформулирован иначе, короче/длиннее или
  на другом языке — например, одну и ту же новость независимо написали
  vc.ru и TechCrunch). Иначе — null. Похожая, но НЕ идентичная тема
  (например, два разных раунда инвестиций одной компании, или две
  разные компании в одной нише) — это НЕ дубль, ставь null.

Верни ТОЛЬКО JSON, без пояснений вокруг и без markdown-обёртки.

Источник: {source_name}
Заголовок: {title}
Текст: {snippet}

Уже в бэклоге (номер: заголовок) — используй для duplicate_of_index:
{candidates_block}
"""


def load_config():
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


def _dedup_candidates(all_rows):
    """Строки бэклога, которые стоит показать модели как потенциальные
    "оригиналы" для проверки на дубль — только не старше DEDUP_LOOKBACK_
    DAYS и не больше DEDUP_MAX_CANDIDATES штук (см. комментарий у
    констант). Возвращает список (индекс_в_all_rows, строка)."""
    cutoff = date.today() - timedelta(days=DEDUP_LOOKBACK_DAYS)
    picked = []
    for i, row in enumerate(all_rows):
        try:
            found = date.fromisoformat((row.get("date_found") or "").strip())
        except ValueError:
            found = None
        if found is None or found >= cutoff:
            picked.append((i, row))
    return picked[-DEDUP_MAX_CANDIDATES:]


def _format_candidates(candidates):
    if not candidates:
        return "(бэклог пуст или все сигналы в нём старше 45 дней — дублей быть не может, ставь null)"
    return "\n".join(f"{i}: {row.get('title', '')}" for i, row in candidates)


def _resolve_cluster_id(raw_index, candidates, all_rows):
    """Если модель уверенно указала индекс существующей строки как дубль —
    возвращает cluster_id для НОВОЙ строки. Если у найденной существующей
    строки ещё не было cluster_id (она ещё не была частью кластера), она
    мутируется на месте (all_rows[idx] — тот же объект, что и в общем
    списке) и получает cluster_id, равный её же fid — тем самым становясь
    "главной" в новом кластере из двух строк; все дальнейшие дубли этой
    же истории в этом и следующих запусках будут ссылаться на тот же id."""
    if raw_index is None:
        return ""
    try:
        idx = int(raw_index)
    except (TypeError, ValueError):
        return ""
    if idx not in {i for i, _ in candidates}:
        return ""  # модель указала индекс не из предложенного списка — игнорируем
    target_row = all_rows[idx]
    target_cluster_id = (target_row.get("cluster_id") or "").strip()
    if not target_cluster_id:
        target_cluster_id = hash_key(card_key(target_row))
        target_row["cluster_id"] = target_cluster_id
    return target_cluster_id


def _format_funnel_stages_block(funnel_stages_cfg):
    if not funnel_stages_cfg:
        return "  (не заданы в config/parameters.yaml -> funnel_stages — верни пустой список)"
    return "\n".join(
        f'  - "{name}" — {(info or {}).get("description", "")}'
        for name, info in funnel_stages_cfg.items()
    )


def _resolve_funnel_stages(raw_value, funnel_stages_cfg):
    """Модель должна вернуть список ровно из названий, заданных в config ->
    funnel_stages. Всё, что не совпадает дословно с известным названием,
    молча отбрасывается (опечатка модели не должна ронять весь анализ) —
    сохраняем как одну строку через "; ", как key_unknowns."""
    known = set(funnel_stages_cfg or {})
    if not raw_value:
        return ""
    if isinstance(raw_value, str):
        raw_value = [raw_value]
    if not isinstance(raw_value, list):
        return ""
    picked = [str(v).strip() for v in raw_value if str(v).strip() in known]
    return "; ".join(dict.fromkeys(picked))  # dict.fromkeys — убрать дубли, сохранив порядок


def _extract_text(resp):
    """Достаёт итоговый текстовый блок из ответа модели.

    Начиная с claude-sonnet-5 модель иногда сама, без явного запроса,
    включает "размышление" (thinking) — тогда resp.content НЕ начинается
    сразу с текста, а первым идёт ThinkingBlock (поле .thinking, а не
    .text). Старый код брал resp.content[0].text вслепую — на моделях,
    что вели себя так, ЛЮБОЙ вызов падал с AttributeError на 100% сигналов
    (это и произошло в реальном прогоне: 40 из 40 "ошибка LLM"), при этом
    сам сигнал был бы прекрасно проанализирован, если бы не эта ошибка
    разбора ответа. Поэтому ищем именно блок типа "text", а не полагаемся
    на позицию — это правильно и для сегодняшних моделей, и для будущих,
    которые могут добавлять другие служебные блоки перед текстом."""
    for block in resp.content:
        if getattr(block, "type", None) == "text":
            return block.text.strip()
    block_types = [getattr(b, "type", type(b).__name__) for b in resp.content]
    raise ValueError(f"В ответе модели нет текстового блока (получены блоки: {block_types})")


def call_llm(client, model, item, weights, candidates, funnel_stages_cfg):
    prompt = PROMPT_TEMPLATE.format(
        criteria_keys=", ".join(weights.keys()),
        source_name=item["source_name"],
        title=item["title"],
        snippet=item["snippet"],
        candidates_block=_format_candidates(candidates),
        funnel_stages_block=_format_funnel_stages_block(funnel_stages_cfg),
    )
    resp = client.messages.create(
        model=model,
        max_tokens=700,  # снижено с 800 — небольшая, безопасная экономия;
        # русскоязычный JSON-ответ по этой схеме на практике укладывается
        # заметно ниже потолка, но текстовые поля (fact/interpretation/
        # opportunity_hypothesis) свободной длины, поэтому не опускаем
        # сильно ниже — риск обрезать ответ и сломать JSON стоит дороже
        # сэкономленного
        thinking={"type": "disabled"},  # см. _extract_text — этой задаче
        # "размышление" не нужно (структурированная разметка по жёсткой
        # схеме), а неявное включение только тратит бюджет max_tokens и
        # ломало разбор ответа на claude-sonnet-5
        messages=[{"role": "user", "content": prompt}],
    )
    text = _extract_text(resp)
    if text.startswith("```"):
        text = text.strip("`")
        text = text.split("\n", 1)[1] if "\n" in text else text
        text = text.rsplit("```", 1)[0]
    return json.loads(text)


def weighted_score(criteria_scores, weights):
    """Взвешенное среднее по критериям. Критерий без оценки от модели
    считается за 0 — намеренно консервативное упрощение, чтобы
    отсутствующая оценка не завышала итог."""
    total_weight = sum(weights.values()) or 1
    total = sum(float(criteria_scores.get(k, 0)) * w for k, w in weights.items())
    return round(total / total_weight, 1)


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
    funnel_stages_cfg = cfg.get("funnel_stages", {}) or {}

    # Читаем весь текущий бэклог в память (а не дописываем построчно, как
    # раньше) — нужно, чтобы при обнаружении дубля можно было проставить
    # cluster_id и уже существующей строке, если она ещё не была частью
    # кластера (см. _resolve_cluster_id). Файл размером в сотни-тысячи
    # строк это не проблема, а бэклог как раз такого масштаба.
    all_rows = []
    if BACKLOG_PATH.exists() and BACKLOG_PATH.stat().st_size > 0:
        with BACKLOG_PATH.open(newline="", encoding="utf-8") as f:
            all_rows = list(csv.DictReader(f))

    duplicates_found = 0
    failed = 0
    last_error = None
    for item in items:
        candidates = _dedup_candidates(all_rows)
        try:
            result = call_llm(client, model, item, weights, candidates, funnel_stages_cfg)
        except Exception as e:
            failed += 1
            last_error = e
            print(f"Пропуск '{item['title']}': ошибка LLM ({type(e).__name__}: {e})")
            continue

        criteria_scores = result.get("criteria_scores", {})
        score = weighted_score(criteria_scores, weights)
        cluster_id = _resolve_cluster_id(result.get("duplicate_of_index"), candidates, all_rows)
        if cluster_id:
            duplicates_found += 1

        all_rows.append({
            "date_found": date.today().isoformat(),
            "source_url": item["url"],
            "source_date": item["published"],
            "source_tier": item.get("group", ""),  # поле называется source_tier по
            # историческим причинам, хранит название группы источника
            "in_target_window": item["in_target_window"],
            "market_guess": item["market_guess"],
            "title": item["title"],
            "fact": result.get("fact", ""),
            "interpretation": result.get("interpretation", ""),
            "cluster_id": cluster_id,
            "opportunity_hypothesis": result.get("opportunity_hypothesis", ""),
            "criteria_scores": json.dumps(criteria_scores, ensure_ascii=False),
            "priority_score": score,
            "confidence": result.get("confidence", ""),
            "key_unknowns": result.get("key_unknowns", ""),
            "status": "new",
            "funnel_stages": _resolve_funnel_stages(result.get("funnel_stages"), funnel_stages_cfg),
        })
        note = " — похоже, дубль уже известной истории" if cluster_id else ""
        print(f"Добавлено: {item['title']} (score={score}){note}")

    with BACKLOG_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(all_rows)

    if duplicates_found:
        print(f"Найдено и сгруппировано дублей: {duplicates_found}")

    succeeded = len(items) - failed
    print(f"Обработано: {succeeded} из {len(items)} успешно" + (f", ошибок: {failed}" if failed else ""))

    # КРИТИЧНО: если ошиблись АБСОЛЮТНО все сигналы — это почти наверняка
    # системная проблема (неверный/отключённый API-ключ, исчерпан лимит
    # расходов, не проходит формат ответа модели), а не просто "не повезло
    # с одной статьёй". Раньше в этом случае скрипт всё равно завершался
    # успешно (exit code 0) и просто переписывал backlog.csv без единой
    # новой строки — GitHub Actions показывал зелёную галочку "Success",
    # хотя пайплайн по факту не сделал ничего. Явно роняем прогон, чтобы
    # это стало красным крестиком и точным текстом ошибки в логе, а не
    # молчаливой отпиской через print().
    if failed and succeeded == 0:
        raise SystemExit(
            f"Все {failed} сигналов не прошли анализ — ни одного не добавлено. "
            f"Последняя ошибка ({type(last_error).__name__}): {last_error}"
        )


if __name__ == "__main__":
    analyze()
