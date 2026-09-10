"""
send_digest.py — еженедельная рассылка новых сигналов 1-го уровня
подписчикам (email через Resend, Telegram через Bot API).

Запускается из .github/workflows/weekly_signal_scan.yml сразу после
build_dashboard.py, тем же еженедельным расписанием (см. README).

Почему это единственная часть пайплайна, которой нужен служебный
Firebase-аккаунт (Service Account), а не просто публичный конфиг из
config/parameters.yaml -> dashboard.firebase: список подписчиков
(email/Telegram) — чувствительные данные, и правила Firestore (см.
firestore_rules.txt) намеренно запрещают ЧТЕНИЕ коллекции
digest_subscriptions из браузера — создать подписку может кто угодно,
а прочитать чужую — никто, кроме этого скрипта, у которого через Admin
SDK есть доступ в обход правил.

Требуемые переменные окружения (заданы как GitHub Secrets):
  FIREBASE_SERVICE_ACCOUNT_JSON — содержимое JSON-ключа служебного
    аккаунта (Firebase Console -> Настройки проекта -> Сервисные
    аккаунты -> Generate new private key), вставленное как есть, одним
    секретом.
  RESEND_API_KEY — ключ API resend.com. Нужен, только если есть хотя бы
    один email-подписчик.
  TELEGRAM_BOT_TOKEN — токен бота из @BotFather. Нужен, только если
    есть хотя бы один Telegram-подписчик.
Если какого-то из двух последних ключей нет, а подписчик на этот канал
есть — рассылка на этот канал для этого подписчика пропускается с
пояснением в логе, остальные подписчики это не затрагивает.
"""
import json
import os
from datetime import date, timedelta
from pathlib import Path

import requests
import yaml

import firebase_admin
from firebase_admin import credentials, firestore

from build_dashboard import CONFIG_PATH, load_rows, traffic_light

TELEGRAM_MAX_CHARS = 3500  # запас от лимита Telegram в 4096 символов на сообщение
COLOR_LABELS = {
    "green": "🟢 Топ идея",
    "yellow": "🟡 Внимательно изучить",
    "red": "🔴 Посмотреть в полглаза",
}
COLOR_ORDER = ["green", "yellow", "red"]


# ---- Тот же хэш id карточки, что и в build_dashboard.py (функция
# hashKey в JS) — нужен, чтобы найти голоса сигнала в Firestore
# (signal_votes/{fid}) и понять, не ушёл ли он уже на 2-й уровень.
# Проверено на совпадение с JS построчно (см. историю сессии) — если
# когда-нибудь измените hashKey в build_dashboard.py, поправьте и здесь.
def _imul32(a, b):
    return ((a & 0xFFFFFFFF) * (b & 0xFFFFFFFF)) & 0xFFFFFFFF


def _utf16_code_units(s):
    units = []
    for ch in s:
        cp = ord(ch)
        if cp > 0xFFFF:
            cp -= 0x10000
            units.append(0xD800 + (cp >> 10))
            units.append(0xDC00 + (cp & 0x3FF))
        else:
            units.append(cp)
    return units


def hash_key(s: str) -> str:
    h1 = 0xdeadbeef
    h2 = 0x41c6ce57
    for ch in _utf16_code_units(s):
        h1 = _imul32(h1 ^ ch, 2654435761)
        h2 = _imul32(h2 ^ ch, 1597334677)
    h1 = (_imul32(h1 ^ (h1 >> 16), 2246822507) ^ _imul32(h2 ^ (h2 >> 13), 3266489909)) & 0xFFFFFFFF
    h2 = (_imul32(h2 ^ (h2 >> 16), 2246822507) ^ _imul32(h1 ^ (h1 >> 13), 3266489909)) & 0xFFFFFFFF
    return format(h1, "08x") + format(h2, "08x")


def card_key(row):
    return (row.get("source_url", "").strip() or row.get("title", "")) + "|" + row.get("title", "")


# ---- Статус 2-го уровня — портированная копия computeLevel2Status из
# build_dashboard.py (см. комментарий там же про приоритет при равенстве
# голосов: hot > maybe > out).
def compute_level2_status(counts, votes_to_promote):
    order = ["hot", "maybe", "out"]
    max_count = max((counts.get(k, 0) or 0) for k in order) if counts else 0
    if max_count < votes_to_promote:
        return ""
    for k in order:
        if (counts.get(k, 0) or 0) == max_count:
            return k
    return ""


