"""
build_dashboard.py — читает data/backlog.csv и собирает docs/index.html:
дашборд со сводной статистикой по светофору, вкладками по рынкам с
подсказкой о каждом рынке, фильтром по группе источника (с подсказкой),
фильтрами по диапазону оценки и по диапазону даты источника, сортировкой,
двумя сворачиваемыми блоками описания на карточке («Идея из новости» /
«Применимость для Авито»), разбивкой оценки по критериям с подсказками и
РУЧНЫМ редактированием каждой оценки (пересчёт итога и цвета — сразу),
комментариями к сигналу, голосованием за перевод сигнала на 2-й уровень
(Отмели / Сомнительно, но окей / Очень интересно), переключателем уровней,
админ-панелью (пороги светофора, веса критериев, кол-во голосов для
перехода на 2-й уровень, частота обновления) и кнопкой перехода к ручному
запуску обновления в GitHub Actions.

Всё, что должно быть общим для всех, кто открывает дашборд (ручные правки
оценки, комментарии, голоса, настройки админ-панели), хранится в Firestore
— см. config/parameters.yaml -> dashboard.firebase и README. Без
настроенного Firebase эти функции либо отключены с пояснением (комментарии,
админ-панель), либо (ручное редактирование оценки, голосование) работают
только локально в этом браузере.

Пороги светофора и визуальные настройки по умолчанию — из
config/parameters.yaml -> dashboard / traffic_light_thresholds / markets /
sources / priority_weights / votes_to_promote / update_cadence_days —
дальше их можно перекрыть live через админ-панель на самой странице.

docs/index.html обслуживается GitHub Pages (Settings -> Pages -> Deploy
from a branch -> main -> /docs), поэтому после каждого пуша страница по
тому же адресу обновляется сама.
"""
import csv
import html
import json
from datetime import date
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "parameters.yaml"
BACKLOG_PATH = ROOT / "data" / "backlog.csv"
OUT_PATH = ROOT / "docs" / "index.html"

DEFAULT_TITLE = "Точки роста — Авито.Работа"
DEFAULT_ACCENT = "#7C3AED"  # ориентир на фирменный фиолетовый Авито —
# точный HEX не подтверждён официальным брендбуком, при необходимости
# замените здесь или через config/parameters.yaml -> dashboard.accent_color

CRITERIA_LABELS = {
    "strategic_fit": "Соответствие рынку",
    "signal_strength": "Сила сигнала",
    "market_growth": "Размер/рост рынка",
    "asset_fit": "Fit с активами Авито",
    "feasibility": "Реализуемость MVP",
    "urgency": "Срочность/окно",
    "regulatory_risk": "Регуляторный риск",
}

# Короткие пояснения к каждому критерию — показываются тултипом при наведении
# на его название в разбивке оценки на карточке.
CRITERIA_DESCRIPTIONS = {
    "strategic_fit": "Насколько идея соответствует стратегии и одному из трёх рынков Авито.Работы (Классифайд, Подработка, HR-tech)",
    "signal_strength": "Насколько сигнал подтверждён — несколько независимых источников сильнее, чем одно упоминание",
    "market_growth": "Насколько велик и быстро растёт рынок или тренд, к которому относится сигнал",
    "asset_fit": "Насколько легко реализовать идею на существующих активах Авито — аудитории, платформе, интеграциях",
    "feasibility": "Насколько реалистично собрать MVP в разумный срок доступными ресурсами",
    "urgency": "Насколько узко окно возможности — конкуренты или тренд не будут ждать",
    "regulatory_risk": "Насколько низки регуляторные и юридические риски запуска — чем выше оценка, тем меньше риск",
}

# Пояснения к рынкам для тултипа/попапа у фильтра "Рынок" — используются,
# если в config/parameters.yaml -> markets -> <рынок> нет своего поля
# description.
DEFAULT_MARKET_DESCRIPTIONS = {
    "Классифайд": "Классифайд/джоборды — размещение вакансий как объявлений, без транзакционной модели внутри платформы.",
    "Подработка": "Авито.Подработка — транзакционная платформенная занятость: смены и гиг-задачи, сделка происходит внутри платформы.",
    "HR-tech": "HR-tech — продукты HRmost и AIR: инструменты автоматизации найма и рекрутинга для бизнеса.",
}

