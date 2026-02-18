"""
Daily Report - SOL Smart Money
Her gün 00:00 UTC+3'te günlük kapanış raporu gönderir.

Format:
- Bir önceki günün tüm alertleri token bazlı listelenir
- Her token: alert MCap → ATH MCap (peak %) | güncel MCap (hold %)
- ATH bazlı W/L: token en az bir kere yükseldiyse W
- Trash call oranı (classification bazlı)
- Toplam W/L sayısıyla biter
"""

import sys
import os
import time
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.telegram_alert import send_telegram_message, get_token_info_dexscreener
from scripts.database import is_db_available, get_alerts_by_date_range, cleanup_old_data

REPORT_HOUR = 0
REPORT_MINUTE = 0
UTC_PLUS_3 = timezone(timedelta(hours=3))

_last_report_date = None


def _format_mcap(mcap: float) -> str:
    if mcap >= 1_000_000:
        return f"${mcap / 1_000_000:.1f}M"
    elif mcap >= 1_000:
        return f"${mcap / 1_000:.0f}K"
    elif mcap > 0:
        return f"${mcap:.0f}"
    return "$0"


def _fetch_current_mcap(token_address: str) -> float:
    """DexScreener'dan token'in güncel MCap bilgisini al."""
    try:
        token_info = get_token_info_dexscreener(token_address)
        return token_info.get("mcap", 0)
    except Exception as e:
        print(f"⚠️ MCap fetch hatası ({token_address[:10]}...): {e}")
        return 0


def _get_yesterday_alerts() -> list:
    """
    Dünün alertlerini DB'den çek (UTC+3 00:00 - 23:59).
    LATERAL JOIN ile classification ve ath_mcap bilgisini alır.
    """
    if not is_db_available():
        return []
    now_tr = datetime.now(UTC_PLUS_3)
    yesterday_tr = now_tr - timedelta(days=1)
    start_tr = yesterday_tr.replace(hour=0, minute=0, second=0, microsecond=0)
    end_tr = now_tr.replace(hour=0, minute=0, second=0, microsecond=0)
    start_utc = (start_tr - timedelta(hours=3)).isoformat()
    end_utc = (end_tr - timedelta(hours=3)).isoformat()
    return get_alerts_by_date_range(start_utc, end_utc)


def _build_token_summary(alerts: list) -> list:
    """
    Alert listesinden token bazlı özet oluştur.
    Aynı token birden fazla alert almışsa ilk alert_mcap kullanılır.
    DexScreener'dan güncel MCap çekilir.
    """
    # Token bazında grupla (ilk alert_mcap'i tut, classification al, max ath_mcap)
    token_map = {}
    for alert in alerts:
        addr = alert["token_address"]
        if addr not in token_map:
            token_map[addr] = {
                "token_address": addr,
                "token_symbol": alert["token_symbol"] or "???",
                "alert_mcap": alert["alert_mcap"],
                "alert_count": 1,
                "wallet_count": alert.get("wallet_count", 0),
                "classification": alert.get("classification", "unknown"),
                "ath_mcap": alert.get("ath_mcap", 0),
            }
        else:
            token_map[addr]["alert_count"] += 1
            # İlk alert'te classification unknown ama sonraki doluysa güncelle
            if token_map[addr]["classification"] == "unknown":
                token_map[addr]["classification"] = alert.get("classification", "unknown")
            # En yüksek ATH MCap'i tut
            new_ath = alert.get("ath_mcap", 0)
            if new_ath > token_map[addr]["ath_mcap"]:
                token_map[addr]["ath_mcap"] = new_ath

    # Her token için güncel MCap çek
    results = []
    for addr, info in token_map.items():
        time.sleep(0.3)  # DexScreener rate limit

        current_mcap = _fetch_current_mcap(addr)
        alert_mcap = info["alert_mcap"]
        ath_mcap = info.get("ath_mcap", 0)

        # ATH MCap: DB'den gelen vs şu anki MCap — hangisi büyükse
        if current_mcap > ath_mcap:
            ath_mcap = current_mcap

        # alert_mcap=0 ise W/L hesaplanamaz
        has_alert_mcap = alert_mcap > 0

        # HOLD senaryosu: alert → şu an
        if has_alert_mcap:
            change_pct = ((current_mcap - alert_mcap) / alert_mcap) * 100
        else:
            change_pct = None

        # ATH senaryosu: alert → max MCap (ideal sell)
        if has_alert_mcap and ath_mcap > 0:
            ath_change_pct = ((ath_mcap - alert_mcap) / alert_mcap) * 100
        else:
            ath_change_pct = None

        # W/L: ATH bazlı (token en az bir kere yükseldiyse W)
        if ath_change_pct is not None:
            is_win = ath_change_pct > 0
        elif change_pct is not None:
            is_win = change_pct > 0
        else:
            is_win = False

        results.append({
            "token_symbol": info["token_symbol"],
            "token_address": addr,
            "alert_mcap": alert_mcap,
            "current_mcap": current_mcap,
            "ath_mcap": ath_mcap,
            "change_pct": round(change_pct, 1) if change_pct is not None else None,
            "ath_change_pct": round(ath_change_pct, 1) if ath_change_pct is not None else None,
            "is_win": is_win,
            "alert_count": info["alert_count"],
            "classification": info["classification"],
            "has_alert_mcap": has_alert_mcap,
        })

    # ATH değişim yüzdesine göre sırala (en iyi → en kötü, None'lar sona)
    results.sort(key=lambda x: x["ath_change_pct"] if x["ath_change_pct"] is not None else -9999, reverse=True)
    return results