def _parse_date(raw):
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def load_level1_signals(db, cfg):
    """Бэклог минус всё, что уже ушло на 2-й уровень — то есть ровно то,
    что видно на дашборде в режиме «Уровень 1». Пороги светофора и
    votes_to_promote берутся живыми из admin_config/config, если админ-
    панель их переопределяла, иначе — из config/parameters.yaml (то же
    поведение, что у самого дашборда)."""
    thresholds = dict(cfg.get("traffic_light_thresholds", {}))
    votes_to_promote = int(cfg.get("votes_to_promote", 3) or 3)

    admin_doc = db.collection("admin_config").document("config").get()
    if admin_doc.exists:
        admin_cfg = admin_doc.to_dict() or {}
        if admin_cfg.get("green_min") is not None:
            thresholds["green_min"] = admin_cfg["green_min"]
        if admin_cfg.get("yellow_min") is not None:
            thresholds["yellow_min"] = admin_cfg["yellow_min"]
        if admin_cfg.get("votes_to_promote") is not None:
            votes_to_promote = int(admin_cfg["votes_to_promote"])

    result = []
    for row in load_rows():
        fid = hash_key(card_key(row))
        vote_doc = db.collection("signal_votes").document(fid).get()
        counts = vote_doc.to_dict() if vote_doc.exists else {}
        if compute_level2_status(counts or {}, votes_to_promote):
            continue  # уже провалидировано командой — рассылка шлёт только новое
        row = dict(row)
        row["_color"] = traffic_light(row.get("priority_score"), thresholds)
        row["_fid"] = fid
        result.append(row)
    return result


def matches_subscription(row, sub, cadence_days):
    markets = sub.get("markets") or []
    if markets and row.get("market_guess") not in markets:
        return False
    groups = sub.get("groups") or []
    if groups and row.get("source_tier") not in groups:
        return False
    colors = sub.get("colors") or []
    if colors and row.get("_color") not in colors:
        return False

    recency_days = int(sub.get("recency_days") or 0)
    if recency_days > 0:
        d = _parse_date(row.get("source_date"))
        if not d or d < date.today() - timedelta(days=recency_days):
            return False

    # Дедупликация между рассылками: сигнал показываем не раньше, чем
    # он появился в бэклоге (date_found), и не позже последней успешной
    # отправки этому подписчику — иначе один и тот же сигнал придёт
    # снова на следующей неделе, пока не «состарится» по recency_days.
    # Для ещё ни разу не отправлявшейся подписки история ограничена
    # update_cadence_days (по умолчанию 7) — чтобы первое письмо не
    # вываливало весь исторический бэклог целиком.
    found = _parse_date(row.get("date_found"))
    if not found:
        return False
    cutoff = date.today() - timedelta(days=cadence_days)
    last_sent = sub.get("last_digest_at")
    since = last_sent.date() if last_sent else None
    effective_since = max(since, cutoff) if since else cutoff
    return found > effective_since


def group_for_output(rows):
    by_color = {c: [] for c in COLOR_ORDER}
    for r in rows:
        by_color.setdefault(r.get("_color", "red"), []).append(r)
    for c in by_color:
        by_color[c].sort(key=lambda r: float(r.get("priority_score") or 0), reverse=True)
    return by_color


def build_text_digest(rows, dashboard_url):
    by_color = group_for_output(rows)
    lines = [f"Новых сигналов 1-го уровня: {len(rows)}", ""]
    for color in COLOR_ORDER:
        group = by_color.get(color) or []
        if not group:
            continue
        lines.append(COLOR_LABELS[color])
        for r in group:
            lines.append(
                f"• {r.get('title', '').strip()} ({r.get('priority_score', '')}, "
                f"{r.get('market_guess', '')}) — {r.get('source_url', '')}"
            )
        lines.append("")
    if dashboard_url:
        lines.append(f"Полный дашборд: {dashboard_url}")
    return "\n".join(lines).strip()


def build_html_digest(rows, dashboard_url):
    by_color = group_for_output(rows)
    parts = [
        f"<p>Новых сигналов 1-го уровня: <b>{len(rows)}</b></p>",
    ]
    for color in COLOR_ORDER:
        group = by_color.get(color) or []
        if not group:
            continue
        parts.append(f"<h3 style='margin:18px 0 6px;'>{COLOR_LABELS[color]}</h3><ul style='margin:0;padding-left:20px;'>")
        for r in group:
            title = (r.get("title") or "").strip()
            url = r.get("source_url") or "#"
            parts.append(
                f"<li style='margin-bottom:8px;'><a href='{url}'>{title}</a> "
                f"— {r.get('priority_score', '')}, {r.get('market_guess', '')}, {r.get('source_tier', '')}</li>"
            )
        parts.append("</ul>")
    if dashboard_url:
        parts.append(f"<p style='margin-top:20px;'><a href='{dashboard_url}'>Открыть полный дашборд</a></p>")
    return "\n".join(parts)


def chunk_text(text, max_chars):
    if len(text) <= max_chars:
        return [text]
    chunks, current = [], []
    length = 0
    for line in text.split("\n"):
        if length + len(line) + 1 > max_chars and current:
            chunks.append("\n".join(current))
            current, length = [], 0
        current.append(line)
        length += len(line) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks


def send_email(to_email, subject, html_body, from_addr, api_key):
    resp = requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"from": from_addr, "to": [to_email], "subject": subject, "html": html_body},
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()