TEMPLATE = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  :root {{
    --accent: {accent};
    --accent-soft: {accent}1a;
    --bg: #F7F5F0;
    --surface: #fff;
    --ink: #23262B;
    --ink-soft: #767b82;
    --border: #e3ddd2;
    --green: #2f9e59;    --green-soft: #e4f5ea; --green-ink: #1f6b3d;
    --yellow: #c98a1f;   --yellow-soft: #fbeed7; --yellow-ink: #8a5a12;
    --red: #c0392b;      --red-soft: #fbe1de;    --red-ink: #96291b;
    --grey: #8a8f96;     --grey-soft: #eceded;   --grey-ink: #5c6066;
  }}
  *{{box-sizing:border-box;}}
  body{{font-family:system-ui,-apple-system,'Segoe UI',sans-serif;background:var(--bg);color:var(--ink);margin:0;padding:32px 24px 60px;}}
  h1{{font-size:22px;margin:0 0 4px;display:flex;align-items:center;gap:14px;flex-wrap:wrap;}}
  .sub{{color:var(--ink-soft);font-size:13px;margin:0 0 18px;}}

  [data-tip]{{position:relative;cursor:help;}}
  [data-tip]:hover::after, [data-tip]:focus::after{{
    content:attr(data-tip);
    position:absolute;
    left:0;
    top:100%;
    margin-top:6px;
    background:var(--ink);
    color:#fff;
    padding:7px 10px;
    border-radius:7px;
    font-size:11px;
    line-height:1.45;
    width:220px;
    max-width:70vw;
    white-space:normal;
    z-index:8;
    box-shadow:0 6px 16px rgba(0,0,0,.18);
  }}
  .crit-name{{border-bottom:1px dotted var(--ink-soft);}}

  .refresh-btn{{font:inherit;font-size:12.5px;font-weight:600;padding:7px 14px;border-radius:999px;border:1px solid var(--accent);background:var(--accent-soft);color:var(--accent);cursor:pointer;text-decoration:none;white-space:nowrap;}}
  .refresh-btn.disabled{{opacity:.55;cursor:not-allowed;border-color:var(--border);background:var(--grey-soft);color:var(--grey-ink);}}
  .refresh-wrap{{display:flex;align-items:center;gap:10px;flex-wrap:wrap;font-size:11.5px;color:var(--ink-soft);}}
  .admin-btn{{width:30px;height:30px;border-radius:50%;border:1px solid var(--border);background:var(--surface);color:var(--ink-soft);font-size:14px;cursor:pointer;line-height:1;}}
  .admin-btn:hover{{border-color:var(--accent);color:var(--accent);}}

  .level-switch{{display:flex;gap:8px;margin-bottom:16px;flex-wrap:wrap;}}
  .level-btn{{font:inherit;font-size:13px;font-weight:600;padding:9px 16px;border-radius:10px;border:1px solid var(--border);background:var(--surface);color:var(--ink-soft);cursor:pointer;}}
  .level-btn.active{{border-color:var(--accent);background:var(--accent-soft);color:var(--accent);}}

  .stats{{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:22px;}}
  .stats[hidden]{{display:none;}}
  .stat{{flex:1;min-width:110px;background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:12px 14px;cursor:pointer;text-align:left;font:inherit;border-left:4px solid var(--grey);}}
  .stat .n{{display:block;font-size:22px;font-weight:700;line-height:1.1;}}
  .stat .l{{display:block;font-size:11.5px;color:var(--ink-soft);margin-top:2px;}}
  .stat.total{{border-left-color:var(--accent);}}
  .stat.green{{border-left-color:var(--green);}}
  .stat.yellow{{border-left-color:var(--yellow);}}
  .stat.red{{border-left-color:var(--red);}}
  .stat.grey{{border-left-color:var(--grey);}}
  .stat.active{{outline:2px solid var(--accent);outline-offset:-1px;}}

  .toolbar{{display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:14px 24px;margin-bottom:18px;}}
  .filters{{display:flex;flex-wrap:wrap;gap:16px 26px;align-items:center;}}
  .filter-group{{display:flex;gap:8px;flex-wrap:wrap;align-items:center;}}
  .filter-label{{font-size:12px;color:var(--ink-soft);margin-right:2px;}}
  .tabs, .sort{{display:flex;gap:8px;flex-wrap:wrap;}}
  .tab, .sortbtn, .groupbtn{{font:inherit;font-size:13px;padding:6px 14px;border-radius:999px;border:1px solid var(--border);background:var(--surface);cursor:pointer;color:var(--ink);}}
  .tab.active, .sortbtn.active, .groupbtn.active{{background:var(--accent);color:#fff;border-color:var(--accent);}}
  .sort{{font-size:12px;color:var(--ink-soft);align-items:center;}}

  .info-popover{{position:relative;display:inline-flex;}}
  .info-btn{{width:22px;height:22px;border-radius:50%;border:1px solid var(--border);background:var(--surface);color:var(--ink-soft);font-size:12px;cursor:pointer;line-height:1;}}
  .info-pop{{position:absolute;top:28px;left:0;z-index:10;background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:12px 14px;width:260px;box-shadow:0 6px 20px rgba(0,0,0,.08);font-size:12.5px;line-height:1.5;}}
  .info-pop b{{color:var(--ink);}}
  .info-pop p{{margin:0 0 6px;}}
  .info-pop p:last-child{{margin-bottom:0;}}

  .date-range, .score-range{{display:flex;align-items:center;gap:6px;}}
  .date-range input[type=date]{{font:inherit;font-size:12.5px;padding:5px 8px;border-radius:8px;border:1px solid var(--border);background:var(--surface);color:var(--ink);}}
  .date-range span{{color:var(--ink-soft);font-size:12px;}}

  .score-range{{gap:10px;}}
  .range-slider{{position:relative;height:26px;width:130px;}}
  .range-slider input[type=range]{{position:absolute;left:0;right:0;top:50%;transform:translateY(-50%);-webkit-appearance:none;appearance:none;width:100%;height:0;background:transparent;pointer-events:none;margin:0;}}
  .range-slider input[type=range]::-webkit-slider-thumb{{-webkit-appearance:none;pointer-events:auto;width:16px;height:16px;border-radius:50%;background:var(--accent);border:2px solid #fff;box-shadow:0 0 0 1px var(--accent);cursor:pointer;margin-top:0;}}
  .range-slider input[type=range]::-moz-range-thumb{{pointer-events:auto;width:14px;height:14px;border-radius:50%;background:var(--accent);border:2px solid #fff;box-shadow:0 0 0 1px var(--accent);cursor:pointer;}}
  .range-slider input[type=range]::-webkit-slider-runnable-track{{background:transparent;}}
  .range-slider input[type=range]::-moz-range-track{{background:transparent;}}
  .range-track{{position:absolute;left:0;right:0;top:50%;height:4px;transform:translateY(-50%);background:var(--border);border-radius:2px;}}
  .range-fill{{position:absolute;top:50%;height:4px;transform:translateY(-50%);background:var(--accent);border-radius:2px;}}
  .range-values{{font-size:12px;color:var(--ink-soft);white-space:nowrap;}}

  .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:14px;}}
  .card{{background:var(--surface);border:1px solid var(--border);border-left:5px solid var(--grey);border-radius:10px;padding:14px;transition:opacity .15s ease;}}
  .card.green{{border-left-color:var(--green);}}
  .card.yellow{{border-left-color:var(--yellow);}}
  .card.red{{border-left-color:var(--red);}}
  .card.grey{{border-left-color:var(--grey);}}
  .card.hidden{{display:none;}}
  .card-top{{display:flex;justify-content:space-between;align-items:center;gap:8px;}}
  .badges{{display:flex;gap:6px;flex-wrap:wrap;}}
  .card h3{{font-size:15px;margin:8px 0 6px;text-wrap:balance;}}
  .pill{{font-size:11px;font-weight:700;padding:2px 8px;border-radius:999px;background:var(--grey-soft);color:var(--grey-ink);white-space:nowrap;}}
  .pill.green{{background:var(--green-soft);color:var(--green-ink);}}
  .pill.yellow{{background:var(--yellow-soft);color:var(--yellow-ink);}}
  .pill.red{{background:var(--red-soft);color:var(--red-ink);}}
  .pill.grey{{background:var(--grey-soft);color:var(--grey-ink);}}
  .pill.market{{background:var(--accent-soft);color:var(--accent);}}
  .pill.status{{background:var(--grey-soft);color:var(--grey-ink);}}
  .flag{{font-size:10.5px;color:var(--yellow-ink);background:var(--yellow-soft);padding:1px 6px;border-radius:999px;}}
  .misc-list{{list-style:none;margin:8px 0 0;padding:0;font-size:12px;color:#444;}}
  .misc-list li{{position:relative;padding:3px 0 3px 14px;line-height:1.5;}}
  .misc-list li::before{{content:"•";position:absolute;left:0;color:var(--accent);}}
  .misc-list a{{color:var(--accent);}}
  .empty{{color:#999;font-size:13px;grid-column:1/-1;}}

  .card-collapse{{margin-top:6px;}}
  .card-collapse summary{{font-size:12px;color:var(--accent);cursor:pointer;list-style:none;}}
  .card-collapse summary::-webkit-details-marker{{display:none;}}
  .card-collapse summary::before{{content:"▸ ";}}
  .card-collapse[open] summary::before{{content:"▾ ";}}
  .card-collapse .body{{margin:8px 0 0;font-size:12.5px;color:#444;line-height:1.5;}}

  .breakdown ul{{list-style:none;margin:8px 0 0;padding:0;font-size:12px;}}
  .breakdown li{{display:flex;justify-content:space-between;align-items:center;padding:4px 2px;border-top:1px dashed var(--border);}}
  .breakdown li:first-child{{border-top:none;}}
  .breakdown .val{{font-weight:700;color:var(--ink);display:flex;align-items:center;gap:4px;}}
  .breakdown li.overridden{{background:var(--accent-soft);border-radius:6px;}}
  .breakdown li.overridden .override-flag{{display:inline-flex;align-items:center;justify-content:center;width:14px;height:14px;border-radius:50%;background:var(--accent);color:#fff;font-size:9px;line-height:1;flex-shrink:0;}}
  .crit-input{{width:36px;font:inherit;font-size:12px;font-weight:700;text-align:center;border:1px solid var(--border);border-radius:6px;padding:2px 3px;background:var(--surface);color:var(--ink);}}
  .crit-input:focus{{outline:2px solid var(--accent);outline-offset:1px;}}
  .reset-btn{{display:block;font:inherit;font-size:11px;font-weight:600;padding:5px 10px;border-radius:7px;border:1px solid var(--border);background:var(--bg);color:var(--ink-soft);cursor:pointer;margin:8px 0 2px;}}
  .reset-btn:not(:disabled):hover{{border-color:var(--accent);color:var(--accent);}}
  .reset-btn:disabled{{opacity:.5;cursor:not-allowed;}}

  .vote-row{{margin-top:10px;display:flex;gap:6px;flex-wrap:wrap;}}
  .vote-row[hidden]{{display:none;}}
  .vote-btn{{font:inherit;font-size:10.5px;padding:5px 8px;border-radius:7px;border:1px solid var(--border);background:var(--bg);color:var(--ink-soft);cursor:pointer;}}
  .vote-btn.voted{{border-color:var(--accent);color:var(--accent);background:var(--accent-soft);font-weight:600;}}
  .vote-btn:disabled{{cursor:not-allowed;}}
  .vote-hint{{font-size:10.5px;color:var(--ink-soft);margin:4px 0 0;line-height:1.4;}}
  .vote-hint[hidden]{{display:none;}}

  .comments{{margin-top:8px;}}
  .comment-list{{display:flex;flex-direction:column;gap:6px;margin:8px 0;max-height:160px;overflow-y:auto;}}
  .comment-item{{font-size:12px;background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:6px 9px;}}
  .comment-item .t{{display:block;font-size:10px;color:var(--ink-soft);margin-top:2px;}}
  .comment-form{{display:flex;gap:6px;}}
  .comment-form[hidden]{{display:none;}}
  .comment-form input{{flex:1;font:inherit;font-size:12px;padding:6px 8px;border-radius:7px;border:1px solid var(--border);background:var(--surface);color:var(--ink);}}
  .comment-form button{{font:inherit;font-size:12px;font-weight:600;padding:6px 10px;border-radius:7px;border:none;background:var(--accent);color:#fff;cursor:pointer;}}
  .disabled-note{{font-size:11px;color:var(--ink-soft);font-style:italic;margin:6px 0 0;}}


  .admin-overlay{{position:fixed;inset:0;background:rgba(20,18,15,.4);z-index:40;display:flex;align-items:center;justify-content:center;padding:20px;}}
  .admin-overlay[hidden]{{display:none;}}
  .admin-modal{{background:var(--surface);border-radius:14px;max-width:560px;width:100%;max-height:86vh;overflow-y:auto;padding:22px 24px;box-shadow:0 20px 60px rgba(0,0,0,.28);}}
  .admin-modal h2{{font-size:16px;margin:0 0 6px;}}
  .admin-modal .note{{font-size:11.5px;color:var(--ink-soft);margin:0 0 14px;line-height:1.5;}}
  .admin-row{{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:7px 0;border-top:1px dashed var(--border);}}
  .admin-row:first-of-type{{border-top:none;}}
  .admin-row label{{font-size:12.5px;}}
  .admin-row input{{width:80px;font:inherit;font-size:12.5px;padding:5px 8px;border-radius:7px;border:1px solid var(--border);text-align:right;}}
  .admin-section-title{{font-size:11.5px;font-weight:700;color:var(--ink-soft);text-transform:uppercase;letter-spacing:.03em;margin:16px 0 2px;}}
  .admin-actions{{display:flex;justify-content:flex-end;align-items:center;gap:10px;margin-top:18px;}}
  .admin-actions button{{font:inherit;font-size:12.5px;font-weight:600;padding:8px 16px;border-radius:8px;border:1px solid var(--border);background:var(--bg);color:var(--ink);cursor:pointer;}}
  .admin-actions .save{{border-color:var(--accent);background:var(--accent);color:#fff;}}
  .admin-actions .save:disabled{{opacity:.5;cursor:not-allowed;}}
  .admin-status{{font-size:11.5px;color:var(--green-ink);}}

  .assistant-toggle{{position:fixed;right:20px;bottom:20px;z-index:20;background:var(--accent);color:#fff;border:none;border-radius:999px;padding:12px 18px;font:inherit;font-size:13px;font-weight:600;cursor:pointer;box-shadow:0 6px 18px rgba(0,0,0,.18);display:flex;align-items:center;gap:8px;}}
  .assistant-panel{{position:fixed;top:0;right:0;height:100vh;width:340px;max-width:92vw;background:var(--surface);border-left:1px solid var(--border);box-shadow:-8px 0 24px rgba(0,0,0,.08);transform:translateX(100%);transition:transform .22s ease;z-index:30;display:flex;flex-direction:column;}}
  .assistant-panel.open{{transform:translateX(0);}}
  .assistant-head{{display:flex;justify-content:space-between;align-items:center;padding:14px 16px;border-bottom:1px solid var(--border);}}
  .assistant-head h2{{font-size:14px;margin:0;}}
  .assistant-head p{{font-size:11px;color:var(--ink-soft);margin:2px 0 0;}}
  .assistant-close{{background:none;border:none;font-size:18px;color:var(--ink-soft);cursor:pointer;line-height:1;padding:4px;}}
  .assistant-chips{{display:flex;flex-wrap:wrap;gap:6px;padding:10px 16px;border-bottom:1px solid var(--border);}}
  .chip{{font-size:11px;padding:5px 10px;border-radius:999px;border:1px solid var(--border);background:var(--bg);color:var(--ink-soft);cursor:pointer;}}
  .assistant-log{{flex:1;overflow-y:auto;padding:14px 16px;display:flex;flex-direction:column;gap:10px;}}
  .msg{{font-size:13px;line-height:1.5;padding:9px 12px;border-radius:12px;max-width:86%;white-space:pre-wrap;}}
  .msg.user{{align-self:flex-end;background:var(--accent);color:#fff;border-bottom-right-radius:3px;}}
  .msg.bot{{align-self:flex-start;background:var(--bg);border:1px solid var(--border);border-bottom-left-radius:3px;}}
  .assistant-form{{display:flex;gap:8px;padding:12px 16px;border-top:1px solid var(--border);}}
  .assistant-form input{{flex:1;font:inherit;font-size:13px;padding:9px 11px;border-radius:8px;border:1px solid var(--border);background:var(--bg);color:var(--ink);}}
  .assistant-form button{{font:inherit;font-size:13px;font-weight:600;padding:9px 14px;border-radius:8px;border:none;background:var(--accent);color:#fff;cursor:pointer;}}
</style>
</head>
<body>
  <h1>{title}
    <span class="refresh-wrap">
      <a class="refresh-btn{refresh_disabled_class}" id="refreshBtn" href="{refresh_href}" target="_blank" rel="noopener">↻ Обновить</a>
      <span>{refresh_note}</span>
      <button class="admin-btn" id="adminBtn" type="button" title="Настройки дашборда" aria-label="Настройки дашборда">⚙️</button>
    </span>
  </h1>
  <p class="sub">Бэклог сигналов · </p>

  <div class="level-switch" id="levelSwitch">
    <button class="level-btn active" data-level="1" type="button">Уровень 1 · Сырой список</button>
    <button class="level-btn" data-level="2" type="button">Уровень 2 · После голосования (<span id="level2Count">0</span>)</button>
  </div>

  <div class="stats" id="stats">
    <button class="stat total active" data-color="Все">
      <span class="n">{total}</span><span class="l">Всего идей</span>
    </button>
    <button class="stat green" data-color="green">
      <span class="n" id="statGreen">{green_count}</span><span class="l">🟢 Топ идея</span>
    </button>
    <button class="stat yellow" data-color="yellow">
      <span class="n" id="statYellow">{yellow_count}</span><span class="l">🟡 Внимательно изучить</span>
    </button>
    <button class="stat red" data-color="red">
      <span class="n" id="statRed">{red_count}</span><span class="l">🔴 Посмотреть в полглаза</span>
    </button>
  </div>

  <div class="stats" id="stats2" hidden>
    <button class="stat total active" data-l2="Все">
      <span class="n" id="statL2Total">0</span><span class="l">Всего на 2-м уровне</span>
    </button>
    <button class="stat grey" data-l2="out">
      <span class="n" id="statL2Out">0</span><span class="l">⚪ Отмели</span>
    </button>
    <button class="stat yellow" data-l2="maybe">
      <span class="n" id="statL2Maybe">0</span><span class="l">🤔 Сомнительно, но окей</span>
    </button>
    <button class="stat green" data-l2="hot">
      <span class="n" id="statL2Hot">0</span><span class="l">🔥 Очень интересно</span>
    </button>
  </div>

  <div class="toolbar">
    <div class="filters">
      <div class="filter-group">
        <span class="filter-label">Рынок:</span>
        <div class="tabs" id="tabs">{tabs}</div>
        <div class="info-popover">
          <button class="info-btn" id="marketInfoBtn" type="button" aria-expanded="false" aria-label="Что означает каждый рынок">i</button>
          <div class="info-pop" id="marketPop" hidden>{market_pop}</div>
        </div>
      </div>
      <div class="filter-group">
        <span class="filter-label">Группа источника:</span>
        <div class="tabs" id="groups">{group_buttons}</div>
        <div class="info-popover">
          <button class="info-btn" id="groupInfoBtn" type="button" aria-expanded="false" aria-label="Что входит в каждую группу источников">i</button>
          <div class="info-pop" id="groupPop" hidden>{group_pop}</div>
        </div>
      </div>
      <div class="filter-group">
        <span class="filter-label">Оценка:</span>
        <div class="score-range">
          <div class="range-slider" id="scoreSlider">
            <div class="range-track"></div>
            <div class="range-fill" id="scoreFill"></div>
            <input type="range" id="scoreMinRange" min="0" max="5" step="0.1" value="0" aria-label="Минимальная оценка">
            <input type="range" id="scoreMaxRange" min="0" max="5" step="0.1" value="5" aria-label="Максимальная оценка">
          </div>
          <span class="range-values" id="scoreValues">0 – 5</span>
        </div>
      </div>
      <div class="filter-group">
        <span class="filter-label">Дата:</span>
        <div class="date-range">
          <input type="date" id="dateFrom" aria-label="С даты">
          <span>—</span>
          <input type="date" id="dateTo" aria-label="По дату">
        </div>
      </div>
    </div>
    <div class="sort" id="sort">
      Сортировка:
      <button class="sortbtn active" data-sort="score">по оценке</button>
      <button class="sortbtn" data-sort="date">по дате</button>
    </div>
  </div>

  <div class="grid" id="grid">{cards}</div>

  <button class="assistant-toggle" id="assistantToggle" type="button">💬 Помощник</button>
  <aside class="assistant-panel" id="assistantPanel" aria-hidden="true">
    <div class="assistant-head">
      <div>
        <h2>Помощник по дашборду</h2>
        <p>Отвечает по данным на странице, без внешних запросов</p>
      </div>
      <button class="assistant-close" id="assistantClose" type="button" aria-label="Закрыть">×</button>
    </div>
    <div class="assistant-chips" id="assistantChips">
      <button class="chip" type="button">Сколько зелёных идей?</button>
      <button class="chip" type="button">Топ идея по оценке</button>
      <button class="chip" type="button">Идеи с низкой уверенностью</button>
    </div>
    <div class="assistant-log" id="assistantLog"></div>
    <form class="assistant-form" id="assistantForm">
      <input type="text" id="assistantInput" placeholder="Спросите про идеи…" autocomplete="off">
      <button type="submit">→</button>
    </form>
  </aside>

  <div class="admin-overlay" id="adminOverlay" hidden>
    <div class="admin-modal">
      <h2>Настройки дашборда</h2>
      <p class="note">Открыто всем, у кого есть ссылка на дашборд — как и голосование. Изменения применяются сразу у всех, без нового запуска пайплайна, и хранятся в Firestore поверх значений по умолчанию из config/parameters.yaml.</p>
      <div class="admin-section-title">Пороги светофора (шкала 1–5)</div>
      <div class="admin-row"><label>«Топ идея» (зелёный), от</label><input type="number" id="cfgGreenMin" step="0.1" min="0" max="5"></div>
      <div class="admin-row"><label>«Внимательно изучить» (жёлтый), от</label><input type="number" id="cfgYellowMin" step="0.1" min="0" max="5"></div>
      <div class="admin-section-title">Голосование и обновления</div>
      <div class="admin-row"><label>Голосов для перехода на 2-й уровень (свой счётчик у каждого варианта)</label><input type="number" id="cfgVotesToPromote" step="1" min="1" max="50"></div>
      <div class="admin-row"><label>Частота обновления дашборда, дней</label><input type="number" id="cfgCadence" step="1" min="1" max="90"></div>
      <p class="note">Частота обновления — справочное значение для команды. Реальное расписание запусков задаётся в .github/workflows/weekly_signal_scan.yml — изменение этого поля само по себе cron не меняет.</p>
      <div class="admin-section-title">Веса критериев в итоговой оценке</div>
      <div id="cfgWeights"></div>
      <div class="admin-actions">
        <span class="admin-status" id="adminStatus"></span>
        <button type="button" id="adminCancel">Закрыть</button>
        <button type="button" class="save" id="adminSave">Сохранить</button>
      </div>
    </div>
  </div>

<script>
  window.SIGNALS = {signals_json};
  window.CRITERIA_LABELS = {criteria_labels_json};
  window.DEFAULT_WEIGHTS = {weights_json};
  window.DEFAULT_THRESHOLDS = {thresholds_json};
  window.DEFAULT_VOTES_TO_PROMOTE = {votes_to_promote};
  window.DEFAULT_CADENCE_DAYS = {cadence_days};
</script>
{firebase_scripts}
<script>
  const state = {{ market: 'Все', color: 'Все', group: 'Все', sort: 'score', scoreMin: 0, scoreMax: 5, dateFrom: '', dateTo: '', level: 1, l2: 'Все' }};
  const grid = document.getElementById('grid');
  const cards = () => Array.from(grid.querySelectorAll('.card'));
  const L2_COLOR = {{ out: 'grey', maybe: 'yellow', hot: 'green' }};

  // Если в config/parameters.yaml -> dashboard.firebase заполнены ключи —
  // «Отмели», ручные правки оценки, комментарии, голосование и админ-панель
  // общие для всех, кто открывает дашборд (Firestore, обновление почти
  // мгновенное). Если нет — «Отмели» и ручные правки оценки работают
  // только локально в этом браузере, а голосование/комментарии/админ-панель
  // отключены с пояснением (это не то, что имеет смысл хранить только у
  // себя — весь смысл в том, чтобы видели все).
  const FIREBASE_CONFIG = {firebase_config_json};
  const FIREBASE_ENABLED = !!(FIREBASE_CONFIG && FIREBASE_CONFIG.apiKey);
  let db = null;
  if (FIREBASE_ENABLED) {{
    try {{
      firebase.initializeApp(FIREBASE_CONFIG);
      db = firebase.firestore();
    }} catch (e) {{
      console.error('Firebase не инициализировался — общие функции будут отключены/локальны:', e);
      db = null;
    }}
  }}

  // Стабильный короткий id карточки для Firestore (id документа не может
  // содержать "/", а в data-key есть ссылка на источник) — простой
  // детерминированный хэш, не криптографический, но коллизии между
  // разными сигналами в масштабе одного бэклога практически исключены.
  function hashKey(str) {{
    let h1 = 0xdeadbeef, h2 = 0x41c6ce57;
    for (let i = 0; i < str.length; i++) {{
      const ch = str.charCodeAt(i);
      h1 = Math.imul(h1 ^ ch, 2654435761);
      h2 = Math.imul(h2 ^ ch, 1597334677);
    }}
    h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909);
    h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^ Math.imul(h1 ^ (h1 >>> 13), 3266489909);
    return (h1 >>> 0).toString(16).padStart(8, '0') + (h2 >>> 0).toString(16).padStart(8, '0');
  }}
  cards().forEach(c => {{ c.dataset.fid = hashKey(c.dataset.key); }});

  const VOTE_KEY = 'avitoVoteChoices';
  let localVoteChoice = {{}};
  try {{ localVoteChoice = JSON.parse(localStorage.getItem(VOTE_KEY) || '{{}}'); }} catch (e) {{}}
  function persistVoteChoice() {{ try {{ localStorage.setItem(VOTE_KEY, JSON.stringify(localVoteChoice)); }} catch (e) {{}} }}

  function setCardColor(card, color) {{
    card.classList.remove('green', 'yellow', 'red', 'grey');
    card.classList.add(color);
    card.dataset.color = color;
    const scorePill = card.querySelector('.card-top .pill:first-child');
    if (scorePill) {{
      scorePill.classList.remove('green', 'yellow', 'red', 'grey');
      scorePill.classList.add(color);
    }}
  }}

  function recomputeStats() {{
    const counts = {{ green: 0, yellow: 0, red: 0 }};
    cards().forEach(c => {{ if (!c.dataset.level2) counts[c.dataset.color] = (counts[c.dataset.color] || 0) + 1; }});
    document.getElementById('statGreen').textContent = counts.green;
    document.getElementById('statYellow').textContent = counts.yellow;
    document.getElementById('statRed').textContent = counts.red;
  }}

  function applyLevel2Stats() {{
    const counts = {{ out: 0, maybe: 0, hot: 0 }};
    let total = 0;
    cards().forEach(c => {{ if (c.dataset.level2) {{ counts[c.dataset.level2] = (counts[c.dataset.level2] || 0) + 1; total++; }} }});
    document.getElementById('statL2Total').textContent = total;
    document.getElementById('statL2Out').textContent = counts.out;
    document.getElementById('statL2Maybe').textContent = counts.maybe;
    document.getElementById('statL2Hot').textContent = counts.hot;
    document.getElementById('level2Count').textContent = total;
  }}

  // ---- Живой пересчёт: веса/пороги (админ-панель) + ручные правки оценки
  // (signal_overrides) + статус 2-го уровня (signal_votes) все вместе
  // определяют итоговый показанный цвет/оценку карточки. Пересчитывается
  // при любом изменении любого из трёх источников.
  let liveWeights = {{...window.DEFAULT_WEIGHTS}};
  let liveThresholds = {{...window.DEFAULT_THRESHOLDS}};
  let liveVotesToPromote = window.DEFAULT_VOTES_TO_PROMOTE;
  let liveCadenceDays = window.DEFAULT_CADENCE_DAYS;
  // Локальные версии ручных оценок и голосов — используются, когда
  // Firebase не настроен: работает без бэкенда, просто не видно другим.
  const OVERRIDE_KEY = 'avitoLocalOverrides';
  const LOCAL_VOTES_KEY = 'avitoLocalVotes';
  let overridesMap = {{}};
  let votesMap = {{}};
  try {{ overridesMap = JSON.parse(localStorage.getItem(OVERRIDE_KEY) || '{{}}'); }} catch (e) {{}}
  try {{ votesMap = JSON.parse(localStorage.getItem(LOCAL_VOTES_KEY) || '{{}}'); }} catch (e) {{}}
  function persistOverridesLocal() {{ try {{ localStorage.setItem(OVERRIDE_KEY, JSON.stringify(overridesMap)); }} catch (e) {{}} }}
  function persistVotesLocal() {{ try {{ localStorage.setItem(LOCAL_VOTES_KEY, JSON.stringify(votesMap)); }} catch (e) {{}} }}

  function computeScore(criteria, weights) {{
    const keys = Object.keys(weights);
    const totalW = keys.reduce((s, k) => s + (parseFloat(weights[k]) || 0), 0) || 1;
    const total = keys.reduce((s, k) => s + (parseFloat(criteria[k]) || 0) * (parseFloat(weights[k]) || 0), 0);
    return Math.round((total / totalW) * 10) / 10;
  }}
  function colorFor(score, thresholds) {{
    if (isNaN(score)) return 'grey';
    if (score >= thresholds.green_min) return 'green';
    if (score >= thresholds.yellow_min) return 'yellow';
    return 'red';
  }}

  // Статус 2-го уровня считается заново при каждом изменении голосов —
  // не «залипает» навсегда после первого достижения порога. Если
  // распределение голосов меняется (кто-то переголосовал), сигнал может
  // вернуться на 1-й уровень (ни один вариант больше не набирает порог)
  // либо переехать в другую группу 2-го уровня. При равенстве голосов
  // у нескольких вариантов приоритет: Очень интересно > Сомнительно, но
  // окей > Отмели.
  function computeLevel2Status(counts, votesToPromote) {{
    const order = ['hot', 'maybe', 'out'];
    let maxCount = 0;
    order.forEach(k => {{ const c = counts[k] || 0; if (c > maxCount) maxCount = c; }});
    if (maxCount < votesToPromote) return '';
    return order.find(k => (counts[k] || 0) === maxCount);
  }}

  function updateVoteUI(card, vote) {{
    const row = card.querySelector('[data-vote-row]');
    if (!row) return;
    const counts = vote || {{ out: 0, maybe: 0, hot: 0 }};
    ['out', 'maybe', 'hot'].forEach(k => {{
      const span = row.querySelector(`[data-vote-count="${{k}}"]`);
      if (span) span.textContent = counts[k] || 0;
    }});
    const fid = card.dataset.fid;
    const myChoice = localVoteChoice[fid];
    const hint = card.querySelector('[data-vote-hint]');
    if (hint) hint.hidden = !myChoice;
    row.querySelectorAll('.vote-btn').forEach(btn => {{
      const isMine = myChoice === btn.dataset.vote;
      btn.classList.toggle('voted', isMine);
      btn.disabled = isMine;
    }});
  }}

  function recomputeAll() {{
    cards().forEach(card => {{
      const idx = parseInt(card.dataset.idx, 10);
      const signal = window.SIGNALS[idx] || {{}};
      const criteriaBase = signal.criteria_scores || {{}};
      const overrides = overridesMap[card.dataset.fid] || {{}};
      const merged = {{...criteriaBase, ...overrides}};

      card.querySelectorAll('.crit-input').forEach(inp => {{
        const k = inp.dataset.criterion;
        const hasOverride = Object.prototype.hasOwnProperty.call(overrides, k);
        const v = hasOverride ? overrides[k] : (criteriaBase[k] !== undefined ? criteriaBase[k] : inp.dataset.orig);
        if (document.activeElement !== inp) inp.value = v;
        const row = inp.closest('.crit-row');
        if (row) {{
          row.classList.toggle('overridden', hasOverride);
          const flag = row.querySelector('.override-flag');
          if (flag) flag.hidden = !hasOverride;
        }}
      }});
      const resetBtn = card.querySelector('[data-reset-scores]');
      if (resetBtn) resetBtn.disabled = Object.keys(overrides).length === 0;

      let baseColor = card.dataset.bakedColor;
      if (Object.keys(criteriaBase).length) {{
        const score = computeScore(merged, liveWeights);
        baseColor = colorFor(score, liveThresholds);
        card.dataset.score = score;
        const scorePill = card.querySelector('.card-top .pill:first-child');
        if (scorePill) scorePill.textContent = score.toFixed(1);
      }}

      const vote = votesMap[card.dataset.fid];
      const counts = vote || {{ out: 0, maybe: 0, hot: 0 }};
      const l2 = computeLevel2Status(counts, liveVotesToPromote);
      card.dataset.level2 = l2;
      const displayColor = l2 ? L2_COLOR[l2] : baseColor;
      setCardColor(card, displayColor);
      updateVoteUI(card, vote);
    }});
    recomputeStats();
    applyLevel2Stats();
    apply();
  }}

  grid.addEventListener('click', e => {{
    const voteBtn = e.target.closest('.vote-btn');
    if (voteBtn && !voteBtn.disabled) {{
      const card = voteBtn.closest('.card');
      castVote(card, voteBtn.dataset.vote);
      return;
    }}
    const resetBtn = e.target.closest('[data-reset-scores]');
    if (resetBtn && !resetBtn.disabled) {{
      const card = resetBtn.closest('.card');
      resetOverrides(card);
    }}
  }});

  // Сбрасывает все ручные правки оценки сигнала обратно к тому, что
  // выставила система (baked-значения из window.SIGNALS) — пометки
  // «изменено вручную» у всех критериев этого сигнала пропадают.
  function resetOverrides(card) {{
    const fid = card.dataset.fid;
    if (!db) {{
      delete overridesMap[fid];
      persistOverridesLocal();
      recomputeAll();
      return;
    }}
    db.collection('signal_overrides').doc(fid).set({{ overrides: {{}} }})
      .catch(err => console.error('Не удалось сбросить ручные оценки:', err));
  }}

  // Голос можно менять сколько угодно раз: клик по другому варианту снимает
  // старый голос и добавляет новый — счётчики, а значит и группа 2-го
  // уровня (см. computeLevel2Status выше), пересчитываются сразу у всех.
  function castVote(card, option) {{
    const fid = card.dataset.fid;
    const prevChoice = localVoteChoice[fid];
    if (prevChoice === option) return; // уже выбран этот вариант
    if (!db) {{
      const data = votesMap[fid] || {{ out: 0, maybe: 0, hot: 0 }};
      if (prevChoice) data[prevChoice] = Math.max(0, (data[prevChoice] || 0) - 1);
      data[option] = (data[option] || 0) + 1;
      votesMap[fid] = data;
      persistVotesLocal();
      localVoteChoice[fid] = option;
      persistVoteChoice();
      recomputeAll();
      return;
    }}
    const ref = db.collection('signal_votes').doc(fid);
    db.runTransaction(tx => tx.get(ref).then(doc => {{
      const data = doc.exists ? doc.data() : {{ out: 0, maybe: 0, hot: 0 }};
      if (prevChoice) data[prevChoice] = Math.max(0, (data[prevChoice] || 0) - 1);
      data[option] = (data[option] || 0) + 1;
      // Только out/maybe/hot — ровно то, что разрешено правилами
      // безопасности Firestore для signal_votes (см. firestore_rules_updated.txt
      // в корне репозитория). level2_status/promoted_at там больше не нужны
      // — статус считается на лету на клиенте (computeLevel2Status).
      tx.set(ref, {{
        out: data.out || 0,
        maybe: data.maybe || 0,
        hot: data.hot || 0,
      }}, {{ merge: true }});
    }})).then(() => {{
      localVoteChoice[fid] = option;
      persistVoteChoice();
      updateVoteUI(card, votesMap[fid]);
    }}).catch(err => console.error('Не удалось проголосовать:', err));
  }}

  grid.addEventListener('change', e => {{
    const inp = e.target.closest('.crit-input');
    if (!inp) return;
    const card = inp.closest('.card');
    const k = inp.dataset.criterion;
    let v = parseFloat(inp.value);
    if (isNaN(v)) v = parseFloat(inp.dataset.orig);
    v = Math.min(5, Math.max(1, Math.round(v)));
    inp.value = v;
    if (!db) {{
      overridesMap[card.dataset.fid] = overridesMap[card.dataset.fid] || {{}};
      overridesMap[card.dataset.fid][k] = v;
      persistOverridesLocal();
      recomputeAll();
      return;
    }}
    // Настоящий вложенный объект, а не строка с точкой в ключе:
    // set(..., {{merge:true}}) сам рекурсивно сливает вложенные map-поля
    // Firestore, обновляя только overrides.<k> и не трогая остальные
    // критерии в overrides — то, ради чего изначально была нужна точечная
    // запись, но по-настоящему рабочим способом (строковый ключ с точкой
    // вида 'overrides.strategic_fit' в set() трактуется как ОДНО поле с
    // точкой в имени, а не как путь — так работает только в update()).
    db.collection('signal_overrides').doc(card.dataset.fid).set({{
      overrides: {{ [k]: v }},
      updated_at: firebase.firestore.FieldValue.serverTimestamp(),
    }}, {{ merge: true }})
      .catch(err => console.error('Не удалось сохранить ручную оценку:', err));
  }});

  function apply() {{
    let visible = 0;
    cards().forEach(c => {{
      const isPromoted = !!c.dataset.level2;
      const levelMatch = state.level === 1 ? !isPromoted : isPromoted;
      const l2Match = state.level !== 2 || state.l2 === 'Все' || c.dataset.level2 === state.l2;
      const matchMarket = state.market === 'Все' || c.dataset.market === state.market;
      const matchColor = state.level !== 1 || state.color === 'Все' || c.dataset.color === state.color;
      const matchGroup = state.group === 'Все' || c.dataset.group === state.group;
      const score = parseFloat(c.dataset.score);
      const matchScore = isNaN(score) || (score >= state.scoreMin && score <= state.scoreMax);
      const d = c.dataset.date || '';
      const matchDate = (!state.dateFrom || !d || d >= state.dateFrom) && (!state.dateTo || !d || d <= state.dateTo);
      const show = levelMatch && l2Match && matchMarket && matchColor && matchGroup && matchScore && matchDate;
      c.classList.toggle('hidden', !show);
      if (show) visible++;
    }});
    let empty = grid.querySelector('.empty');
    if (visible === 0 && !empty) {{
      empty = document.createElement('p');
      empty.className = 'empty';
      empty.textContent = 'Нет сигналов под текущий фильтр';
      grid.appendChild(empty);
    }} else if (visible > 0 && empty) {{
      empty.remove();
    }}
  }}

  function sortCards() {{
    const sorted = cards().sort((a, b) => {{
      if (state.sort === 'score') return (parseFloat(b.dataset.score) || -1) - (parseFloat(a.dataset.score) || -1);
      return (b.dataset.date || '').localeCompare(a.dataset.date || '');
    }});
    sorted.forEach(c => grid.appendChild(c));
  }}

  document.getElementById('levelSwitch').addEventListener('click', e => {{
    const btn = e.target.closest('.level-btn');
    if (!btn) return;
    state.level = parseInt(btn.dataset.level, 10);
    document.querySelectorAll('.level-btn').forEach(b => b.classList.toggle('active', b === btn));
    document.getElementById('stats').hidden = state.level !== 1;
    document.getElementById('stats2').hidden = state.level !== 2;
    apply();
  }});

  document.getElementById('stats2').addEventListener('click', e => {{
    const btn = e.target.closest('.stat');
    if (!btn) return;
    state.l2 = btn.dataset.l2;
    document.querySelectorAll('#stats2 .stat').forEach(s => s.classList.toggle('active', s === btn));
    apply();
  }});

  document.getElementById('tabs').addEventListener('click', e => {{
    if (!e.target.classList.contains('tab')) return;
    state.market = e.target.dataset.market;
    document.querySelectorAll('#tabs .tab').forEach(t => t.classList.toggle('active', t === e.target));
    apply();
  }});

  document.getElementById('groups').addEventListener('click', e => {{
    if (!e.target.classList.contains('groupbtn')) return;
    state.group = e.target.dataset.group;
    document.querySelectorAll('#groups .groupbtn').forEach(t => t.classList.toggle('active', t === e.target));
    apply();
  }});

  document.getElementById('stats').addEventListener('click', e => {{
    const btn = e.target.closest('.stat');
    if (!btn) return;
    state.color = btn.dataset.color;
    document.querySelectorAll('#stats .stat').forEach(s => s.classList.toggle('active', s === btn));
    apply();
  }});

  document.getElementById('sort').addEventListener('click', e => {{
    if (!e.target.classList.contains('sortbtn')) return;
    state.sort = e.target.dataset.sort;
    document.querySelectorAll('.sortbtn').forEach(b => b.classList.toggle('active', b === e.target));
    sortCards();
  }});

  function togglePop(btn, pop) {{
    btn.addEventListener('click', e => {{
      e.stopPropagation();
      const open = pop.hasAttribute('hidden');
      if (open) pop.removeAttribute('hidden'); else pop.setAttribute('hidden', '');
      btn.setAttribute('aria-expanded', String(open));
    }});
  }}
  const groupInfoBtn = document.getElementById('groupInfoBtn');
  const groupPop = document.getElementById('groupPop');
  const marketInfoBtn = document.getElementById('marketInfoBtn');
  const marketPop = document.getElementById('marketPop');
  togglePop(groupInfoBtn, groupPop);
  togglePop(marketInfoBtn, marketPop);
  document.addEventListener('click', e => {{
    if (!groupPop.contains(e.target) && e.target !== groupInfoBtn) groupPop.setAttribute('hidden', '');
    if (!marketPop.contains(e.target) && e.target !== marketInfoBtn) marketPop.setAttribute('hidden', '');
  }});

  // ---- Диапазон оценки: два перекрывающихся range-инпута ----
  const scoreMinRange = document.getElementById('scoreMinRange');
  const scoreMaxRange = document.getElementById('scoreMaxRange');
  const scoreFill = document.getElementById('scoreFill');
  const scoreValues = document.getElementById('scoreValues');
  function updateScoreUI() {{
    let lo = parseFloat(scoreMinRange.value);
    let hi = parseFloat(scoreMaxRange.value);
    if (lo > hi) {{ [lo, hi] = [hi, lo]; }}
    state.scoreMin = lo;
    state.scoreMax = hi;
    const pct = v => (v / 5) * 100;
    scoreFill.style.left = pct(lo) + '%';
    scoreFill.style.width = (pct(hi) - pct(lo)) + '%';
    scoreValues.textContent = lo.toFixed(1) + ' – ' + hi.toFixed(1);
  }}
  scoreMinRange.addEventListener('input', () => {{
    if (parseFloat(scoreMinRange.value) > parseFloat(scoreMaxRange.value)) scoreMinRange.value = scoreMaxRange.value;
    updateScoreUI(); apply();
  }});
  scoreMaxRange.addEventListener('input', () => {{
    if (parseFloat(scoreMaxRange.value) < parseFloat(scoreMinRange.value)) scoreMaxRange.value = scoreMinRange.value;
    updateScoreUI(); apply();
  }});
  updateScoreUI();

  // ---- Диапазон даты источника ----
  const dateFrom = document.getElementById('dateFrom');
  const dateTo = document.getElementById('dateTo');
  dateFrom.addEventListener('change', () => {{ state.dateFrom = dateFrom.value; apply(); }});
  dateTo.addEventListener('change', () => {{ state.dateTo = dateTo.value; apply(); }});

  const refreshBtn = document.getElementById('refreshBtn');
  if (refreshBtn.classList.contains('disabled')) {{
    refreshBtn.addEventListener('click', e => e.preventDefault());
  }}

  // ---- Комментарии: подписка на подколлекцию только когда блок открыт
  // (чтобы не открывать десятки слушателей Firestore сразу при загрузке) ----
  const commentUnsubs = {{}};
  document.querySelectorAll('.comments').forEach(det => {{
    const card = det.closest('.card');
    const fid = card.dataset.fid;
    const list = det.querySelector('[data-comment-list]');
    const form = det.querySelector('[data-comment-form]');
    const inputEl = det.querySelector('[data-comment-input]');
    const disabledNote = det.querySelector('[data-comment-disabled]');
    if (!db) {{
      form.hidden = true;
      disabledNote.hidden = false;
      return;
    }}
    det.addEventListener('toggle', () => {{
      if (det.open && !commentUnsubs[fid]) {{
        commentUnsubs[fid] = db.collection('signal_comments').doc(fid).collection('items')
          .orderBy('created_at', 'asc')
          .onSnapshot(snap => {{
            list.innerHTML = '';
            if (snap.empty) {{
              list.innerHTML = '<p class="disabled-note">Комментариев пока нет.</p>';
            }}
            snap.forEach(doc => {{
              const d = doc.data();
              const when = (d.created_at && d.created_at.toDate) ? d.created_at.toDate().toLocaleString('ru-RU') : '';
              const item = document.createElement('div');
              item.className = 'comment-item';
              item.textContent = d.text || '';
              if (when) {{
                const t = document.createElement('span');
                t.className = 't';
                t.textContent = when;
                item.appendChild(t);
              }}
              list.appendChild(item);
            }});
          }}, err => console.error('Не удалось загрузить комментарии:', err));
      }}
    }});
    form.addEventListener('submit', e => {{
      e.preventDefault();
      const text = inputEl.value.trim();
      if (!text) return;
      db.collection('signal_comments').doc(fid).collection('items').add({{
        text, created_at: firebase.firestore.FieldValue.serverTimestamp(),
      }}).then(() => {{ inputEl.value = ''; }}).catch(err => console.error('Не удалось сохранить комментарий:', err));
    }});
  }});

  // ---- Админ-панель ----
  const adminBtn = document.getElementById('adminBtn');
  const adminOverlay = document.getElementById('adminOverlay');
  const adminCancel = document.getElementById('adminCancel');
  const adminSave = document.getElementById('adminSave');
  const adminStatus = document.getElementById('adminStatus');
  const cfgWeightsWrap = document.getElementById('cfgWeights');

  function fillAdminForm() {{
    document.getElementById('cfgGreenMin').value = liveThresholds.green_min;
    document.getElementById('cfgYellowMin').value = liveThresholds.yellow_min;
    document.getElementById('cfgVotesToPromote').value = liveVotesToPromote;
    document.getElementById('cfgCadence').value = liveCadenceDays;
    cfgWeightsWrap.innerHTML = Object.keys(window.DEFAULT_WEIGHTS).map(k => `
      <div class="admin-row">
        <label>${{window.CRITERIA_LABELS[k] || k}}</label>
        <input type="number" step="0.1" min="0" max="5" data-weight="${{k}}" value="${{liveWeights[k] !== undefined ? liveWeights[k] : window.DEFAULT_WEIGHTS[k]}}">
      </div>`).join('');
  }}

  adminBtn.addEventListener('click', () => {{ fillAdminForm(); adminStatus.textContent = ''; adminOverlay.hidden = false; }});
  adminCancel.addEventListener('click', () => {{ adminOverlay.hidden = true; }});
  adminOverlay.addEventListener('click', e => {{ if (e.target === adminOverlay) adminOverlay.hidden = true; }});

  if (!db) {{
    adminSave.disabled = true;
    adminSave.title = 'Нужен настроенный Firebase, см. README';
  }}

  adminSave.addEventListener('click', () => {{
    if (!db) return;
    const weights = {{}};
    cfgWeightsWrap.querySelectorAll('[data-weight]').forEach(inp => {{ weights[inp.dataset.weight] = parseFloat(inp.value) || 0; }});
    const payload = {{
      green_min: parseFloat(document.getElementById('cfgGreenMin').value),
      yellow_min: parseFloat(document.getElementById('cfgYellowMin').value),
      votes_to_promote: parseInt(document.getElementById('cfgVotesToPromote').value, 10) || window.DEFAULT_VOTES_TO_PROMOTE,
      update_cadence_days: parseInt(document.getElementById('cfgCadence').value, 10) || window.DEFAULT_CADENCE_DAYS,
      weights,
      updated_at: firebase.firestore.FieldValue.serverTimestamp(),
    }};
    if (isNaN(payload.green_min)) payload.green_min = window.DEFAULT_THRESHOLDS.green_min;
    if (isNaN(payload.yellow_min)) payload.yellow_min = window.DEFAULT_THRESHOLDS.yellow_min;
    db.collection('admin_config').doc('config').set(payload, {{ merge: true }})
      .then(() => {{
        adminStatus.textContent = 'Сохранено — применяется у всех.';
        setTimeout(() => {{ adminOverlay.hidden = true; }}, 900);
      }})
      .catch(err => {{ adminStatus.textContent = 'Ошибка сохранения.'; console.error(err); }});
  }});

  // ---- Загрузка и подписки ----
  sortCards();
  if (db) {{
    db.collection('signal_overrides').onSnapshot(snap => {{
      const map = {{}};
      snap.forEach(doc => {{ map[doc.id] = doc.data().overrides || {{}}; }});
      overridesMap = map;
      recomputeAll();
    }}, err => console.error('Firestore (ручные оценки) недоступен:', err));

    db.collection('signal_votes').onSnapshot(snap => {{
      const map = {{}};
      snap.forEach(doc => {{ map[doc.id] = doc.data(); }});
      votesMap = map;
      recomputeAll();
    }}, err => console.error('Firestore (голосование) недоступен:', err));

    db.collection('admin_config').doc('config').onSnapshot(doc => {{
      const d = doc.exists ? doc.data() : {{}};
      liveThresholds = {{
        green_min: typeof d.green_min === 'number' ? d.green_min : window.DEFAULT_THRESHOLDS.green_min,
        yellow_min: typeof d.yellow_min === 'number' ? d.yellow_min : window.DEFAULT_THRESHOLDS.yellow_min,
      }};
      liveWeights = (d.weights && Object.keys(d.weights).length) ? d.weights : {{...window.DEFAULT_WEIGHTS}};
      liveVotesToPromote = typeof d.votes_to_promote === 'number' ? d.votes_to_promote : window.DEFAULT_VOTES_TO_PROMOTE;
      liveCadenceDays = typeof d.update_cadence_days === 'number' ? d.update_cadence_days : window.DEFAULT_CADENCE_DAYS;
      recomputeAll();
    }}, err => console.error('Firestore (настройки) недоступен:', err));
  }}
  recomputeAll();

  // ---- Помощник по дашборду: локальная логика, без внешних вызовов ----
  const panel = document.getElementById('assistantPanel');
  const toggle = document.getElementById('assistantToggle');
  const closeBtn = document.getElementById('assistantClose');
  const log = document.getElementById('assistantLog');
  const form = document.getElementById('assistantForm');
  const input = document.getElementById('assistantInput');
  const chips = document.getElementById('assistantChips');

  function openPanel() {{ panel.classList.add('open'); panel.setAttribute('aria-hidden', 'false'); input.focus(); }}
  function closePanel() {{ panel.classList.remove('open'); panel.setAttribute('aria-hidden', 'true'); }}
  toggle.addEventListener('click', openPanel);
  closeBtn.addEventListener('click', closePanel);

  function addMsg(text, who) {{
    const div = document.createElement('div');
    div.className = 'msg ' + who;
    div.textContent = text;
    log.appendChild(div);
    log.scrollTop = log.scrollHeight;
  }}

  const STOPWORDS = new Set(['как','что','это','для','или','идея','идеи','идею','про','по','на','с','в','у','и','а','из']);

  function findByFragment(q) {{
    const words = q.toLowerCase().split(/[^a-zа-яё0-9]+/i).filter(w => w.length > 3 && !STOPWORDS.has(w));
    let best = null, bestScore = 0;
    window.SIGNALS.forEach(r => {{
      const t = (r.title + ' ' + r.opportunity_hypothesis).toLowerCase();
      const score = words.reduce((acc, w) => acc + (t.includes(w) ? 1 : 0), 0);
      if (score > bestScore) {{ bestScore = score; best = r; }}
    }});
    return bestScore > 0 ? best : null;
  }}

  function fmtIdea(r) {{
    return `«${{r.title}}» — ${{r.priority_score ?? '—'}}/5, рынок: ${{r.market_guess}}, уверенность: ${{r.confidence || '—'}}`;
  }}

  function answer(raw) {{
    const q = raw.toLowerCase().trim();
    const rows = window.SIGNALS;
    if (!rows.length) return 'В бэклоге пока нет идей — нечего анализировать.';

    if (/сколько.*(зел[её]н)/.test(q)) {{
      const n = rows.filter(r => r.color === 'green').length;
      return `Зелёных идей (топ): ${{n}} из ${{rows.length}}.`;
    }}
    if (/сколько.*(жёлт|желт)/.test(q)) {{
      const n = rows.filter(r => r.color === 'yellow').length;
      return `Жёлтых идей (внимательно изучить): ${{n}} из ${{rows.length}}.`;
    }}
    if (/сколько.*красн/.test(q)) {{
      const n = rows.filter(r => r.color === 'red').length;
      return `Красных идей (посмотреть в полглаза): ${{n}} из ${{rows.length}}.`;
    }}

    if (/(топ|лучш|самая высок|самый приоритет|максимальн)/.test(q)) {{
      const top = [...rows].sort((a, b) => (b.priority_score||0) - (a.priority_score||0))[0];
      return top ? `Сейчас в топе: ${{fmtIdea(top)}}.\\nГипотеза: ${{top.opportunity_hypothesis || '—'}}` : 'Нет данных.';
    }}

    if (/низк.*уверен/.test(q)) {{
      const subset = rows.filter(r => (r.confidence||'').toLowerCase().includes('низ'));
      if (!subset.length) return 'Идей с низкой уверенностью сейчас нет.';
      return 'С низкой уверенностью:\\n' + subset.map(r => '• ' + fmtIdea(r)).join('\\n');
    }}
    if (/высок.*уверен/.test(q)) {{
      const subset = rows.filter(r => (r.confidence||'').toLowerCase().includes('высок'));
      if (!subset.length) return 'Идей с высокой уверенностью пока нет.';
      return 'С высокой уверенностью:\\n' + subset.map(r => '• ' + fmtIdea(r)).join('\\n');
    }}

    const marketNames = [...new Set(rows.map(r => r.market_guess))];
    const marketHit = marketNames.find(m => q.includes(m.toLowerCase()));
    if (marketHit) {{
      const subset = rows.filter(r => r.market_guess === marketHit);
      if (/сколько/.test(q)) return `В «${{marketHit}}» сейчас ${{subset.length}} иде${{subset.length === 1 ? 'я' : 'й'}}.`;
      return `Идеи в «${{marketHit}}»:\\n` + subset.map(r => '• ' + fmtIdea(r)).join('\\n');
    }}

    const groupNames = [...new Set(rows.map(r => r.source_tier).filter(Boolean))];
    const groupHit = groupNames.find(g => q.includes(g.toLowerCase()));
    if (groupHit) {{
      const subset = rows.filter(r => r.source_tier === groupHit);
      return subset.length ? `Группа «${{groupHit}}»:\\n` + subset.map(r => '• ' + fmtIdea(r)).join('\\n') : `Пока нет идей из группы «${{groupHit}}».`;
    }}

    if (/(почему|разбивк|из чего|критери)/.test(q)) {{
      const match = findByFragment(q);
      if (match && match.criteria_scores && Object.keys(match.criteria_scores).length) {{
        const lines = Object.entries(match.criteria_scores).map(([k, v]) => `• ${{window.CRITERIA_LABELS[k] || k}}: ${{v}}/5`);
        return `Разбивка «${{match.title}}» (итог ${{match.priority_score}}/5):\\n` + lines.join('\\n');
      }}
      return 'Не нашёл подходящую идею по названию — попробуйте назвать её словами из заголовка, или для этой записи разбивка ещё не собрана.';
    }}

    if (/сколько.*(всего|идей)|сколько идей/.test(q)) {{
      return `Всего идей в бэклоге: ${{rows.length}}.`;
    }}

    const direct = findByFragment(q);
    if (direct) {{
      return `${{fmtIdea(direct)}}\\nГипотеза: ${{direct.opportunity_hypothesis || '—'}}\\nОсновные неизвестные: ${{direct.key_unknowns || '—'}}`;
    }}

    return 'Могу подсказать: сколько идей зелёных/жёлтых/красных, какая идея топ по оценке, что в рынке «Классифайд»/«Подработка»/«HR-tech», идеи из группы источников (например «РФ/ТГ» или «Мировой»), идеи с низкой или высокой уверенностью, разбивку оценки конкретной идеи — назовите её словами из заголовка.';
  }}

  function handleQuestion(text) {{
    if (!text.trim()) return;
    addMsg(text, 'user');
    setTimeout(() => addMsg(answer(text), 'bot'), 150);
  }}

  form.addEventListener('submit', e => {{
    e.preventDefault();
    const text = input.value;
    input.value = '';
    handleQuestion(text);
  }});

  chips.addEventListener('click', e => {{
    if (!e.target.classList.contains('chip')) return;
    handleQuestion(e.target.textContent);
  }});

  addMsg('Привет! Спросите что-нибудь про идеи в бэклоге — отвечаю по данным на странице, ничего никуда не отправляю.', 'bot');
</script>
</body>
</html>"""


def load_rows():
    if not BACKLOG_PATH.exists():
        return []
    with BACKLOG_PATH.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def traffic_light(score_str, thresholds):
    try:
        score = float(score_str)
    except (TypeError, ValueError):
        return "grey"
    if score >= thresholds["green_min"]:
        return "green"
    if score >= thresholds["yellow_min"]:
        return "yellow"
    return "red"


def render_new_tip(row):
    """Тултип для тега статуса 'new': сколько дней назад сигнал был
    найден (date_found), с реальным числом вместо X. Считается на момент
    сборки дашборда — обновится при следующем запуске пайплайна."""
    raw = (row.get("date_found") or "").strip()
    if not raw:
        return ""
    try:
        found = date.fromisoformat(raw)
    except ValueError:
        return ""
    days_ago = (date.today() - found).days
    if days_ago <= 0:
        return f"Сигнал найден сегодня ({raw})"
    unit = "день" if days_ago % 10 == 1 and days_ago % 100 != 11 else (
        "дня" if 2 <= days_ago % 10 <= 4 and not 12 <= days_ago % 100 <= 14 else "дней"
    )
    return f"Сигнал найден {days_ago} {unit} назад ({raw})"


def render_breakdown(row):
    raw = row.get("criteria_scores") or ""
    try:
        scores = json.loads(raw) if raw else {}
    except (json.JSONDecodeError, TypeError):
        scores = {}
    if not scores:
        return ""
    items = "".join(
        f'<li class="crit-row" data-criterion="{html.escape(k)}">'
        f'<span class="crit-name" tabindex="0" data-tip="{html.escape(CRITERIA_DESCRIPTIONS.get(k, ""))}">'
        f'{html.escape(CRITERIA_LABELS.get(k, k))}</span>'
        f'<span class="val"><input class="crit-input" type="number" min="1" max="5" step="1" '
        f'value="{html.escape(str(v))}" data-orig="{html.escape(str(v))}" data-criterion="{html.escape(k)}">'
        f'/5<span class="override-flag" tabindex="0" data-tip="Изменено вручную" hidden>✎</span></span></li>'
        for k, v in scores.items()
    )
    return (
        '<details class="card-collapse breakdown"><summary>Разбивка оценки</summary>'
        '<button type="button" class="reset-btn" data-reset-scores disabled>Вернуть к оценке агента</button>'
        f'<ul>{items}</ul></details>'
    )


def render_description_blocks(row):
    fact = (row.get("fact") or "").strip()
    interp = (row.get("interpretation") or "").strip()
    news_body = " ".join(p for p in (fact, interp) if p) or "—"
    avito_body = (row.get("opportunity_hypothesis") or "").strip() or "—"
    return (
        f'<details class="card-collapse"><summary>Идея из новости</summary>'
        f'<p class="body">{html.escape(news_body)}</p></details>'
        f'<details class="card-collapse" open><summary>Применимость для Авито</summary>'
        f'<p class="body">{html.escape(avito_body)}</p></details>'
    )


def render_misc_block(row, confidence, window_flag):
    items = [f'<li><a href="{html.escape(row.get("source_url",""))}" target="_blank" rel="noopener">Источник</a></li>']
    items.append(f'<li>Дата источника: {html.escape(row.get("source_date","") or "—")}</li>')
    items.append(f'<li>Уверенность: {html.escape(confidence)}</li>')
    if window_flag:
        items.append('<li><span class="flag">вне целевого окна</span></li>')
    return (
        '<details class="card-collapse misc"><summary>Прочее</summary>'
        f'<ul class="misc-list">{"".join(items)}</ul></details>'
    )


def render_comments_block():
    return (
        '<details class="card-collapse comments"><summary>Комментарии</summary>'
        '<div class="comment-list" data-comment-list></div>'
        '<form class="comment-form" data-comment-form>'
        '<input type="text" placeholder="Оставить комментарий…" maxlength="500" data-comment-input>'
        '<button type="submit">Добавить</button>'
        '</form>'
        '<p class="disabled-note" data-comment-disabled hidden>Комментарии видны только при настроенном Firebase (см. README).</p>'
        '</details>'
    )


def render_vote_row():
    return (
        '<div class="vote-row" data-vote-row>'
        '<button type="button" class="vote-btn" data-vote="out">🗑 Отмели <span data-vote-count="out">0</span></button>'
        '<button type="button" class="vote-btn" data-vote="maybe">🤔 Сомнительно, но окей <span data-vote-count="maybe">0</span></button>'
        '<button type="button" class="vote-btn" data-vote="hot">🔥 Очень интересно <span data-vote-count="hot">0</span></button>'
        '</div>'
        '<p class="vote-hint" data-vote-hint hidden>Голос можно изменить в любой момент — сигнал может вернуться'
        ' на 1-й уровень или перейти в другую группу 2-го уровня.</p>'
    )


def render_card(row, color, idx, group_lookup, group_descriptions, market_descriptions):
    market = row.get("market_guess", "") or "Не определено"
    cluster = row.get("cluster_id", "")
    status = row.get("status", "") or "new"
    confidence = row.get("confidence", "") or "—"
    score = row.get("priority_score") or "—"
    # Поле называется source_tier по историческим причинам (раньше здесь
    # хранился номер tier), но с переходом на именованные группы источников
    # хранит название группы — колонку решили не переименовывать, чтобы не
    # заставлять вас вручную править уже накопленный backlog.csv.
    group = (row.get("source_tier") or "").strip()
    in_window = str(row.get("in_target_window", "")).strip().lower()
    window_flag = in_window in ("false", "0", "нет")

    market_tip = html.escape(market_descriptions.get(market, ""))
    badges = f'<span class="pill market" tabindex="0" data-tip="{market_tip}">{html.escape(market)}</span>'
    if group:
        group_sources = group_lookup.get(group, [])
        tip_parts = []
        if group_descriptions.get(group):
            tip_parts.append(group_descriptions[group])
        if group_sources:
            tip_parts.append("Источники: " + ", ".join(group_sources))
        group_tip = html.escape(" ".join(tip_parts))
        badges += f'<span class="pill" tabindex="0" data-tip="{group_tip}">{html.escape(group)}</span>'
    if cluster:
        badges += f'<span class="pill">кластер {html.escape(cluster)}</span>'

    status_attrs = ""
    if status == "new":
        tip = render_new_tip(row)
        if tip:
            status_attrs = f' tabindex="0" data-tip="{html.escape(tip)}"'

    key = html.escape((row.get("source_url", "").strip() or row.get("title", "")) + "|" + row.get("title", ""))
    has_breakdown = bool((row.get("criteria_scores") or "").strip())

    return f"""
    <article class="card {color}" data-key="{key}" data-idx="{idx}" data-baked-color="{color}" data-orig-color="{color}" data-market="{html.escape(market)}" data-color="{color}" data-group="{html.escape(group)}" data-score="{html.escape(str(score))}" data-date="{html.escape(row.get('source_date',''))}">
      <div class="card-top">
        <span class="pill {color}">{html.escape(str(score))}</span>
        <span class="pill status"{status_attrs}>{html.escape(status)}</span>
      </div>
      <h3>{html.escape(row.get('title',''))}</h3>
      <div class="badges">{badges}</div>
      {render_description_blocks(row)}
      {render_misc_block(row, confidence, window_flag)}
      {render_breakdown(row) if has_breakdown else ""}
      {render_vote_row()}
      {render_comments_block()}
    </article>"""


def build_signals_json(rows, colors):
    """Готовит компактный список сигналов для клиентского помощника и для
    живого пересчёта оценки/цвета (recomputeAll) — те же данные, что уже
    на странице, просто в удобной для JS форме, в том же порядке, что и
    карточки (индекс i соответствует data-idx карточки)."""
    signals = []
    for row, color in zip(rows, colors):
        raw = row.get("criteria_scores") or ""
        try:
            criteria_scores = json.loads(raw) if raw else {}
        except (json.JSONDecodeError, TypeError):
            criteria_scores = {}
        signals.append({
            "title": row.get("title", ""),
            "market_guess": row.get("market_guess", "") or "Не определено",
            "priority_score": _to_float(row.get("priority_score")),
            "confidence": row.get("confidence", ""),
            "opportunity_hypothesis": row.get("opportunity_hypothesis", ""),
            "key_unknowns": row.get("key_unknowns", ""),
            "criteria_scores": criteria_scores,
            "color": color,
            "source_tier": row.get("source_tier", ""),
            "status": row.get("status", ""),
        })
    # экранируем "</" на случай спецсимволов в тексте, чтобы не оборвать <script>
    return json.dumps(signals, ensure_ascii=False).replace("</", "<\\/")


def _to_float(value):
    try:
        return round(float(value), 1)
    except (TypeError, ValueError):
        return None


def build_group_lookup(cfg):
    """Группирует источники из config -> sources по source.group, для
    подсказки и для фильтра. Порядок групп — как в config -> source_groups
    (если он есть), иначе как источники впервые встретились в sources."""
    by_group = {}
    for src in cfg.get("sources", []):
        g = str(src.get("group") or "Без группы")
        by_group.setdefault(g, []).append(src.get("name", "?"))
    declared_order = list((cfg.get("source_groups") or {}).keys())
    ordered = {g: by_group[g] for g in declared_order if g in by_group}
    for g, names in by_group.items():
        if g not in ordered:
            ordered[g] = names
    return ordered


def market_description(name, markets_cfg):
    cfg_desc = (markets_cfg.get(name) or {}).get("description")
    if cfg_desc:
        return cfg_desc
    return DEFAULT_MARKET_DESCRIPTIONS.get(name, "")


def build():
    cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    thresholds = cfg["traffic_light_thresholds"]
    dash_cfg = cfg.get("dashboard", {})
    title = dash_cfg.get("title", DEFAULT_TITLE)
    accent = dash_cfg.get("accent_color", DEFAULT_ACCENT)
    markets_cfg = cfg.get("markets", {})
    weights_cfg = cfg.get("priority_weights", {}) or dict.fromkeys(CRITERIA_LABELS, 1.0)
    votes_to_promote = int(cfg.get("votes_to_promote", 3) or 3)
    cadence_days = int(cfg.get("update_cadence_days", 7) or 7)

    rows = load_rows()
    colors = [traffic_light(r.get("priority_score"), thresholds) for r in rows]

    markets = sorted({r.get("market_guess", "Не определено") for r in rows})
    all_tabs = ["Все"] + (markets or ["Нет данных"])
    tabs_html = "".join(
        f'<button class="tab{" active" if m == "Все" else ""}" data-market="{html.escape(m)}">{html.escape(m)}</button>'
        for m in all_tabs
    )

    market_descriptions = {name: market_description(name, markets_cfg) for name in markets_cfg.keys()}
    for m in markets:
        market_descriptions.setdefault(m, DEFAULT_MARKET_DESCRIPTIONS.get(m, ""))
    market_pop_html = "".join(
        f"<p><b>{html.escape(name)}:</b> {html.escape(desc)}</p>"
        for name, desc in market_descriptions.items() if name != "Не определено"
    ) or "<p>Рынки ещё не заданы в config/parameters.yaml -> markets</p>"

    source_groups_cfg = cfg.get("source_groups", {}) or {}
    group_descriptions = {name: (info or {}).get("description", "") for name, info in source_groups_cfg.items()}
    group_lookup = build_group_lookup(cfg)
    group_values = list(group_lookup.keys())
    group_buttons_html = '<button class="groupbtn active" data-group="Все">Все</button>' + "".join(
        f'<button class="groupbtn" data-group="{html.escape(g)}">{html.escape(g)}</button>' for g in group_values
    )
    group_pop_html = "".join(
        f"<p><b>{html.escape(g)}:</b> {html.escape(group_descriptions.get(g, ''))}"
        f"<br>Источники: {html.escape(', '.join(names))}</p>"
        for g, names in group_lookup.items()
    ) or "<p>Источники ещё не заданы в config/parameters.yaml -> sources</p>"

    cards_html = "".join(
        render_card(r, c, i, group_lookup, group_descriptions, market_descriptions)
        for i, (r, c) in enumerate(zip(rows, colors))
    ) or '<p class="empty">Пока нет сигналов</p>'
    signals_json = build_signals_json(rows, colors)
    criteria_labels_json = json.dumps(CRITERIA_LABELS, ensure_ascii=False)
    weights_json = json.dumps(weights_cfg, ensure_ascii=False)
    thresholds_json = json.dumps(
        {"green_min": thresholds["green_min"], "yellow_min": thresholds["yellow_min"]}, ensure_ascii=False
    )

    firebase_cfg = dash_cfg.get("firebase", {}) or {}
    firebase_enabled = bool(str(firebase_cfg.get("api_key", "")).strip())
    if firebase_enabled:
        firebase_scripts_html = (
            '<script src="https://www.gstatic.com/firebasejs/10.12.2/firebase-app-compat.js"></script>\n'
            '<script src="https://www.gstatic.com/firebasejs/10.12.2/firebase-firestore-compat.js"></script>'
        )
        firebase_config_json = json.dumps({
            "apiKey": firebase_cfg.get("api_key", ""),
            "authDomain": firebase_cfg.get("auth_domain", ""),
            "projectId": firebase_cfg.get("project_id", ""),
            "storageBucket": firebase_cfg.get("storage_bucket", ""),
            "messagingSenderId": firebase_cfg.get("messaging_sender_id", ""),
            "appId": firebase_cfg.get("app_id", ""),
        }, ensure_ascii=False)
    else:
        firebase_scripts_html = ""
        firebase_config_json = "null"

    repo = str(dash_cfg.get("github_repo", "")).strip()
    if repo:
        refresh_href = f"https://github.com/{html.escape(repo)}/actions/workflows/weekly_signal_scan.yml"
        refresh_disabled_class = ""
        refresh_note = "Откроется страница Actions на GitHub — там нажмите «Run workflow», чтобы запустить обновление."
    else:
        refresh_href = "#"
        refresh_disabled_class = " disabled"
        refresh_note = "Укажите dashboard.github_repo (вида username/repo) в config/parameters.yaml, чтобы кнопка вела на страницу запуска."

    OUT_PATH.parent.mkdir(exist_ok=True)
    OUT_PATH.write_text(
        TEMPLATE.format(
            title=html.escape(title),
            accent=accent,
            total=len(rows),
            green_count=colors.count("green"),
            yellow_count=colors.count("yellow"),
            red_count=colors.count("red"),
            tabs=tabs_html,
            market_pop=market_pop_html,
            group_buttons=group_buttons_html,
            group_pop=group_pop_html,
            cards=cards_html,
            signals_json=signals_json,
            criteria_labels_json=criteria_labels_json,
            weights_json=weights_json,
            thresholds_json=thresholds_json,
            votes_to_promote=votes_to_promote,
            cadence_days=cadence_days,
            refresh_href=refresh_href,
            refresh_disabled_class=refresh_disabled_class,
            refresh_note=refresh_note,
            firebase_scripts=firebase_scripts_html,
            firebase_config_json=firebase_config_json,
        ),
        encoding="utf-8",
    )
    print(f"Дашборд собран: {OUT_PATH} ({len(rows)} идей)")


if __name__ == "__main__":
    build()
