"""
analytics_report.py — присылает вам на почту сводку посещаемости
дашборда: сколько уникальных людей его открывало, сколько раз и
насколько надолго они задерживались на странице.

Эти цифры НЕТ на самом дашборде — src/build_dashboard.py только тихо
пишет их в Firestore (коллекция analytics_sessions) фоновым скриптом,
без единого элемента интерфейса. Более того, эта коллекция вообще НЕ
читается с клиента ни при каких условиях — firestore_rules.txt запрещает
read всем, включая того браузера, который создал свою же запись. Увидеть
цифры можно только отсюда — этот скрипт использует служебный аккаунт
(Firebase Admin SDK), у которого есть доступ в обход правил, ровно как
send_digest.py читает digest_subscriptions.

Что считается "уникальным пользователем": анонимный Firebase-uid,
который дашборд один раз генерирует в браузере посетителя и держит,
пока тот не почистит данные сайта. Один и тот же человек в двух разных
браузерах/устройствах/режиме инкогнито считается двумя разными
"пользователями" — точного способа отличить людей без входа по паролю
не бывает, это обычная и ожидаемая точность подобной анонимной
аналитики (та же логика, что и у author_uid в комментариях).

Длительность сессии — оценка снизу: дашборд обновляет её "пульсом" раз в
30 секунд, пока вкладка открыта, плюс пытается дописать точный момент
ухода (pagehide/сворачивание вкладки), но эта последняя попытка не
гарантирована (например, если вкладку просто закрыли без сети) — тогда
в Firestore остаётся значение из последнего успешного пульса, отстающее
от реального ухода не более чем на ~30 секунд.

Отчёт считается за ВСЁ время (накопительно), а не за неделю/месяц — это
самый простой и однозначный вариант; если нужна разбивка по периодам,
это осмысленное расширение на будущее, но не в этой версии.

Запускается вручную через .github/workflows/analytics_report.yml
(Actions -> Analytics Report -> Run workflow) — там же настроено и
еженедельное расписание, если хотите получать сводку сами по себе, без
необходимости запускать вручную (можно отключить в workflow, если
письма не нужны так часто).

Требуемые переменные окружения (те же, что и у send_digest.py):
  FIREBASE_SERVICE_ACCOUNT_JSON — служебный аккаунт Firebase (см.
    send_digest.py — используется тот же самый секрет, отдельного не
    нужно).
  RESEND_API_KEY — ключ API resend.com, нужен для отправки письма.
Письмо уходит на dashboard -> owner_email из config/parameters.yaml.
Если любого из двух не хватает — скрипт не падает, а просто печатает
цифры в лог запуска (Actions -> этот прогон -> лог шага).
"""
import os
from collections import defaultdict

import yaml

from build_dashboard import CONFIG_PATH
from send_digest import init_firestore, send_email, dashboard_url_from_repo


def format_duration(seconds):
    seconds = int(round(seconds or 0))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours} ч {minutes} мин"
    if minutes:
        return f"{minutes} мин {secs} с"
    return f"{secs} с"


