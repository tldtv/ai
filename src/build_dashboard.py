"""
build_dashboard.py — читает data/backlog.csv и собирает docs/index.html:
дашборд со сводной статистикой (светофор + статус «Отмели»), вкладками по
рынкам с подсказкой о каждом рынке, фильтром по тиру источника (с
подсказкой, какие источники в каком тире), фильтрами по диапазону оценки
и по диапазону даты источника, сортировкой, разбивкой оценки по критериям
с подсказками на каждой карточке, кнопкой «Отмели»/«Вернуть в цвет» и
кнопкой перехода к ручному запуску обновления в GitHub Actions.

Пороги светофора и визуальные настройки — из config/parameters.yaml ->
dashboard / traffic_light_thresholds / markets / sources.

docs/index.html обслуживается GitHub Pages (Settings -> Pages -> Deploy
from a branch -> main -> /docs), поэтому после каждого пуша страница по
тому же адресу обновляется сама.
"""
import csv
import html
import json
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

# Названия и метки светофора. "grey" — не только сигналы без валидной
# оценки, но и основной смысл цвета: идеи, вручную отправленные в "Отмели".
STAT_META = [
    ("total", "Все", "Всего идей", "", "total"),
    ("green", "green", "Топ идея", "🟢", "green"),
    ("yellow", "yellow", "Внимательно изучить", "🟡", "yellow"),
    ("red", "red", "Посмотреть в полглаза", "🔴", "red"),
    ("grey", "grey", "Отмели", "⚪", "grey"),
]

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
  .sub{{color:var(--ink-soft);font-size:13px;margin:0 0 22px;}}

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

  .stats{{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:22px;}}
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

  .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(270px,1fr));gap:14px;}}
  .card{{background:var(--surface);border:1px solid var(--border);border-left:5px solid var(--grey);border-radius:10px;padding:14px;transition:opacity .15s ease;}}
  .card.green{{border-left-color:var(--green);}}
  .card.yellow{{border-left-color:var(--yellow);}}
  .card.red{{border-left-color:var(--red);}}
  .card.grey{{border-left-color:var(--grey);}}
  .card.shelved{{opacity:.65;}}
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
  .hyp{{font-size:13px;color:#444;margin:0 0 8px;}}
  .meta{{font-size:11.5px;color:var(--ink-soft);display:flex;flex-wrap:wrap;gap:4px 6px;align-items:center;margin:0 0 6px;}}
  .meta a{{color:var(--accent);}}
  .flag{{font-size:10.5px;color:var(--yellow-ink);background:var(--yellow-soft);padding:1px 6px;border-radius:999px;}}
  .empty{{color:#999;font-size:13px;grid-column:1/-1;}}

  .breakdown{{margin-top:6px;}}
  .breakdown summary{{font-size:12px;color:var(--accent);cursor:pointer;list-style:none;}}
  .breakdown summary::-webkit-details-marker{{display:none;}}
  .breakdown summary::before{{content:"▸ ";}}
  .breakdown[open] summary::before{{content:"▾ ";}}
  .breakdown ul{{list-style:none;margin:8px 0 0;padding:0;font-size:12px;}}
  .breakdown li{{display:flex;justify-content:space-between;padding:3px 0;border-top:1px dashed var(--border);}}
  .breakdown li:first-child{{border-top:none;}}
  .breakdown .val{{font-weight:700;color:var(--ink);}}

  .card-actions{{margin-top:10px;display:flex;gap:8px;}}
  .shelve-btn, .restore-btn{{font:inherit;font-size:11.5px;padding:5px 11px;border-radius:7px;border:1px solid var(--border);background:var(--bg);color:var(--ink-soft);cursor:pointer;}}
  .shelve-btn:hover, .restore-btn:hover{{border-color:var(--accent);color:var(--accent);}}
  .card:not(.shelved) .restore-btn{{display:none;}}
  .card.shelved .shelve-btn{{display:none;}}

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
    </span>
  </h1>
  <p class="sub">Бэклог сигналов · </p>

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
    <button class="stat grey" data-color="grey">
      <span class="n" id="statGrey">{grey_count}</span><span class="l">⚪ Отмели</span>
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

<script>
  window.SIGNALS = {signals_json};
  window.CRITERIA_LABELS = {criteria_labels_json};
</script>
{firebase_scripts}
<script>
  const state = {{ market: 'Все', color: 'Все', group: 'Все', sort: 'score', scoreMin: 0, scoreMax: 5, dateFrom: '', dateTo: '' }};
  const grid = document.getElementById('grid');
  const cards = () => Array.from(grid.querySelectorAll('.card'));

  // ---- Отмели / вернуть в цвет ----
  // Если в config/parameters.yaml -> dashboard.firebase заполнены ключи —
  // состояние "Отмели" общее для всех, хранится в Firestore и обновляется
  // у всех открытых вкладок практически сразу (см. README). Если нет —
  // используется localStorage этого браузера как раньше: отметка видна
  // только на этом устройстве, не синхронизируется между коллегами.
  const FIREBASE_CONFIG = {firebase_config_json};
  const FIREBASE_ENABLED = !!(FIREBASE_CONFIG && FIREBASE_CONFIG.apiKey);
  let db = null;
  if (FIREBASE_ENABLED) {{
    try {{
      firebase.initializeApp(FIREBASE_CONFIG);
      db = firebase.firestore();
    }} catch (e) {{
      console.error('Firebase не инициализировался — "Отмели" будет работать только локально в этом браузере:', e);
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

  const SHELF_KEY = 'avitoShelvedIdeas';
  let shelved = new Set();
  try {{
    shelved = new Set(JSON.parse(localStorage.getItem(SHELF_KEY) || '[]'));
  }} catch (e) {{ /* localStorage недоступен (приватный режим и т.п.) — просто не сохраняем между визитами */ }}
  function persistShelved() {{
    try {{ localStorage.setItem(SHELF_KEY, JSON.stringify([...shelved])); }} catch (e) {{}}
  }}

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

  function hydrateShelved() {{
    cards().forEach(c => {{
      if (shelved.has(c.dataset.key)) {{
        c.classList.add('shelved');
        setCardColor(c, 'grey');
      }}
    }});
  }}

  function recomputeStats() {{
    const counts = {{ green: 0, yellow: 0, red: 0, grey: 0 }};
    cards().forEach(c => {{ counts[c.dataset.color] = (counts[c.dataset.color] || 0) + 1; }});
    document.getElementById('statGreen').textContent = counts.green;
    document.getElementById('statYellow').textContent = counts.yellow;
    document.getElementById('statRed').textContent = counts.red;
    document.getElementById('statGrey').textContent = counts.grey;
  }}

  grid.addEventListener('click', e => {{
    const shelveBtn = e.target.closest('.shelve-btn');
    if (shelveBtn) {{
      const card = shelveBtn.closest('.card');
      if (db) {{
        db.collection('shelved_ideas').doc(card.dataset.fid).set({{
          shelved: true, key: card.dataset.key, updated: firebase.firestore.FieldValue.serverTimestamp(),
        }}).catch(err => console.error('Не удалось записать "Отмели" в Firestore:', err));
      }} else {{
        shelved.add(card.dataset.key);
        persistShelved();
        card.classList.add('shelved');
        setCardColor(card, 'grey');
        recomputeStats();
        apply();
      }}
      return;
    }}
    const restoreBtn = e.target.closest('.restore-btn');
    if (restoreBtn) {{
      const card = restoreBtn.closest('.card');
      if (db) {{
        db.collection('shelved_ideas').doc(card.dataset.fid).delete()
          .catch(err => console.error('Не удалось убрать "Отмели" в Firestore:', err));
      }} else {{
        shelved.delete(card.dataset.key);
        persistShelved();
        card.classList.remove('shelved');
        setCardColor(card, card.dataset.origColor);
        recomputeStats();
        apply();
      }}
    }}
  }});

  function apply() {{
    let visible = 0;
    cards().forEach(c => {{
      const matchMarket = state.market === 'Все' || c.dataset.market === state.market;
      const matchColor = state.color === 'Все' || c.dataset.color === state.color;
      const matchGroup = state.group === 'Все' || c.dataset.group === state.group;
      const score = parseFloat(c.dataset.score);
      const matchScore = isNaN(score) || (score >= state.scoreMin && score <= state.scoreMax);
      const d = c.dataset.date || '';
      const matchDate = (!state.dateFrom || !d || d >= state.dateFrom) && (!state.dateTo || !d || d <= state.dateTo);
      const show = matchMarket && matchColor && matchGroup && matchScore && matchDate;
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
    document.querySelectorAll('.stat').forEach(s => s.classList.toggle('active', s === btn));
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

  sortCards();
  recomputeStats();
  apply();

  if (db) {{
    try {{
      db.collection('shelved_ideas').onSnapshot(snap => {{
        const ids = new Set();
        snap.forEach(doc => ids.add(doc.id));
        cards().forEach(c => {{
          const isShelved = ids.has(c.dataset.fid);
          c.classList.toggle('shelved', isShelved);
          setCardColor(c, isShelved ? 'grey' : c.dataset.origColor);
        }});
        recomputeStats();
        apply();
      }}, err => {{
        console.error('Firestore недоступен — "Отмели" работает только локально в этом браузере:', err);
      }});
    }} catch (e) {{
      console.error('Не удалось подписаться на Firestore:', e);
      db = null;
      hydrateShelved();
      recomputeStats();
      apply();
    }}
  }} else {{
    hydrateShelved();
    recomputeStats();
    apply();
  }}

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


def render_breakdown(row):
    raw = row.get("criteria_scores") or ""
    try:
        scores = json.loads(raw) if raw else {}
    except (json.JSONDecodeError, TypeError):
        scores = {}
    if not scores:
        return ""
    items = "".join(
        f'<li><span class="crit-name" tabindex="0" data-tip="{html.escape(CRITERIA_DESCRIPTIONS.get(k, ""))}">'
        f'{html.escape(CRITERIA_LABELS.get(k, k))}</span>'
        f'<span class="val">{html.escape(str(v))}/5</span></li>'
        for k, v in scores.items()
    )
    return f'<details class="breakdown"><summary>Разбивка оценки</summary><ul>{items}</ul></details>'


def render_card(row, color, group_lookup, group_descriptions, market_descriptions):
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
    window_flag = '<span class="flag">вне целевого окна</span>' if in_window in ("false", "0", "нет") else ""

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

    key = html.escape((row.get("source_url", "").strip() or row.get("title", "")) + "|" + row.get("title", ""))

    return f"""
    <article class="card {color}" data-key="{key}" data-orig-color="{color}" data-market="{html.escape(market)}" data-color="{color}" data-group="{html.escape(group)}" data-score="{html.escape(str(score))}" data-date="{html.escape(row.get('source_date',''))}">
      <div class="card-top">
        <span class="pill {color}">{html.escape(str(score))}</span>
        <span class="pill status">{html.escape(status)}</span>
      </div>
      <h3>{html.escape(row.get('title',''))}</h3>
      <div class="badges">{badges}</div>
      <p class="hyp">{html.escape(row.get('opportunity_hypothesis','') or '—')}</p>
      <p class="meta">
        <a href="{html.escape(row.get('source_url',''))}" target="_blank" rel="noopener">источник</a>
        · {html.escape(row.get('source_date','') or '—')}
        · уверенность: {html.escape(confidence)}
        {window_flag}
      </p>
      {render_breakdown(row)}
      <div class="card-actions">
        <button class="shelve-btn" type="button">Отмели</button>
        <button class="restore-btn" type="button">Вернуть в цвет</button>
      </div>
    </article>"""


def build_signals_json(rows, colors):
    """Готовит компактный список сигналов для клиентского помощника —
    те же данные, что уже на странице, просто в удобной для JS форме."""
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

    cards_html = "".join(render_card(r, c, group_lookup, group_descriptions, market_descriptions) for r, c in zip(rows, colors)) or '<p class="empty">Пока нет сигналов</p>'
    signals_json = build_signals_json(rows, colors)
    criteria_labels_json = json.dumps(CRITERIA_LABELS, ensure_ascii=False)

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
            grey_count=colors.count("grey"),
            tabs=tabs_html,
            market_pop=market_pop_html,
            group_buttons=group_buttons_html,
            group_pop=group_pop_html,
            cards=cards_html,
            signals_json=signals_json,
            criteria_labels_json=criteria_labels_json,
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