def send_telegram_message(chat_id, text, bot_token):
    resp = requests.post(
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()


def resolve_telegram_starts(db, bot_token):
    """Подписчик мог нажать «Start» в боте с тех пор, как мы проверяли
    в последний раз — здесь читаем накопившиеся сообщения боту и
    сопоставляем токен /start <id> с ожидающей подтверждения подпиской
    (id документа в digest_subscriptions == тот же токен, что зашит в
    ссылку на дашборде). Подтверждённые получат рассылку уже сегодня."""
    resp = requests.get(f"https://api.telegram.org/bot{bot_token}/getUpdates", timeout=20)
    resp.raise_for_status()
    updates = resp.json().get("result", [])
    resolved = 0
    for upd in updates:
        msg = upd.get("message") or {}
        text = (msg.get("text") or "").strip()
        if not text.startswith("/start"):
            continue
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            continue
        sub_id = parts[1].strip()
        chat_id = (msg.get("chat") or {}).get("id")
        if not chat_id:
            continue
        doc_ref = db.collection("digest_subscriptions").document(sub_id)
        doc = doc_ref.get()
        if doc.exists:
            data = doc.to_dict() or {}
            if data.get("channel") == "telegram" and not data.get("telegram_chat_id"):
                doc_ref.update({"telegram_chat_id": chat_id})
                resolved += 1
    if updates:
        max_id = max(u["update_id"] for u in updates)
        requests.get(
            f"https://api.telegram.org/bot{bot_token}/getUpdates",
            params={"offset": max_id + 1},
            timeout=20,
        )
    return resolved


def init_firestore():
    sa_raw = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON")
    if not sa_raw:
        raise SystemExit("Не задана переменная окружения FIREBASE_SERVICE_ACCOUNT_JSON")
    cred = credentials.Certificate(json.loads(sa_raw))
    firebase_admin.initialize_app(cred)
    return firestore.client()


def dashboard_url_from_repo(repo):
    if not repo or "/" not in repo:
        return ""
    owner, name = repo.split("/", 1)
    return f"https://{owner}.github.io/{name}/"


def main():
    cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    dash_cfg = cfg.get("dashboard", {}) or {}
    cadence_days = int(cfg.get("update_cadence_days", 7) or 7)
    dashboard_url = dashboard_url_from_repo(dash_cfg.get("github_repo", ""))
    from_addr = dash_cfg.get("digest_from_email") or "Точки роста — Авито.Работа <onboarding@resend.dev>"

    db = init_firestore()

    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if bot_token:
        try:
            n = resolve_telegram_starts(db, bot_token)
            print(f"Подтверждено новых Telegram-подписок: {n}")
        except Exception as e:
            print(f"Не удалось проверить новые Telegram-подписки: {e}")

    rows = load_level1_signals(db, cfg)
    print(f"Сигналов 1-го уровня сейчас: {len(rows)}")

    resend_key = os.environ.get("RESEND_API_KEY")

    sent = skipped_no_match = skipped_no_key = skipped_unconfirmed = 0
    for doc in db.collection("digest_subscriptions").stream():
        sub = doc.to_dict() or {}
        if sub.get("unsubscribed"):
            continue

        matched = [r for r in rows if matches_subscription(r, sub, cadence_days)]
        if not matched:
            skipped_no_match += 1
            continue

        channel = sub.get("channel")
        try:
            if channel == "email" and sub.get("email"):
                if not resend_key:
                    print(f"Пропуск {doc.id}: нет RESEND_API_KEY")
                    skipped_no_key += 1
                    continue
                subject = f"Новые сигналы Авито.Работы: {len(matched)}"
                send_email(sub["email"], subject, build_html_digest(matched, dashboard_url), from_addr, resend_key)
            elif channel == "telegram" and sub.get("telegram_chat_id"):
                if not bot_token:
                    print(f"Пропуск {doc.id}: нет TELEGRAM_BOT_TOKEN")
                    skipped_no_key += 1
                    continue
                for chunk in chunk_text(build_text_digest(matched, dashboard_url), TELEGRAM_MAX_CHARS):
                    send_telegram_message(sub["telegram_chat_id"], chunk, bot_token)
            else:
                # Telegram-подписка, ещё не подтверждённая (нет telegram_chat_id) —
                # подождём следующего запуска, ничего не отправляем и не помечаем.
                skipped_unconfirmed += 1
                continue

            doc.reference.update({"last_digest_at": firestore.SERVER_TIMESTAMP})
            sent += 1
        except Exception as e:
            print(f"Ошибка отправки подписчику {doc.id} ({channel}): {e}")

    print(
        f"Разослано: {sent}. Пропущено — нет новых сигналов под фильтр: {skipped_no_match}, "
        f"не подтверждена Telegram-подписка: {skipped_unconfirmed}, нет ключа API: {skipped_no_key}."
    )


if __name__ == "__main__":
    main()