def median(values):
    if not values:
        return 0.0
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def build_report(db):
    """Читает всю коллекцию analytics_sessions разом — для масштаба этого
    проекта (личный/небольшой командный дашборд, не миллионы визитов)
    это нормально: даже несколько тысяч документов читаются и считаются
    в Python за доли секунды, отдельная агрегирующая коллекция была бы
    преждевременной сложностью.

    Документы с id, начинающимся на "diag_", исключены из подсчёта —
    это соглашение для ручной проверки работоспособности (например,
    "запись действительно проходит через Security Rules") прямо в
    консоли браузера на живом сайте, без запуска настоящего дашборда.
    Такую проверку нельзя "откатить" удалением (Security Rules намеренно
    запрещают delete — см. firestore_rules.txt), поэтому вместо удаления
    подобные записи просто не считаются реальными открытиями."""
    sessions = [
        doc.to_dict() or {}
        for doc in db.collection("analytics_sessions").stream()
        if not doc.id.startswith("diag_")
    ]

    by_user = defaultdict(list)
    for s in sessions:
        uid = s.get("uid") or "(без uid)"
        by_user[uid].append(s)

    durations = [float(s.get("duration_seconds") or 0) for s in sessions]
    opens_per_user = [len(v) for v in by_user.values()]

    return {
        "total_sessions": len(sessions),
        "unique_users": len(by_user),
        "avg_duration": (sum(durations) / len(durations)) if durations else 0.0,
        "median_duration": median(durations),
        "opened_once": sum(1 for n in opens_per_user if n == 1),
        "opened_2_3": sum(1 for n in opens_per_user if 2 <= n <= 3),
        "opened_4plus": sum(1 for n in opens_per_user if n >= 4),
        "max_opens_by_one_user": max(opens_per_user) if opens_per_user else 0,
    }


def print_report(report):
    print(f"Уникальных пользователей (по анонимному uid): {report['unique_users']}")
    print(f"Всего открытий дашборда: {report['total_sessions']}")
    print(f"  из них открыли 1 раз: {report['opened_once']}")
    print(f"  открыли 2-3 раза: {report['opened_2_3']}")
    print(f"  открыли 4+ раз: {report['opened_4plus']} (максимум у одного: {report['max_opens_by_one_user']})")
    print(f"Средняя длительность сессии: {format_duration(report['avg_duration'])}")
    print(f"Медианная длительность сессии: {format_duration(report['median_duration'])}")


def build_email_html(report, dashboard_url):
    dash_link = f"<p style='margin-top:20px;'><a href='{dashboard_url}'>Открыть дашборд</a></p>" if dashboard_url else ""
    rows = [
        ("Уникальных пользователей", str(report["unique_users"])),
        ("Всего открытий дашборда", str(report["total_sessions"])),
        ("— открыли 1 раз", str(report["opened_once"])),
        ("— открыли 2-3 раза", str(report["opened_2_3"])),
        ("— открыли 4+ раз", f"{report['opened_4plus']} (максимум у одного — {report['max_opens_by_one_user']})"),
        ("Средняя длительность сессии", format_duration(report["avg_duration"])),
        ("Медианная длительность сессии", format_duration(report["median_duration"])),
    ]
    table_rows = "".join(
        f"<tr><td style='padding:6px 14px 6px 0;color:#666;'>{label}</td>"
        f"<td style='padding:6px 0;font-weight:600;'>{value}</td></tr>"
        for label, value in rows
    )
    return (
        "<p>Сводка посещаемости дашборда, накопительно за всё время "
        "(не отображается на самом дашборде — эти цифры видите только вы, в этом письме):</p>"
        f"<table style='border-collapse:collapse;margin-top:10px;'>{table_rows}</table>"
        f"{dash_link}"
    )


def main():
    cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    dash_cfg = cfg.get("dashboard", {}) or {}
    owner_email = (dash_cfg.get("owner_email") or "").strip()
    from_addr = dash_cfg.get("digest_from_email") or "Точки роста — Авито.Работа <onboarding@resend.dev>"
    dashboard_url = dashboard_url_from_repo(dash_cfg.get("github_repo", ""))

    db = init_firestore()
    report = build_report(db)
    print_report(report)

    if not owner_email:
        print("dashboard -> owner_email не задан в config/parameters.yaml — письмо не отправлено, см. цифры выше в логе.")
        return

    resend_key = os.environ.get("RESEND_API_KEY")
    if not resend_key:
        print("Нет переменной окружения RESEND_API_KEY — письмо не отправлено, см. цифры выше в логе.")
        return

    html_body = build_email_html(report, dashboard_url)
    send_email(owner_email, "Аналитика дашборда — сводка посещаемости", html_body, from_addr, resend_key)
    print(f"Письмо с аналитикой отправлено на {owner_email}")


if __name__ == "__main__":
    main()
