"""Интеграционный тест реальной (не-мок) формы подписки на рассылку и
ссылки отписки — поверх фейкового Firebase (fake_firebase.js), т.к. в
песочнице нет сети до Firebase. Проверяет: запись документа в
digest_subscriptions для email- и telegram-каналов (с чекбоксами рынков/
групп/приоритета и периодом), появление ссылки на Telegram-бота после
сохранения, отказ на невалидном email, и обновление unsubscribed=true
по ссылке ?unsub=<id>."""
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent
DOCS = ROOT / "docs" / "index.html"
FAKE_FB = (ROOT / "fake_firebase.js").read_text(encoding="utf-8")
URL = f"file://{DOCS}"

errors = []


def check(label, cond):
    status = "OK" if cond else "FAIL"
    print(f"[{status}] {label}")
    if not cond:
        errors.append(label)


with sync_playwright() as p:
    browser = p.chromium.launch(executable_path="/opt/pw-browsers/chromium-1194/chrome-linux/chrome")

    # ---- Сценарий 1: подписка по email ----
    page = browser.new_page()
    page.add_init_script(FAKE_FB)
    page.on("pageerror", lambda exc: errors.append(f"pageerror(email): {exc}"))
    page.goto(URL)
    page.wait_for_timeout(400)

    page.locator("#subscribeBtn").click()
    check("subscribe overlay opens", page.locator("#subscribeOverlay").is_visible())

    # Невалидный email должен блокировать запись
    page.locator("#subEmail").fill("не-email")
    page.locator("#subSave").click()
    page.wait_for_timeout(150)
    check("invalid email rejected (status message)", "корректный email" in page.locator("#subStatus").inner_text())

    # Снимаем пару чекбоксов рынков/групп, оставляем только жёлтый приоритет
    page.locator(".subMarketChk").first.uncheck()
    page.locator(".subGroupChk").first.uncheck()
    page.locator('input.subLevelChk[value="green"]').uncheck()
    page.locator('input.subLevelChk[value="red"]').uncheck()
    page.locator('#subRecency .tab[data-days="7"]').click()

    page.locator("#subEmail").fill("bulat@example.com")
    page.locator("#subSave").click()
    page.wait_for_timeout(250)

    status_text = page.locator("#subStatus").inner_text()
    check("email subscribe success message shown", "Готово" in status_text)

    # fake_firebase.js держит данные в замыкании (переменная store), а не на
    # window — читаем через реальный API коллекции, у которой есть onSnapshot.
    snap = page.evaluate("""
        () => new Promise(resolve => {
            let unsub;
            unsub = firebase.firestore().collection('digest_subscriptions').onSnapshot(qs => {
                const out = [];
                qs.forEach(d => out.push(Object.assign({id: d.id}, d.data())));
                setTimeout(() => unsub(), 0);
                resolve(out);
            });
        })
    """)
    check("exactly one subscription doc created (invalid attempt did not write)", len(snap) == 1)
    if snap:
        doc = snap[0]
        check("channel is email", doc.get("channel") == "email")
        check("email stored correctly", doc.get("email") == "bulat@example.com")
        check("one market unchecked -> markets list missing it", len(doc.get("markets", [])) >= 1)
        check("colors only contains yellow", doc.get("colors") == ["yellow"])
        check("recency_days is 7", doc.get("recency_days") == 7)
        check("created_at is a server-timestamp marker", isinstance(doc.get("created_at"), dict) and doc["created_at"].get("__isServerTimestamp") is True)

    page.screenshot(path="test13_email_screenshot.png", full_page=True)
    page.close()

    # ---- Сценарий 2: подписка по Telegram (проверяем deep-link) ----
    page2 = browser.new_page()
    page2.add_init_script(FAKE_FB)
    page2.on("pageerror", lambda exc: errors.append(f"pageerror(telegram): {exc}"))
    # Не открываем реально новую вкладку по window.open() — просто проверим,
    # что она была вызвана (это единственный способ узнать про Start).
    opened_urls = []
    page2.expose_binding("__recordOpen", lambda source, url: opened_urls.append(url))
    page2.add_init_script("window.open = (url) => { window.__recordOpen(url); return null; };")
    page2.goto(URL)
    page2.wait_for_timeout(400)

    page2.locator("#subscribeBtn").click()
    page2.locator('#subChannelTabs .tab[data-channel="telegram"]').click()
    check("telegram block visible after tab click", page2.locator("#subTelegramBlock").is_visible())
    check("email block hidden", not page2.locator("#subEmailBlock").is_visible())
    check("connect area hidden before save", not page2.locator("#subTelegramConnectArea").is_visible())

    page2.locator("#subSave").click()
    page2.wait_for_timeout(250)

    check("connect area revealed after save", page2.locator("#subTelegramConnectArea").is_visible())
    href = page2.locator("#subTelegramLink").get_attribute("href")
    check(f"telegram link has correct shape (got {href!r})", bool(href) and href.startswith("https://t.me/") and "?start=" in href)

    snap2 = page2.evaluate("""
        () => new Promise(resolve => {
            let unsub;
            unsub = firebase.firestore().collection('digest_subscriptions').onSnapshot(qs => {
                const out = [];
                qs.forEach(d => out.push(Object.assign({id: d.id}, d.data())));
                setTimeout(() => unsub(), 0);
                resolve(out);
            });
        })
    """)
    check("telegram subscription doc created", len(snap2) == 1)
    if snap2 and href:
        tg_doc = snap2[0]
        check("telegram doc has no telegram_chat_id yet (unconfirmed)", "telegram_chat_id" not in tg_doc)
        check("deep link start param matches the new doc id", href.endswith("?start=" + tg_doc["id"]))
    check("window.open was called with the same link (auto-open Start)", opened_urls == [href] if href else False)

    page2.screenshot(path="test13_telegram_screenshot.png", full_page=True)
    page2.close()

    # ---- Сценарий 3: отписка по ?unsub=<id> ----
    page3 = browser.new_page()
    page3.add_init_script(FAKE_FB)
    # Заранее "засеваем" существующую подписку прямо через fake Firestore API,
    # до того как дашборд успеет вызвать checkUnsubscribeLink() при загрузке.
    page3.add_init_script("""
        window.__seedReady = new Promise(resolve => {
            const wait = () => {
                if (window.firebase && window.firebase.__fake) {
                    firebase.firestore().collection('digest_subscriptions').doc('existing_sub').set({
                        channel: 'email', email: 'old@example.com', markets: [], groups: [], colors: [],
                    }).then(resolve);
                } else {
                    setTimeout(wait, 5);
                }
            };
            wait();
        });
    """)
    page3.on("pageerror", lambda exc: errors.append(f"pageerror(unsub): {exc}"))
    dialogs = []
    page3.on("dialog", lambda d: (dialogs.append(d.message), d.accept()))
    page3.goto(URL + "?unsub=existing_sub")
    page3.evaluate("() => window.__seedReady")
    page3.wait_for_timeout(50)
    # checkUnsubscribeLink() уже выполнилась при исходной загрузке скриптов
    # ДО того, как сработал наш seed (гонка) — принудительно вызываем ещё раз,
    # это ровно то, что произошло бы при реальном переходе по ссылке после
    # существования документа.
    page3.evaluate("() => checkUnsubscribeLink()")
    page3.wait_for_timeout(200)

    check("confirmation dialog shown", any("отписаны" in m for m in dialogs))
    snap3 = page3.evaluate("""
        () => new Promise(resolve => {
            let unsub;
            unsub = firebase.firestore().collection('digest_subscriptions').doc('existing_sub').onSnapshot(d => {
                setTimeout(() => unsub(), 0);
                resolve(d.data());
            });
        })
    """)
    check("unsubscribed field set to true", snap3.get("unsubscribed") is True)
    check("other fields untouched", snap3.get("email") == "old@example.com" and snap3.get("channel") == "email")

    page3.close()
    browser.close()

print("\n=== SUMMARY ===")
if errors:
    print(f"{len(errors)} FAILURES:")
    for e in errors:
        print(" -", e)
else:
    print("ALL CHECKS PASSED")
