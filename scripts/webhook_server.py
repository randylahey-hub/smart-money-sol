"""
Webhook Server — Helius Enhanced Webhooks receiver.
Polling yerine event-driven: Helius, izlenen cüzdanlarda yeni TX olduğunda
bu endpoint'e POST atar. Kredi kullanımı %99.5 azalır.

Aynı zamanda yedek polling (5dk) çalıştırır — kaçırılan TX'ler için.
"""

import asyncio
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone, timedelta

from flask import Flask, request, jsonify

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import (
    WEBHOOK_SECRET,
    POLLING_INTERVAL,
)
from scripts.wallet_monitor import SolSmartMoneyMonitor
from scripts.database import init_db, is_db_available
from scripts.solana_client import register_webhook, get_webhook_id, save_webhook_id
from scripts.telegram_alert import send_status_update

# Flush için
sys.stdout.reconfigure(line_buffering=True)

UTC_PLUS_3 = timezone(timedelta(hours=3))

# Flask app
app = Flask(__name__)

# Global monitor instance
monitor = None


def init_monitor():
    """Monitor'u başlat."""
    global monitor
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    wallets_file = os.path.join(base_dir, "data", "smart_money_wallets.json")

    if not os.path.exists(wallets_file):
        print(f"❌ Cüzdan dosyası bulunamadı: {wallets_file}")
        return False

    print(f"📂 Cüzdan dosyası: {wallets_file}")

    # Database
    if is_db_available():
        init_db()
        print("🗄️  PostgreSQL aktif (sol_ prefix)")
    else:
        print("⚠️ DATABASE_URL yok — DB özellikleri devre dışı")

    monitor = SolSmartMoneyMonitor(wallets_file)
    if not monitor.wallets:
        print("❌ İzlenecek cüzdan bulunamadı!")
        return False

    # SOL fiyatı al
    from scripts.solana_client import get_sol_price
    monitor.sol_price = get_sol_price()
    print(f"💰 SOL: ${monitor.sol_price:.2f}")

    return True


def setup_webhook(max_retries=3):
    """
    Helius webhook'unu kaydet veya güncelle.
    429 alırsa 30/60/90sn backoff ile retry yapar.
    Akış: Helius'ta listele → URL eşleşen varsa update → yoksa yeni kayıt.
    """
    if not monitor:
        return

    public_url = os.getenv("KOYEB_PUBLIC_DOMAIN", "")
    if not public_url:
        service_name = os.getenv("KOYEB_SERVICE_NAME", "")
        app_name = os.getenv("KOYEB_APP_NAME", "")
        if service_name and app_name:
            public_url = f"{service_name}-{app_name}.koyeb.app"

    if not public_url:
        print("⚠️ Public URL bulunamadı — webhook kaydedilemedi")
        return

    webhook_url = f"https://{public_url}/webhook"
    wallet_list = list(monitor.wallets_set)

    from scripts.solana_client import list_webhooks, update_webhook

    for attempt in range(1, max_retries + 1):
        # --- Adım 1: Helius'ta mevcut webhook'u ara ---
        existing_webhooks = list_webhooks()  # [] dönerse 429 veya hata
        for wh in existing_webhooks:
            if wh.get("webhookURL") == webhook_url:
                wh_id = wh["webhookID"]
                print(f"🔎 Helius'ta mevcut webhook bulundu: {wh_id[:12]}...")
                if update_webhook(wh_id, wallet_list, webhook_url):
                    save_webhook_id(wh_id)
                    print(f"✅ Webhook güncellendi ({len(wallet_list)} cüzdan)")
                    return
                # update başarısız ama webhook var — devam et
                print(f"⚠️ Update başarısız ama webhook mevcut, push'lar gelmeye devam edecek")
                return

        # --- Adım 2: Dosyadan ID fallback ---
        existing_id = get_webhook_id()
        if existing_id:
            print(f"🔄 Dosyadan webhook ID: {existing_id[:12]}...")
            if update_webhook(existing_id, wallet_list, webhook_url):
                print(f"✅ Webhook güncellendi: {webhook_url}")
                return

        # --- Adım 3: Yeni kayıt ---
        print(f"📡 Yeni webhook kaydediliyor: {webhook_url}")
        result = register_webhook(wallet_list, webhook_url, WEBHOOK_SECRET)
        if result and "webhookID" in result:
            save_webhook_id(result["webhookID"])
            print(f"✅ Webhook registered: {result['webhookID'][:12]}...")
            return

        # --- Hiçbiri çalışmadı — 429 veya geçici hata ---
        if attempt < max_retries:
            wait = 30 * attempt
            print(f"⏳ Webhook setup başarısız — {wait}s bekleniyor (deneme {attempt}/{max_retries})")
            time.sleep(wait)
        else:
            print(f"⚠️ Webhook setup {max_retries} denemede başarısız")
            print(f"   Önceki webhook hâlâ aktif olabilir — push'lar gelmeye devam edecek")


