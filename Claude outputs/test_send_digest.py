"""Тест send_digest.py целиком (оркестрация main()) без реальных
Firebase/Resend/Telegram — Firestore подменён fake_firebase_admin.py,
HTTP-вызовы requests.post/get перехвачены и просто записываются."""
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

import fake_firebase_admin  # noqa: E402

fake_db = fake_firebase_admin.install()

os.environ["FIREBASE_SERVICE_ACCOUNT_JSON"] = '{"type": "service_account"}'
os.environ["RESEND_API_KEY"] = "test-resend-key"
os.environ["TELEGRAM_BOT_TOKEN"] = "test-bot-token"

import send_digest  # noqa: E402

errors = []


def check(label, cond):
    status = "OK" if cond else "FAIL"
    print(f"[{status}] {label}")
    if not cond:
        errors.append(label)


# ---- 1. Синтетический бэклог вместо data/backlog.csv ----
TODAY = send_digest.date.today()
ISO = lambda d: d.isoformat()  # noqa: E731

ROWS = [
    {  # A: сегодняшний HR-tech сигнал, оценка высокая -> green, никто не голосовал
        "title": "Сигнал A: HR-tech новость",
        "source_url": "https://example.com/a",
        "date_found": ISO(TODAY), "source_date": ISO(TODAY),
        "market_guess": "HR-tech", "source_tier": "Мировой",
        "priority_score": "4.5",
    },
    {  # B: уже проголосован до 2-го уровня (hot) -> должен быть ИСКЛЮЧЁН из рассылки
        "title": "Сигнал B: уже на 2-м уровне",
        "source_url": "https://example.com/b",
        "date_found": ISO(TODAY), "source_date": ISO(TODAY),
        "market_guess": "HR-tech", "source_tier": "Мировой",
        "priority_score": "4.8",
    },
    {  # C: старый (10 дней назад) сигнал РФ/ТГ, попадает под cadence=7 -> НЕ должен войти
        "title": "Сигнал C: старый РФ",
        "source_url": "https://example.com/c",
        "date_found": ISO(TODAY - send_digest.timedelta(days=10)), "source_date": ISO(TODAY - send_digest.timedelta(days=10)),
        "market_guess": "Классифайд", "source_tier": "РФ/ТГ",
        "priority_score": "2.0",
    },
    {  # D: жёлтый светофор, найден вчера, группа РФ/ТГ
        "title": "Сигнал D: жёлтый РФ",
        "source_url": "https://example.com/d",
        "date_found": ISO(TODAY - send_digest.timedelta(days=1)), "source_date": ISO(TODAY - send_digest.timedelta(days=1)),
        "market_guess": "Классифайд", "source_tier": "РФ/ТГ",
        "priority_score": "3.0",
    },
    {  # E: найден 3 дня назад -- будет исключён у подписчика с last_digest_at=2 дня назад (дедуп)
        "title": "Сигнал E: для проверки дедупа",
        "source_url": "https://example.com/e",
        "date_found": ISO(TODAY - send_digest.timedelta(days=3)), "source_date": ISO(TODAY - send_digest.timedelta(days=3)),
        "market_guess": "HR-tech", "source_tier": "Мировой",
        "priority_score": "4.0",
    },
]
send_digest.load_rows = lambda: ROWS

# fid сигнала B должен получить 3 голоса "hot" -> считается ушедшим на 2-й уровень
fid_b = send_digest.hash_key(send_digest.card_key(ROWS[1]))
fake_db.seed("signal_votes", fid_b, {"out": 0, "maybe": 0, "hot": 3})

# ---- 2. Конфиг: votes_to_promote=3, cadence=7 (совпадает с parameters.yaml по умолчанию) ----
send_digest.CONFIG_PATH = ROOT / "config" / "parameters.yaml"

# ---- 3. Подписки ----
fake_db.seed("digest_subscriptions", "sub_email_all", {
    "channel": "email", "email": "subscriber@example.com",
    "markets": [], "groups": [], "colors": [],
    "recency_days": 0,
})
fake_db.seed("digest_subscriptions", "sub_telegram_pending", {
    "channel": "telegram",
    "markets": [], "groups": [], "colors": [],
    "recency_days": 0,
})
fake_db.seed("digest_subscriptions", "sub_telegram_confirmed", {
    "channel": "telegram", "telegram_chat_id": 555,
    "markets": ["Классифайд"], "groups": ["РФ/ТГ"], "colors": [],
    "recency_days": 0,
})
fake_db.seed("digest_subscriptions", "sub_unsubscribed", {
    "channel": "email", "email": "gone@example.com", "unsubscribed": True,
    "markets": [], "groups": [], "colors": [],
})
fake_db.seed("digest_subscriptions", "sub_no_match", {
    "channel": "email", "email": "picky@example.com",
    "markets": ["Подработка"], "groups": [], "colors": [],
})
fake_db.seed("digest_subscriptions", "sub_dedup", {
    "channel": "email", "email": "dedup@example.com",
    "markets": [], "groups": [], "colors": [],
    "recency_days": 0,
    # Как в реальном Firestore: Timestamp приходит в Python как datetime,
    # а не date — send_digest.py вызывает .date() на этом значении.
    "last_digest_at": datetime.combine(TODAY - send_digest.timedelta(days=2), datetime.min.time()),
})

