"""
Telegram Alert Modülü - Solana Smart Money
"""

import requests
import sys
import os
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID


def send_telegram_message(message: str, parse_mode: str = "HTML") -> bool:
    """Telegram grubuna mesaj gönderir."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": parse_mode,
        "disable_web_page_preview": False
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        return True
    except Exception as e:
        print(f"Telegram mesaj hatası: {e}")
        return False


def format_number(num: float) -> str:
    """Sayıyı okunabilir formata çevir."""
    if num >= 1_000_000:
        return f"${num/1_000_000:.2f}M"
    elif num >= 1_000:
        return f"${num/1_000:.1f}K"
    else:
        return f"${num:.2f}"


def get_token_info_dexscreener(token_address: str) -> dict:
    """DexScreener API'den Solana token bilgisi al."""
    try:
        url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
        response = requests.get(url, timeout=10)
        data = response.json()

        if data.get('pairs') and len(data['pairs']) > 0:
            # Solana pair'ini seç (en yüksek likidite)
            solana_pairs = [p for p in data['pairs'] if p.get('chainId') == 'solana']
            if not solana_pairs:
                solana_pairs = data['pairs']

            pair = max(solana_pairs, key=lambda x: float(x.get('liquidity', {}).get('usd', 0) or 0))
            return {
                'symbol': pair.get('baseToken', {}).get('symbol', 'UNKNOWN'),
                'name': pair.get('baseToken', {}).get('name', 'Unknown Token'),
                'mcap': float(pair.get('marketCap', 0) or pair.get('fdv', 0) or 0),
                'price': float(pair.get('priceUsd', 0) or 0),
                'liquidity': float(pair.get('liquidity', {}).get('usd', 0) or 0),
                'price_change_24h': float(pair.get('priceChange', {}).get('h24', 0) or 0),
                'volume_24h': float(pair.get('volume', {}).get('h24', 0) or 0),
                'txns_24h_buys': int(pair.get('txns', {}).get('h24', {}).get('buys', 0) or 0),
                'txns_24h_sells': int(pair.get('txns', {}).get('h24', {}).get('sells', 0) or 0),
                'pair_address': pair.get('pairAddress', ''),
                'dex_id': pair.get('dexId', ''),
            }
    except Exception as e:
        print(f"DEXScreener API hatası: {e}")

    return {
        'symbol': 'UNKNOWN', 'name': 'Unknown Token',
        'mcap': 0, 'price': 0, 'liquidity': 0,
        'price_change_24h': 0, 'volume_24h': 0,
        'txns_24h_buys': 0, 'txns_24h_sells': 0,
        'pair_address': '', 'dex_id': '',
    }