@app.route("/health", methods=["GET"])
def health():
    """Koyeb health check."""
    wallet_count = len(monitor.wallets) if monitor else 0
    return jsonify({
        "status": "ok",
        "wallets": wallet_count,
        "stats": monitor.stats if monitor else {},
        "mode": "webhook",
    })


@app.route("/webhook", methods=["POST"])
def webhook():
    """Helius Enhanced Webhook receiver."""
    if not monitor:
        return jsonify({"error": "monitor not initialized"}), 503

    # Auth header doğrula
    if WEBHOOK_SECRET:
        auth = request.headers.get("Authorization", "")
        if auth != WEBHOOK_SECRET:
            print(f"⚠️ Webhook auth hatası: {auth[:20]}...")
            return jsonify({"error": "unauthorized"}), 401

    try:
        payload = request.get_json(force=True)
    except Exception as e:
        print(f"⚠️ Webhook JSON parse hatası: {e}")
        return jsonify({"error": "invalid json"}), 400

    # Helius webhook payload: tek TX veya liste
    if isinstance(payload, list):
        transactions = payload
    elif isinstance(payload, dict):
        # Bazen tek TX dict olarak gelir
        transactions = [payload]
    else:
        return jsonify({"error": "unexpected payload type"}), 400

    processed = 0
    for etx in transactions:
        try:
            # Hangi monitored wallet bu TX'te yer alıyor?
            wallet = _find_monitored_wallet(etx)
            if wallet:
                monitor.process_swap(wallet, etx)
                processed += 1
        except Exception as e:
            print(f"⚠️ Webhook TX işleme hatası: {e}")

    if processed > 0:
        print(f"📨 Webhook: {processed}/{len(transactions)} TX işlendi")

    return jsonify({"processed": processed}), 200


def _find_monitored_wallet(enhanced_tx: dict) -> str:
    """Enhanced TX'te hangi monitored wallet yer alıyor bulur."""
    if not monitor:
        return ""

    wallets_set = monitor.wallets_set

    # 1. feePayer kontrolü (en yaygın — cüzdan kendi TX'ini yapmış)
    fee_payer = enhanced_tx.get("feePayer", "")
    if fee_payer in wallets_set:
        return fee_payer

    # 2. tokenTransfers'da from/to kontrolü
    for tt in enhanced_tx.get("tokenTransfers", []):
        from_acc = tt.get("fromUserAccount", "")
        to_acc = tt.get("toUserAccount", "")
        if from_acc in wallets_set:
            return from_acc
        if to_acc in wallets_set:
            return to_acc

    # 3. nativeTransfers'da from/to kontrolü
    for nt in enhanced_tx.get("nativeTransfers", []):
        from_acc = nt.get("fromUserAccount", "")
        to_acc = nt.get("toUserAccount", "")
        if from_acc in wallets_set:
            return from_acc
        if to_acc in wallets_set:
            return to_acc

    # 4. accountData kontrolü (son çare)
    for ad in enhanced_tx.get("accountData", []):
        account = ad.get("account", "")
        if account in wallets_set:
            return account

    return ""


