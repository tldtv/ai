"""
build_dashboard.py — читает data/backlog.csv и собирает docs/index.html:
дашборд со сводной статистикой, вкладками по рынкам, светофором
(красный/жёлтый/зелёный) по полю priority_score и сортировкой/фильтрами.
Пороги светофора и визуальные настройки (акцентный цвет, заголовок)
берутся из config/parameters.yaml -> dashboard / traffic_light_thresholds.

docs/index.html обслуживается GitHub Pages (Settings -> Pages -> Deploy
from a branch -> main -> /docs), поэтому после каждого пуша страница по
тому же адресу обновляется сама.
"""
import csv
import html
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
    --grey: #a3a3a3;     --grey-soft: #eee;      --grey-ink: #777;
  }}
  *{{box-sizing:border-box;}}
  body{{font-family:system-ui,-apple-system,'Segoe UI',sans-serif;background:var(--bg);color:var(--ink);margin:0;padding:32px 24px 60px;}}
  h1{{font-size:22px;margin:0 0 4px;}}
  .sub{{color:var(--ink-soft);font-size:13px;margin:0 0 22px;}}

  .stats{{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:22px;}}
  .stat{{flex:1;min-width:110px;background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:12px 14px;cursor:pointer;text-align:left;font:inherit;border-left:4px solid var(--grey);}}
  .stat .n{{display:block;font-size:22px;font-weight:700;line-height:1.1;}}
  .stat .l{{display:block;font-size:11.5px;color:var(--ink-soft);margin-top:2px;}}
  .stat.total{{border-left-color:var(--accent);}}
  .stat.green{{border-left-color:var(--green);}}
  .stat.yellow{{border-left-color:var(--yellow);}}
  .stat.red{{border-left-color:var(--red);}}
  .stat.active{{outline:2px solid var(--accent);outline-offset:-1px;}}

  .toolbar{{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px;margin-bottom:18px;}}
  .tabs, .sort{{display:flex;gap:8px;flex-wrap:wrap;}}
  .tab, .sortbtn{{font:inherit;font-size:13px;padding:6px 14px;border-radius:999px;border:1px solid var(--border);background:var(--surface);cursor:pointer;color:var(--ink);}}
  .tab.active, .sortbtn.active{{background:var(--accent);color:#fff;border-color:var(--accent);}}
  .sort{{font-size:12px;color:var(--ink-soft);align-items:center;}}

  .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(270px,1fr));gap:14px;}}
  .card{{background:var(--surface);border:1px solid var(--border);border-left:5px solid var(--grey);border-radius:10px;padding:14px;}}
  .card.green{{border-left-color:var(--green);}}
  .card.yellow{{border-left-color:var(--yellow);}}
  .card.red{{border-left-color:var(--red);}}
  .card.hidden{{display:none;}}
  .card-top{{display:flex;justify-content:space-between;align-items:center;gap:8px;}}
  .badges{{display:flex;gap:6px;flex-wrap:wrap;}}
  .card h3{{font-size:15px;margin:8px 0 6px;text-wrap:balance;}}
  .pill{{font-size:11px;font-weight:700;padding:2px 8px;border-radius:999px;background:var(--grey-soft);color:var(--grey-ink);white-space:nowrap;}}
  .pill.green{{background:var(--green-soft);color:var(--green-ink);}}
  .pill.yellow{{background:var(--yellow-soft);color:var(--yellow-ink);}}
  .pill.red{{background:var(--red-soft);color:var(--red-ink);}}
  .pill.market{{background:var(--accent-soft);color:var(--accent);}}
  .pill.status{{background:var(--grey-soft);color:var(--grey-ink);}}
  .hyp{{font-size:13px;color:#444;margin:0 0 8px;}}
  .meta{{font-size:11.5px;color:var(--ink-soft);display:flex;flex-wrap:wrap;gap:4px 6px;align-items:center;}}
  .meta a{{color:var(--accent);}}
  .flag{{font-size:10.5px;color:var(--yellow-ink);background:var(--yellow-soft);padding:1px 6px;border-radius:999px;}}
  .empty{{color:#999;font-size:13px;grid-column:1/-1;}}
</style>
</head>
<body>
  <h1>{title}</h1>
  <p class="sub">Бэклог сигналов · авто-обновление по расписанию · пороги и цвет — из config/parameters.yaml</p>

  <div class="stats" id="stats">
    <button class="stat total active" data-color="Все">
      <span class="n">{total}</span><span class="l">Всего идей</span>
    </button>
    <button class="stat green" data-color="green">
      <span class="n">{green_count}</span><span class="l">🟢 Зелёных</span>
    </button>
    <button class="stat yellow" data-color="yellow">
      <span class="n">{yellow_count}</span><span class="l">🟡 Жёлтых</span>
    </button>
    <button class="stat red" data-color="red">
      <span class="n">{red_count}</span><span class="l">🔴 Красных</span>
    </button>
  </div>

  <div class="toolbar">
    <div class="tabs" id="tabs">{tabs}</div>
    <div class="sort" id="sort">
      Сортировка:
      <button class="sortbtn active" data-sort="score">по оценке</button>
      <button class="sortbtn" data-sort="date">по дате</button>
    </div>
  </div>

  <div class="grid" id="grid">{cards}</div>

<script>
  const state = {{ market: 'Все', color: 'Все', sort: 'score' }};
  const grid = document.getElementById('grid');
  const cards = () => Array.from(grid.querySelectorAll('.card'));

  function apply() {{
    let visible = 0;
    cards().forEach(c => {{
      const matchMarket = state.market === 'Все' || c.dataset.market === state.market;
      const matchColor = state.color === 'Все' || c.dataset.color === state.color;
      const show = matchMarket && matchColor;
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
    document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t === e.target));
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

  sortCards();
  apply();
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


def render_card(row, color):
    market = row.get("market_guess", "") or "Не определено"
    cluster = row.get("cluster_id", "")
    status = row.get("status", "") or "new"
    confidence = row.get("confidence", "") or "—"
    score = row.get("priority_score") or "—"
    in_window = str(row.get("in_target_window", "")).strip().lower()
    window_flag = '<span class="flag">вне целевого окна</span>' if in_window in ("false", "0", "нет") else ""

    badges = f'<span class="pill market">{html.escape(market)}</span>'
    if cluster:
        badges += f'<span class="pill">кластер {html.escape(cluster)}</span>'

    return f"""
    <article class="card {color}" data-market="{html.escape(market)}" data-color="{color}" data-score="{html.escape(str(score))}" data-date="{html.escape(row.get('source_date',''))}">
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
    </article>"""


def build():
    cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    thresholds = cfg["traffic_light_thresholds"]
    dash_cfg = cfg.get("dashboard", {})
    title = dash_cfg.get("title", DEFAULT_TITLE)
    accent = dash_cfg.get("accent_color", DEFAULT_ACCENT)

    rows = load_rows()
    colors = [traffic_light(r.get("priority_score"), thresholds) for r in rows]

    markets = sorted({r.get("market_guess", "Не определено") for r in rows})
    all_tabs = ["Все"] + (markets or ["Нет данных"])
    tabs_html = "".join(
        f'<button class="tab{" active" if m == "Все" else ""}" data-market="{html.escape(m)}">{html.escape(m)}</button>'
        for m in all_tabs
    )

    cards_html = "".join(render_card(r, c) for r, c in zip(rows, colors)) or '<p class="empty">Пока нет сигналов</p>'

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
            cards=cards_html,
        ),
        encoding="utf-8",
    )
    print(f"Дашборд собран: {OUT_PATH} ({len(rows)} идей)")


if __name__ == "__main__":
    build()