def generate_daily_report() -> str:
    """Günlük kapanış raporu oluştur."""
    now = datetime.now(UTC_PLUS_3)
    yesterday = now - timedelta(days=1)
    date_str = yesterday.strftime('%d.%m.%Y')

    alerts = _get_yesterday_alerts()
    if not alerts:
        return (f"📊 <b>SOL GÜNLÜK KAPANIŞ</b> — {date_str}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n\nDün alert gönderilmedi.")

    token_summary = _build_token_summary(alerts)

    # Toplam alert sayısı (snapshot bazlı)
    total_alerts = len(alerts)

    # W/L sayıları (sadece alert_mcap verisi olan tokenlar)
    tokens_with_data = [t for t in token_summary if t["has_alert_mcap"]]
    tokens_no_data = [t for t in token_summary if not t["has_alert_mcap"]]
    wins = sum(1 for t in tokens_with_data if t["is_win"])
    losses = sum(1 for t in tokens_with_data if not t["is_win"])
    total_tokens = len(token_summary)

    # Trash call hesabı (classification bazlı)
    trash_count = sum(1 for t in token_summary if t["classification"] in ("not_short_list", "trash", "dead"))
    success_count = sum(1 for t in token_summary if t["classification"] in ("short_list", "contracts_check"))
    unknown_count = sum(1 for t in token_summary if t["classification"] in ("unknown",))

    lines = [
        f"📊 <b>SOL GÜNLÜK KAPANIŞ</b> — {date_str}",
        f"━━━━━━━━━━━━━━━━━━━━",
        f"",
    ]

    # Token listesi — ATH (ideal sell) ve Current (hold) göster
    for t in token_summary:
        symbol = t["token_symbol"]
        alert_mcap_str = _format_mcap(t["alert_mcap"])
        current_mcap_str = _format_mcap(t["current_mcap"])
        ath_mcap_str = _format_mcap(t["ath_mcap"])

        if t["has_alert_mcap"] and t["ath_change_pct"] is not None:
            emoji = "🟢" if t["is_win"] else "🔴"
            wl = "W" if t["is_win"] else "L"

            # ATH satır (ana gösterge)
            ath_str = f"+{t['ath_change_pct']:.0f}%" if t["ath_change_pct"] >= 0 else f"{t['ath_change_pct']:.0f}%"

            # Current satır (hold durumu)
            if t["change_pct"] is not None:
                cur_str = f"+{t['change_pct']:.0f}%" if t["change_pct"] >= 0 else f"{t['change_pct']:.0f}%"
            else:
                cur_str = "?"

            line = f"{emoji} <b>{symbol}</b> | {alert_mcap_str} → 🔝{ath_mcap_str} ({ath_str}) 📍{current_mcap_str} ({cur_str}) <b>{wl}</b>"
        elif t["has_alert_mcap"] and t["change_pct"] is not None:
            emoji = "🟢" if t["change_pct"] > 0 else "🔴"
            wl = "W" if t["change_pct"] > 0 else "L"
            change_str = f"+{t['change_pct']:.0f}%" if t["change_pct"] >= 0 else f"{t['change_pct']:.0f}%"
            line = f"{emoji} <b>{symbol}</b> | {alert_mcap_str} → {current_mcap_str} ({change_str}) <b>{wl}</b>"
        else:
            line = f"⚪ <b>{symbol}</b> | ? → {current_mcap_str} (veri yok)"
        lines.append(line)

    # Separator
    lines.append("")
    lines.append("━━━━━━━━━━━━━━━━━━━━")

    # W/L özet (ATH bazlı — token yükseldiyse W)
    if tokens_with_data:
        win_rate = (wins / len(tokens_with_data) * 100) if tokens_with_data else 0
        lines.append(f"📈 <b>{wins}W</b> / <b>{losses}L</b> — {len(tokens_with_data)} token ({win_rate:.0f}% ATH başarı)")
    if tokens_no_data:
        lines.append(f"⚪ {len(tokens_no_data)} token MCap verisi eksik")

    # Trash call oranı
    if trash_count > 0 or success_count > 0:
        total_classified = trash_count + success_count
        trash_pct = (trash_count / total_classified * 100) if total_classified > 0 else 0
        lines.append(f"🗑️ Trash: {trash_count}/{total_classified} ({trash_pct:.0f}%) | ✅ Başarılı: {success_count}")
        if unknown_count > 0:
            lines.append(f"❓ Henüz değerlendirilmemiş: {unknown_count}")
    elif unknown_count > 0:
        lines.append(f"❓ {unknown_count} token henüz değerlendirilmemiş")

    # Toplam alert sayısı
    lines.append(f"📡 Toplam alert: {total_alerts} ({total_tokens} farklı token)")

    # Cüzdan durumu
    try:
        import json
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        wallets_file = os.path.join(base_dir, "data", "smart_money_wallets.json")
        with open(wallets_file, 'r') as f:
            wallet_data = json.load(f)
        total_wallets = len(wallet_data.get("wallets", []))
        lines.append(f"👛 <b>Cüzdan:</b> {total_wallets} izleniyor")
    except Exception:
        pass

    return "\n".join(lines)