def _run_backup_polling():
    """Yedek polling — kaçırılan TX'ler için (arka plan thread)."""
    if not monitor:
        return

    print(f"🔄 Yedek polling başlatıldı (her {POLLING_INTERVAL}sn)")

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(monitor._poll_wallets())


def _run_periodic_tasks():
    """Periyodik görevler: daily report, mcap checker, stats."""
    if not monitor:
        return

    cycle = 0
    while True:
        try:
            time.sleep(60)  # Her 60 saniye
            cycle += 1

            # İstatistik (her 5 dk)
            if cycle % 5 == 0:
                elapsed = time.time() - monitor.stats["start_time"]
                print(f"📊 Stats | "
                      f"{monitor.stats['swaps_found']} swap | "
                      f"{monitor.stats['alerts_sent']} alert | "
                      f"Uptime: {elapsed/3600:.1f}h")

            # Daily report (her 20 dk kontrol)
            if cycle % 20 == 0:
                try:
                    from scripts.daily_report import check_and_send_if_time
                    check_and_send_if_time()
                except Exception as e:
                    if "No module" not in str(e):
                        print(f"⚠️ Daily report hatası: {e}")

            # MCap checker (her 5 dk)
            if cycle % 5 == 0:
                try:
                    from scripts.mcap_checker import process_pending_checks, get_pending_count
                    pending = get_pending_count()
                    if pending > 0:
                        results = process_pending_checks()
                        if results:
                            print(f"📈 MCap check: {len(results)} token kontrol edildi")
                except Exception as e:
                    if "No module" not in str(e):
                        print(f"⚠️ MCap checker hatası: {e}")

            # Checkpoint kaydet (her 10 dk)
            if cycle % 10 == 0:
                monitor._save_checkpoints()

        except Exception as e:
            print(f"⚠️ Periodic task hatası: {e}")


_started = False


def _startup():
    """Gunicorn worker başlatıldığında çalışır (bir kez)."""
    global _started
    if _started:
        return
    _started = True

    print("\n" + "=" * 60)
    print("🚀 SOL SMART MONEY WEBHOOK SERVER")
    print("=" * 60)

    if init_monitor():
        # Webhook kaydet (30sn sonra — önceki deploy'un rate limit'ini geçir)
        def delayed_webhook_setup():
            time.sleep(30)
            setup_webhook()

        webhook_thread = threading.Thread(target=delayed_webhook_setup, daemon=True)
        webhook_thread.start()

        # Backup polling devre dışı — webhook zaten real-time push yapıyor.
        # 250 cüzdan × getSignaturesForAddress = 72K çağrı/gün = 2.16M kredi/ay
        # Free tier 1M → aşılıyordu. Webhook modu bu çağrıları sıfıra indiriyor.
        # polling_thread = threading.Thread(target=_run_backup_polling, daemon=True)
        # polling_thread.start()

        # Periyodik görevler thread (stats, daily report, mcap checker)
        periodic_thread = threading.Thread(target=_run_periodic_tasks, daemon=True)
        periodic_thread.start()

        # Telegram bildirim
        wallet_count = len(monitor.wallets) if monitor else 0
        send_status_update(
            f"🟢 SOL Monitor (Webhook mode) başlatıldı!\n"
            f"• {wallet_count} cüzdan izleniyor\n"
            f"• Mode: Helius Enhanced Webhooks (pure)\n"
            f"• Backup polling: KAPALI (kredi tasarrufu)\n"
            f"• SOL: ${monitor.sol_price:.2f}"
        )

        print(f"\n✅ Webhook server hazır — port {os.getenv('PORT', '8080')}")
        print("=" * 60 + "\n")
    else:
        print("❌ Monitor başlatılamadı!")


# Gunicorn import sırasında startup'ı çalıştır
_startup()
