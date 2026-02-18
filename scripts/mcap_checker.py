"""
MCap Checker - Alert sonrası zamanlı MCap kontrolü.

Kontrol noktaları:
- 1dk: Erken fiyat hareketi (sadece ATH takibi)
- 5dk: short_list kontrolü (MCap +20%)
- 15dk: Orta vade kontrol (sadece ATH takibi)
- 30dk: contracts_check kontrolü (MCap +50%)

Her kontrol noktasında ATH MCap güncellenir.
"""

import time
import threading
from datetime import datetime, timezone, timedelta
from collections import deque

from scripts.telegram_alert import get_token_info_dexscreener
from scripts.database import save_token_evaluation
from config.settings import SHORT_LIST_THRESHOLD, CONTRACTS_CHECK_THRESHOLD, DEAD_TOKEN_MCAP

UTC_PLUS_3 = timezone(timedelta(hours=3))

_pending_checks = deque()
_lock = threading.Lock()

# Kontrol noktaları: (süre_saniye, check_type, threshold)
CHECK_POINTS = [
    (60,   "1min",  None),                     # 1dk: sadece ATH takibi
    (300,  "5min",  SHORT_LIST_THRESHOLD),      # 5dk: +20% → short_list
    (900,  "15min", None),                     # 15dk: sadece ATH takibi
    (1800, "30min", CONTRACTS_CHECK_THRESHOLD), # 30dk: +50% → contracts_check
]


def schedule_mcap_check(token_address: str, token_symbol: str, alert_mcap: int,
                         wallets_involved: list = None, alert_time: str = None):
    """Alert sonrası tüm kontrol noktalarını planla."""
    now = time.time()
    if not alert_time:
        alert_time = datetime.now(UTC_PLUS_3).isoformat()

    check_data = {
        "token_address": token_address,
        "token_symbol": token_symbol,
        "alert_mcap": alert_mcap,
        "wallets_involved": wallets_involved or [],
        "alert_time": alert_time,
    }

    with _lock:
        for delay_secs, check_type, threshold in CHECK_POINTS:
            _pending_checks.append({
                **check_data,
                "check_type": check_type,
                "check_at": now + delay_secs,
                "threshold": threshold,
            })

    check_names = ", ".join(ct for _, ct, _ in CHECK_POINTS)
    print(f"⏰ MCap check planlandı: {token_symbol} → {check_names}")


def process_pending_checks() -> list:
    """Zamanı gelen kontrolleri işle."""
    now = time.time()
    results = []
    checks_to_process = []

    with _lock:
        remaining = deque()
        while _pending_checks:
            check = _pending_checks.popleft()
            if check["check_at"] <= now:
                checks_to_process.append(check)
            else:
                remaining.append(check)
        _pending_checks.extend(remaining)

    for check in checks_to_process:
        result = _execute_check(check)
        results.append(result)

    return results


def _execute_check(check: dict) -> dict:
    """Tek bir MCap kontrolünü çalıştır + ATH MCap güncelle."""
    token_addr = check["token_address"]
    token_symbol = check["token_symbol"]
    alert_mcap = check["alert_mcap"]
    check_type = check["check_type"]
    threshold = check["threshold"]

    # DexScreener'dan güncel MCap
    token_info = get_token_info_dexscreener(token_addr)
    current_mcap = token_info.get("mcap", 0)

    # Değişim yüzdesi
    if alert_mcap > 0:
        change_pct = (current_mcap - alert_mcap) / alert_mcap
    else:
        change_pct = 0

    # Sınıflandırma (sadece threshold olan kontrol noktalarında)
    classification = None
    passed = False

    if threshold is not None:
        if current_mcap <= DEAD_TOKEN_MCAP:
            classification = "trash"
        elif change_pct >= threshold:
            classification = "short_list" if check_type == "5min" else "contracts_check"
            passed = True
        else:
            classification = "not_short_list"

    # DB'ye kaydet + ATH MCap güncelle
    save_kwargs = {
        "token_address": token_addr,
        "token_symbol": token_symbol,
        "alert_mcap": alert_mcap,
        "alert_time": check.get("alert_time"),
        "ath_mcap": int(current_mcap),  # Her kontrol noktasında ATH güncelleme denenir
    }

    if check_type == "5min":
        save_kwargs["mcap_5min"] = int(current_mcap)
        save_kwargs["change_5min_pct"] = round(change_pct * 100, 2)
        save_kwargs["wallets_involved"] = check.get("wallets_involved", [])
    elif check_type == "30min":
        save_kwargs["mcap_30min"] = int(current_mcap)
        save_kwargs["change_30min_pct"] = round(change_pct * 100, 2)

    if classification is not None:
        save_kwargs["classification"] = classification

    save_token_evaluation(**save_kwargs)

    emoji = "✅" if passed else ("📊" if threshold is None else "❌")
    print(f"{emoji} MCap Check ({check_type}): {token_symbol} | "
          f"Alert: ${alert_mcap:,.0f} → Şimdi: ${current_mcap:,.0f} ({change_pct*100:+.1f}%) "
          f"{'→ ' + classification if classification else ''}")

    return {
        "token_address": token_addr,
        "token_symbol": token_symbol,
        "check_type": check_type,
        "alert_mcap": alert_mcap,
        "current_mcap": current_mcap,
        "change_pct": round(change_pct * 100, 2),
        "classification": classification,
        "passed": passed,
    }


def get_pending_count() -> int:
    with _lock:
        return len(_pending_checks)