def send_daily_report() -> bool:
    """Günlük raporu Telegram'a gönder."""
    global _last_report_date
    print("\n📤 SOL Günlük kapanış raporu gönderiliyor...")

    report = generate_daily_report()
    success = send_telegram_message(report)

    if success:
        print("✅ SOL Günlük rapor gönderildi!")
        _last_report_date = datetime.now(UTC_PLUS_3).date()
    else:
        print("❌ Rapor gönderilemedi!")

    # Veri temizleme
    try:
        cleanup_old_data()
    except Exception as e:
        print(f"⚠️ Cleanup hatası: {e}")

    return success


def _check_reminders():
    """Planlı hatırlatmaları kontrol et ve gönder."""
    now = datetime.now(UTC_PLUS_3)
    reminders = [
        {
            "date": "2026-02-23",
            "message": (
                "🔔 <b>HATIRLATMA: SOL Blackout Analizi</b>\n\n"
                "5 günlük veri toplandı. Şimdi yapılması gerekenler:\n"
                "1. sol_alert_snapshots tablosundaki alert saatlerini analiz et\n"
                "2. Hangi saatlerde fake/trash alert oranı yüksek?\n"
                "3. Solana'ya özel blackout saatleri belirle\n"
                "4. config/settings.py → BLACKOUT_HOURS güncelle\n\n"
                "📂 Base referans: 2,4,16,20,21 (UTC+3)"
            ),
        },
    ]
    for r in reminders:
        if now.strftime("%Y-%m-%d") == r["date"]:
            try:
                send_telegram_message(r["message"])
                print(f"🔔 Hatırlatma gönderildi: {r['date']}")
            except Exception as e:
                print(f"⚠️ Hatırlatma hatası: {e}")


def check_and_send_if_time():
    """Rapor zamanı geldi mi kontrol et (00:00-00:05 UTC+3)."""
    global _last_report_date
    now = datetime.now(UTC_PLUS_3)

    if now.hour == REPORT_HOUR and REPORT_MINUTE <= now.minute < REPORT_MINUTE + 5:
        if _last_report_date == now.date():
            return False
        send_daily_report()
        _check_reminders()
        return True
    return False