def send_smart_money_alert(
    token_address: str,
    wallet_purchases: list,  # [(wallet_address, sol_amount, buy_mcap), ...]
    first_buy_time: str,
    current_mcap: float = None,
    token_info: dict = None,
    is_bullish: bool = False,
    alert_count: int = 1,
    first_alert_mcap: float = 0
) -> bool:
    """SOL Smart Money alert mesajı gönderir."""

    if token_info is None:
        token_info = get_token_info_dexscreener(token_address)

    if current_mcap is None:
        current_mcap = token_info.get('mcap', 0)

    wallet_count = len(wallet_purchases)
    token_symbol = token_info.get('symbol', 'UNKNOWN')
    token_name = token_info.get('name', 'Unknown Token')
    liquidity = token_info.get('liquidity', 0)
    price_change = token_info.get('price_change_24h', 0)
    volume_24h = token_info.get('volume_24h', 0)
    dex_id = token_info.get('dex_id', '')

    # Toplam SOL alım tutarı
    total_sol = sum([p[1] for p in wallet_purchases if len(p) > 1])

    # Cüzdan listesi
    wallet_lines = []
    for i, purchase in enumerate(wallet_purchases[:5]):
        wallet = purchase[0]
        sol_amount = purchase[1] if len(purchase) > 1 else 0
        buy_mcap = purchase[2] if len(purchase) > 2 else 0

        line = f"  \u2022 <code>{wallet[:6]}...{wallet[-4:]}</code>"
        details = []
        if sol_amount > 0:
            details.append(f"<b>{sol_amount:.2f} SOL</b>")
        if buy_mcap > 0:
            details.append(f"MCap: {format_number(buy_mcap)}")
        if details:
            line += f" | {' | '.join(details)}"
        wallet_lines.append(line)

    wallet_list = "\n".join(wallet_lines)
    if wallet_count > 5:
        wallet_list += f"\n  \u2022 ... ve {wallet_count - 5} cüzdan daha"

    # MCap bilgisi
    mcap_info = ""
    if current_mcap > 0:
        mcap_info = f"\n\ud83d\udcb0 <b>MCap:</b> {format_number(current_mcap)}"
        if liquidity > 0:
            mcap_info += f"\n\ud83d\udca7 <b>Likidite:</b> {format_number(liquidity)}"
        if volume_24h > 0:
            mcap_info += f"\n\ud83d\udcca <b>24s Hacim:</b> {format_number(volume_24h)}"
        if price_change != 0:
            emoji = "\ud83d\udcc8" if price_change > 0 else "\ud83d\udcc9"
            mcap_info += f"\n{emoji} <b>24s De\u011fi\u015fim:</b> {price_change:+.1f}%"

    # DEX bilgisi
    dex_str = dex_id.upper() if dex_id else "?"

    # Bullish header
    if is_bullish and first_alert_mcap > 0:
        mcap_change_pct = ((current_mcap - first_alert_mcap) / first_alert_mcap * 100) if first_alert_mcap > 0 else 0
        header = f"""\ud83d\udd25\ud83d\udd25 <b>BULLISH ALERT!</b> \ud83d\udd25\ud83d\udd25

\ud83d\udd01 <b>{alert_count}. alert</b> (30dk i\u00e7inde)
\ud83d\udcc8 <b>\u0130lk alert MCap:</b> {format_number(first_alert_mcap)} \u2192 \u015eimdi: {format_number(current_mcap)} ({mcap_change_pct:+.0f}%)
"""
    elif is_bullish:
        header = f"""\ud83d\udd25\ud83d\udd25 <b>BULLISH ALERT!</b> \ud83d\udd25\ud83d\udd25

\ud83d\udd01 <b>{alert_count}. alert</b> (30dk i\u00e7inde)
"""
    else:
        header = "\ud83d\udea8 <b>SOL SMART MONEY ALERT!</b> \ud83d\udea8"

    message = f"""
{header}

\ud83d\udcca <b>Token:</b> ${token_symbol}
\ud83d\udcdb <b>Ad:</b> {token_name}
\ud83d\udccd <b>Contract:</b>
<code>{token_address}</code>
{mcap_info}

\ud83d\udc5b <b>Al\u0131m Yapan C\u00fczdanlar ({wallet_count}):</b>
{wallet_list}

\ud83d\udcb5 <b>Toplam Al\u0131m:</b> {total_sol:.2f} SOL
\ud83d\udcb1 <b>DEX:</b> {dex_str}
\u23f0 <b>Tespit Zaman\u0131:</b> {first_buy_time}

\ud83d\udd17 <b>Linkler:</b>
\u2022 <a href="https://dexscreener.com/solana/{token_address}">DEXScreener</a>
\u2022 <a href="https://solscan.io/token/{token_address}">Solscan</a>
\u2022 <a href="https://birdeye.so/token/{token_address}?chain=solana">Birdeye</a>

\u26a1\ufe0f <b>{wallet_count} smart money c\u00fczdanı 20 saniye i\u00e7inde aynı tokeni aldı!</b>
"""

    return send_telegram_message(message.strip())


def send_status_update(status: str) -> bool:
    """Durum güncellemesi gönderir."""
    message = f"\u2139\ufe0f <b>SOL Durum:</b> {status}"
    return send_telegram_message(message)


def send_error_alert(error: str) -> bool:
    """Hata bildirimi gönderir."""
    message = f"\u26a0\ufe0f <b>SOL Hata:</b>\n<code>{error}</code>"
    return send_telegram_message(message)


# Test
if __name__ == "__main__":
    import sys as _sys
    print("Telegram Alert Test (SOL)")
    print("=" * 50)

    # Basit bağlantı testi
    success = send_status_update("Test mesajı - SOL Smart Money Bot")
    print(f"Bağlantı testi: {'✅' if success else '❌'}")

    # Mock alert testi (--mock-alert flag ile)
    if "--mock-alert" in _sys.argv:
        print("\n📨 Mock alert gönderiliyor...")
        mock_wallets = [
            ("7xKXtg2nGy3uFvZqjTCp4yCsBf1K3aKjWVv4BNMuJR1g", 15.5, 450000),
            ("3vQB7z8mPwFRm1xXq7dGHyDHZPdrTb6CpBnFHSEftSHU", 22.0, 435000),
            ("9fRTkp4dXe6iLxMqDrJJq3SY1GhBEPaWCw9vC5YkLXrz", 10.1, 440000),
        ]
        mock_info = {
            "symbol": "MOCKTEST",
            "name": "Mock Test Token",
            "mcap": 450000,
            "price": 0.00045,
            "liquidity": 80000,
            "volume_24h": 120000,
            "price_change_24h": 35.0,
            "txns_24h_buys": 50,
            "txns_24h_sells": 30,
            "pair_address": "test",
            "dex_id": "raydium",
        }
        alert_ok = send_smart_money_alert(
            token_address="DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
            wallet_purchases=mock_wallets,
            first_buy_time="14:35:22",
            token_info=mock_info,
        )
        print(f"Mock alert: {'✅' if alert_ok else '❌'}")
