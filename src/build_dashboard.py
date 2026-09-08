"""
build_dashboard.py — читает data/backlog.csv и собирает docs/index.html:
статический дашборд с вкладками по рынкам и светофором
(красный/жёлтый/зелёный) по полю priority_score; пороги берутся из
config/parameters.yaml.

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

TEMPLATE = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>Точки роста — Авито.Работа</title>
<style>
  body{{font-family:system-ui,-apple-system,'Segoe UI',sans-serif;background:#F7F5F0;color:#23262B;margin:0;padding:32px 24px;}}
  h1{{font-size:22px;margin:0 0 4px;}}
  .sub{{color:#777;font-size:13px;margin:0 0 20px;}}
  .tabs{{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:20px;}}
  .tab{{font:inherit;padding:6px 14px;border-radius:999px;border:1px solid #ddd;background:#fff;cursor:pointer;}}
  .tab.active{{background:#2C6E6B;color:#fff;border-color:#2C6E6B;}}
  .market-panel{{display:none;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:14px;}}
  .market-panel.active{{display:grid;}}
  .card{{background:#fff;border:1px solid #e3ddd2;border-left:5px solid #ccc;border-radius:10px;padding:14px;}}
  .card.green{{border-left-color:#2f9e59;}}
  .card.yellow{{border-left-color:#c98a1f;}}
  .card.red{{border-left-color:#c0392b;}}
  .card.grey{{border-left-color:#aaa;}}
  .card-top{{display:flex;justify-content:space-between;align-items:center;}}
  .card h3{{font-size:15px;margin:8px 0 6px;}}
  .pill{{font-size:11px;font-weight:700;padding:2px 8px;border-radius:999px;background:#eee;}}
  .pill.green{{background:#e4f5ea;color:#1f6b3d;}}
  .pill.yellow{{background:#fbeed7;color:#8a5a12;}}
  .pill.red{{background:#fbe1de;color:#96291b;}}
  .pill.grey{{background:#eee;color:#777;}}
  .status{{font-size:11px;color:#888;}}
  .hyp{{font-size:13px;color:#444;}}
  .meta{{font-size:11.5px;color:#888;}}
  .empty{{color:#999;font-size:13px;}}
</style>
</head>
<body>
  <h1>Точки роста — Авито.Работа</h1>
  <p class="sub">Бэклог сигналов, авто-обновление по расписанию · пороги светофора из config/parameters.yaml</p>
  <div class="tabs">{tabs}</div>
  {sections}
<script>
  const tabs = document.querySelectorAll('.tab');
  const panels = document.querySelectorAll('.market-panel');
  function show(market) {{
    tabs.forEach(t => t.classList.toggle('active', t.dataset.market === market));
    panels.forEach(p => p.classList.toggle('active', p.dataset.market === market));
  }}
  tabs.forEach(t => t.addEventListener('click', () => show(t.dataset.market)));
  show('Все');
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
    return f"""
    <article class="card {color}">
      <div class="card-top">
        <span class="pill {color}">{html.escape(str(row.get('priority_score') or '—'))}</span>
        <span class="status">{html.escape(row.get('status',''))}</span>
      </div>
      <h3>{html.escape(row.get('title',''))}</h3>
      <p class="hyp">{html.escape(row.get('opportunity_hypothesis','') or '—')}</p>
      <p class="meta">Источник: <a href="{html.escape(row.get('source_url',''))}" target="_blank" rel="noopener">ссылка</a> · {html.escape(row.get('source_date',''))} · уверенность: {html.escape(row.get('confidence','') or '—')}</p>
    </article>"""


def build():
    cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    thresholds = cfg["traffic_light_thresholds"]
    rows = load_rows()

    markets = sorted({r.get("market_guess", "Не определено") for r in rows})
    all_tabs = ["Все"] + (markets or ["Нет данных"])
    tabs_html = "".join(
        f'<button class="tab" data-market="{html.escape(m)}">{html.escape(m)}</button>'
        for m in all_tabs
    )

    sections = []
    for m in all_tabs:
        cards = [
            render_card(r, traffic_light(r.get("priority_score"), thresholds))
            for r in rows if m == "Все" or r.get("market_guess") == m
        ]
        body = "".join(cards) if cards else '<p class="empty">Пока нет сигналов</p>'
        sections.append(f'<section class="market-panel" data-market="{html.escape(m)}">{body}</section>')

    OUT_PATH.parent.mkdir(exist_ok=True)
    OUT_PATH.write_text(TEMPLATE.format(tabs=tabs_html, sections="".join(sections)), encoding="utf-8")
    print(f"Дашборд собран: {OUT_PATH} ({len(rows)} идей)")


if __name__ == "__main__":
    build()