# ---- 4. Перехват HTTP ----
sent_emails = []
sent_telegrams = []


class FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def fake_post(url, headers=None, json=None, timeout=None):
    if "resend.com" in url:
        sent_emails.append(json)
        return FakeResp({"id": "fake-email-id"})
    if "sendMessage" in url:
        sent_telegrams.append(json)
        return FakeResp({"ok": True})
    raise AssertionError(f"unexpected POST {url}")


def fake_get(url, timeout=None, params=None):
    if "getUpdates" in url:
        if params and "offset" in params:
            return FakeResp({"result": []})  # вызов "очистки" очереди после обработки
        return FakeResp({
            "result": [
                {
                    "update_id": 1001,
                    "message": {
                        "text": "/start sub_telegram_pending",
                        "chat": {"id": 777},
                    },
                }
            ]
        })
    raise AssertionError(f"unexpected GET {url}")


send_digest.requests.post = fake_post
send_digest.requests.get = fake_get

# ---- 5. Запуск ----
send_digest.main()

# ---- 6. Проверки ----
check("sub_telegram_pending got resolved to chat_id 777",
      fake_db._store["digest_subscriptions"]["sub_telegram_pending"].get("telegram_chat_id") == 777)

check("exactly 2 emails sent (sub_email_all + sub_dedup; sub_no_match/sub_unsubscribed excluded)", len(sent_emails) == 2)
email = next((e for e in sent_emails if e["to"] == ["subscriber@example.com"]), None)
check("sub_email_all received an email", email is not None)
if email:
    html = email["html"]
    check("email includes signal A (green, today)", "Сигнал A" in html)
    check("email EXCLUDES signal B (already level 2)", "Сигнал B" not in html)
    check("email EXCLUDES signal C (older than cadence)", "Сигнал C" not in html)
    check("email includes signal D (yesterday, matches no filter = all)", "Сигнал D" in html)
    check("email includes signal E (3 days ago, no last_digest_at yet)", "Сигнал E" in html)

# both sub_telegram_confirmed (pre-existing chat_id 555) and the just-resolved
# sub_telegram_pending (chat_id 777, now confirmed within this same run) should
# receive a message this run.
check("2 telegram sends happened (pre-confirmed + just-resolved)", len(sent_telegrams) == 2)
chat_ids_sent = sorted(t["chat_id"] for t in sent_telegrams)
check(f"telegram sent to chat 555 and 777 (got {chat_ids_sent})", chat_ids_sent == [555, 777])

confirmed_msg = next((t for t in sent_telegrams if t["chat_id"] == 555), None)
if confirmed_msg:
    check("sub_telegram_confirmed (filtered to Классифайд/РФ/ТГ) gets signal D", "Сигнал D" in confirmed_msg["text"])
    check("sub_telegram_confirmed does NOT get signal A (wrong market/group)", "Сигнал A" not in confirmed_msg["text"])
    check("sub_telegram_confirmed does NOT get signal C (too old)", "Сигнал C" not in confirmed_msg["text"])

check("sub_unsubscribed got nothing (no email sent to gone@example.com)",
      all(e["to"] != ["gone@example.com"] for e in sent_emails))
check("sub_no_match got nothing (no email sent to picky@example.com)",
      all(e["to"] != ["picky@example.com"] for e in sent_emails))
dedup_email = next((e for e in sent_emails if e["to"] == ["dedup@example.com"]), None)
check("sub_dedup received an email (signals A/D are newer than its last_digest_at)", dedup_email is not None)
if dedup_email:
    dedup_html = dedup_email["html"]
    check("sub_dedup includes signal A (found today, after last_digest_at)", "Сигнал A" in dedup_html)
    check("sub_dedup includes signal D (found yesterday, after last_digest_at)", "Сигнал D" in dedup_html)
    check("sub_dedup EXCLUDES signal E (found 3d ago, before its last_digest_at of 2d ago — already sent)",
          "Сигнал E" not in dedup_html)
    check("sub_dedup EXCLUDES signal C (older than cadence regardless)", "Сигнал C" not in dedup_html)

check("sub_email_all got last_digest_at set after send",
      fake_db._store["digest_subscriptions"]["sub_email_all"].get("last_digest_at") is not None)
check("sub_no_match did NOT get last_digest_at set (nothing was sent)",
      fake_db._store["digest_subscriptions"]["sub_no_match"].get("last_digest_at") is None)

print("\n=== SUMMARY ===")
if errors:
    print(f"{len(errors)} FAILURES:")
    for e in errors:
        print(" -", e)
    sys.exit(1)
else:
    print("ALL CHECKS PASSED")
