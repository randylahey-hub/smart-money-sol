"""
Solana Smart Money Wallet Monitor
Solana chain üzerinde smart money cüzdanlarını real-time izler.
3+ cüzdan 20 saniye içinde aynı tokeni alırsa alert gönderir.

Monitoring yaklaşımı: Helius Enhanced Transactions API polling
- Her POLLING_INTERVAL saniyede cüzdanların son tx'lerini kontrol et
- Enhanced TX API ile swap'ları parse et
- Filtre pipeline'dan geçir
- Alert koşullarını kontrol et
"""

import asyncio
import json
import sys
import os
import time
from datetime import datetime, timezone, timedelta
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import (
    ALERT_THRESHOLD,
    TIME_WINDOW,
    ALERT_COOLDOWN,
    MAX_MCAP,
    MIN_VOLUME_24H,
    MIN_TXNS_24H,
    MIN_BUY_VALUE_USD,
    MIN_LIQUIDITY,
    BULLISH_WINDOW,
    EXCLUDED_TOKENS,
    EXCLUDED_SYMBOLS,
    BLACKOUT_HOURS,
    BLACKOUT_EXTRA_THRESHOLD,
    POLLING_INTERVAL,
    WALLET_BATCH_SIZE,
    TX_FETCH_LIMIT,
    CHECKPOINT_FILE,
)
from scripts.solana_client import (
    get_slot,
    get_signatures_for_address,
    get_enhanced_transactions,
    get_sol_price,
)
from scripts.telegram_alert import (
    send_smart_money_alert,
    send_status_update,
    send_error_alert,
    get_token_info_dexscreener,
    format_number,
)
from scripts.tx_classifier import classify_enhanced_tx, is_valid_swap
from scripts.database import (
    init_db,
    is_db_available,
    save_alert_snapshot,
    save_trade_signal,
    is_duplicate_signal,
    save_wallet_activity,
)

# Flush için
sys.stdout.reconfigure(line_buffering=True)

# UTC+3 timezone
UTC_PLUS_3 = timezone(timedelta(hours=3))


class SolSmartMoneyMonitor:
    """Solana smart money cüzdanlarını izleyen ana sınıf."""

    def __init__(self, wallets_file: str):
        self.wallets = self._load_wallets(wallets_file)
        self.wallets_set = set(w for w in self.wallets)
        print(f"📋 {len(self.wallets)} SOL cüzdan yüklendi")

        # Token alımlarını takip et: {token_mint: [(wallet, sol_amount, mcap, timestamp), ...]}
        self.token_purchases = defaultdict(list)

        # Son alert bilgileri: {token_mint: {"time": ts, "mcap": mcap, "count": count}}
        self.last_alerts = {}

        # Son işlenen signature'lar (per wallet): {wallet: last_signature}
        self.last_signatures = self._load_checkpoints()

        # İşlenmiş signature cache (duplicate engeli)
        self.processed_signatures = set()

        # SOL fiyatı cache
        self.sol_price = 0
        self.sol_price_updated = 0

        # İstatistikler
        self.stats = {
            "cycles": 0,
            "swaps_found": 0,
            "alerts_sent": 0,
            "start_time": time.time(),
        }

    def _load_wallets(self, wallets_file: str) -> list:
        """Cüzdan listesini yükle."""
        try:
            with open(wallets_file, 'r') as f:
                data = json.load(f)

            if isinstance(data, list):
                if not data:
                    return []
                if isinstance(data[0], str):
                    return data
                elif isinstance(data[0], dict) and 'address' in data[0]:
                    return [w['address'] for w in data]

            elif isinstance(data, dict):
                if 'wallets' in data:
                    wallets = data['wallets']
                    if not wallets:
                        return []
                    if isinstance(wallets[0], str):
                        return wallets
                    elif isinstance(wallets[0], dict):
                        return [w['address'] for w in wallets]

            return []
        except Exception as e:
            print(f"❌ Cüzdan dosyası yüklenemedi: {e}")
            return []

    def _load_checkpoints(self) -> dict:
        """Son işlenen signature'ları yükle."""
        try:
            if os.path.exists(CHECKPOINT_FILE):
                with open(CHECKPOINT_FILE, 'r') as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    def _save_checkpoints(self):
        """Checkpoint'leri kaydet."""
        try:
            os.makedirs(os.path.dirname(CHECKPOINT_FILE), exist_ok=True)
            with open(CHECKPOINT_FILE, 'w') as f:
                json.dump(self.last_signatures, f)
        except Exception as e:
            print(f"⚠️ Checkpoint kayıt hatası: {e}")

    def _get_sol_price(self) -> float:
        """SOL fiyatını al (cache: 60sn)."""
        now = time.time()
        if now - self.sol_price_updated > 60:
            self.sol_price = get_sol_price()
            self.sol_price_updated = now
        return self.sol_price

    def _clean_old_purchases(self):
        """TIME_WINDOW'dan eski alımları temizle."""
        current_time = time.time()
        for token in list(self.token_purchases.keys()):
            self.token_purchases[token] = [
                p for p in self.token_purchases[token]
                if current_time - p[3] < TIME_WINDOW
            ]
            if not self.token_purchases[token]:
                del self.token_purchases[token]

    def _can_send_alert(self, token_address: str, unique_wallet_count: int = 0) -> bool:
        """Alert cooldown kontrolü."""
        if token_address not in self.last_alerts:
            return True
        last_info = self.last_alerts[token_address]
        elapsed = time.time() - last_info["time"]
        if elapsed > ALERT_COOLDOWN:
            return True
        if unique_wallet_count > last_info.get("wallet_count", 0):
            return True
        return False

    def _is_bullish_alert(self, token_address: str) -> tuple:
        """Bullish alert kontrolü. Returns: (is_bullish, alert_count, first_alert_mcap)"""
        if token_address not in self.last_alerts:
            return False, 1, 0
        last_info = self.last_alerts[token_address]
        elapsed = time.time() - last_info["time"]
        if elapsed <= BULLISH_WINDOW:
            return True, last_info["count"] + 1, last_info["mcap"]
        return False, 1, 0

    def process_swap(self, wallet: str, enhanced_tx: dict):
        """
        Bir swap transaction'ını işle.
        Filtre pipeline'dan geçir, tracking'e ekle.
        """
        try:
            signature = enhanced_tx.get("signature", "")

            # Duplicate check
            if signature in self.processed_signatures:
                return
            self.processed_signatures.add(signature)

            # Signature cache boyutunu kontrol et (memory leak engeli)
            if len(self.processed_signatures) > 10000:
                # En eski yarısını at
                self.processed_signatures = set(list(self.processed_signatures)[5000:])

            # Swap doğrulaması ve token bilgisi çıkar
            swap_info = is_valid_swap(enhanced_tx, wallet)

            if not swap_info["valid"]:
                return

            token_mint = swap_info["token_mint"]
            sol_spent = swap_info["sol_spent"]
            source = swap_info["source"]

            # === EXCLUDED TOKEN FİLTRESİ ===
            if token_mint in EXCLUDED_TOKENS:
                return

            current_time = time.time()

            # === DUPLICATE WALLET FİLTRESİ ===
            existing_wallets = [p[0] for p in self.token_purchases[token_mint]]
            if wallet in existing_wallets:
                return

            # === TOKEN BİLGİSİ AL (DexScreener) ===
            token_info = get_token_info_dexscreener(token_mint)
            token_symbol = token_info.get('symbol', 'UNKNOWN')

            # Symbol bazlı excluded token kontrolü
            if token_symbol.upper() in [s.upper() for s in EXCLUDED_SYMBOLS]:
                return

            # === LİKİDİTE FİLTRESİ ===
            liquidity = token_info.get('liquidity', 0)
            if liquidity < MIN_LIQUIDITY:
                print(f"⏭️  Skip: {token_symbol} | Likidite: ${liquidity:.0f} < ${MIN_LIQUIDITY:,}")
                return

            # === MİNİMUM ALIM DEĞERİ FİLTRESİ (Dust) ===
            sol_price = self._get_sol_price()
            buy_value_usd = sol_spent * sol_price
            if 0 < buy_value_usd < MIN_BUY_VALUE_USD:
                print(f"⏭️  Skip: {token_symbol} | Dust: ${buy_value_usd:.2f} < ${MIN_BUY_VALUE_USD}")
                return

            # === MARKET CAP FİLTRESİ ===
            current_mcap = token_info.get('mcap', 0)
            if current_mcap > MAX_MCAP:
                print(f"⏭️  Skip: {token_symbol} | MCap: ${current_mcap/1e3:.0f}K > ${MAX_MCAP/1e3:.0f}K")
                return

            # === HACİM FİLTRESİ (1. katman) ===
            volume_24h = token_info.get('volume_24h', 0)
            if volume_24h < MIN_VOLUME_24H:
                print(f"⏭️  Skip: {token_symbol} | Hacim: ${volume_24h:.0f} < ${MIN_VOLUME_24H:,}")
                return

            # === İŞLEM SAYISI FİLTRESİ (1. katman) ===
            txns_buys = token_info.get('txns_24h_buys', 0)
            txns_sells = token_info.get('txns_24h_sells', 0)
            total_txns = txns_buys + txns_sells
            if total_txns < MIN_TXNS_24H:
                print(f"⏭️  Skip: {token_symbol} | İşlem: {total_txns} < {MIN_TXNS_24H}")
                return

            # ✅ TÜM FİLTRELER GEÇTİ — Tracking'e ekle
            self.token_purchases[token_mint].append(
                (wallet, sol_spent, current_mcap, current_time)
            )
            self.stats["swaps_found"] += 1

            print(f"📥 Alım: {wallet[:8]}... → ${token_symbol} | "
                  f"{sol_spent:.2f} SOL (${buy_value_usd:.0f}) | "
                  f"MCap: {format_number(current_mcap)} | {source}")

            # === WALLET ACTIVITY KAYDI ===
            try:
                save_wallet_activity(
                    wallet_address=wallet,
                    token_address=token_mint,
                    token_symbol=token_symbol,
                    tx_signature=signature,
                    is_early=False,
                    alert_mcap=int(current_mcap)
                )
            except Exception as e:
                print(f"⚠️ Wallet activity kayıt hatası: {e}")

            # Eski alımları temizle
            self._clean_old_purchases()

            # Alert kontrolü
            self._check_and_alert(token_mint)

        except Exception as e:
            print(f"⚠️ Swap işleme hatası: {e}")

    def _check_and_alert(self, token_address: str):
        """Token için alert koşullarını kontrol et."""
        purchases = self.token_purchases.get(token_address, [])
        unique_wallets = {}
        for p in purchases:
            wallet = p[0]
            if wallet not in unique_wallets:
                unique_wallets[wallet] = p

        # === SOFT BLACKOUT ===
        tr_now = datetime.now(UTC_PLUS_3)
        current_hour = tr_now.hour
        effective_threshold = ALERT_THRESHOLD
        if current_hour in BLACKOUT_HOURS:
            effective_threshold = ALERT_THRESHOLD + BLACKOUT_EXTRA_THRESHOLD

        if len(unique_wallets) >= effective_threshold:
            if not self._can_send_alert(token_address, len(unique_wallets)):
                print(f"⏳ Alert cooldown: {token_address[:12]}...")
                return

            if current_hour in BLACKOUT_HOURS:
                print(f"🌙 Soft blackout ({current_hour:02d}:00): Eşik {ALERT_THRESHOLD}→{effective_threshold}")

            print(f"\n🚨 ALERT! {len(unique_wallets)} cüzdan aynı tokeni aldı!")

            # Token bilgisi (güncel)
            token_info = get_token_info_dexscreener(token_address)

            # === 2. KATMAN FİLTRE (alert öncesi son kontrol) ===
            volume_24h = token_info.get('volume_24h', 0)
            txns_buys = token_info.get('txns_24h_buys', 0)
            txns_sells = token_info.get('txns_24h_sells', 0)
            total_txns = txns_buys + txns_sells
            token_sym = token_info.get('symbol', 'UNKNOWN')

            is_fake = False
            fake_reason = ""

            if volume_24h < MIN_VOLUME_24H:
                is_fake = True
                fake_reason = f"Hacim: ${volume_24h:.0f} < ${MIN_VOLUME_24H:,}"

            if total_txns < MIN_TXNS_24H:
                extra = f"İşlem: {total_txns} < {MIN_TXNS_24H}"
                fake_reason = f"{fake_reason} | {extra}" if fake_reason else extra
                is_fake = True

            if is_fake:
                print(f"⚠️ FAKE ALERT ENGELLENDİ: {token_sym} | {fake_reason}")
                self.token_purchases.pop(token_address, None)
                return

            # wallet_purchases formatı
            wallet_purchases = [
                (p[0], p[1], p[2]) for p in unique_wallets.values()
            ]

            # Bullish kontrol
            current_mcap_val = token_info.get('mcap', 0)
            is_bullish, alert_count, first_alert_mcap = self._is_bullish_alert(token_address)

            if is_bullish:
                print(f"🔥 BULLISH! {token_sym} — {alert_count}. alert")

            # Alert gönder
            tr_time = datetime.now(UTC_PLUS_3)
            first_buy_time = tr_time.strftime("%H:%M:%S")
            success = send_smart_money_alert(
                token_address=token_address,
                wallet_purchases=wallet_purchases,
                first_buy_time=first_buy_time,
                token_info=token_info,
                is_bullish=is_bullish,
                alert_count=alert_count,
                first_alert_mcap=first_alert_mcap
            )

            if success:
                self.last_alerts[token_address] = {
                    "time": time.time(),
                    "mcap": first_alert_mcap if is_bullish else current_mcap_val,
                    "count": alert_count,
                    "wallet_count": len(unique_wallets)
                }
                self.stats["alerts_sent"] += 1
                print(f"✅ Alert gönderildi: {token_sym}")

                # Alert snapshot kaydet
                try:
                    save_alert_snapshot(
                        token_address=token_address,
                        token_symbol=token_sym,
                        alert_mcap=int(current_mcap_val),
                        wallet_count=len(unique_wallets),
                        wallets_involved=[p[0] for p in wallet_purchases]
                    )
                except Exception as e:
                    print(f"⚠️ Alert snapshot hatası: {e}")

                # Trade signal
                try:
                    if not is_duplicate_signal(token_address):
                        save_trade_signal(token_address, token_sym, int(current_mcap_val),
                                          "scenario_1", len(unique_wallets))
                except Exception as e:
                    print(f"⚠️ Trade signal hatası: {e}")

                # MCap timer
                try:
                    from scripts.mcap_checker import schedule_mcap_check
                    schedule_mcap_check(
                        token_address=token_address,
                        token_symbol=token_sym,
                        alert_mcap=int(current_mcap_val),
                        wallets_involved=[p[0] for p in wallet_purchases]
                    )
                except Exception as e:
                    print(f"⚠️ MCap timer hatası: {e}")
            else:
                print(f"❌ Alert gönderilemedi!")

    async def start_monitoring(self):
        """Polling ile monitoring başlat."""
        print("\n" + "=" * 60)
        print("🚀 SOL SMART MONEY MONITOR BAŞLATILIYOR")
        print("=" * 60)
        print(f"📊 İzlenen cüzdan: {len(self.wallets)}")
        print(f"⏱️  Zaman penceresi: {TIME_WINDOW}sn")
        print(f"🎯 Alert eşiği: {ALERT_THRESHOLD} cüzdan")
        print(f"💰 Max MCap: ${MAX_MCAP/1e3:.0f}K")
        print(f"📊 Min Hacim: ${MIN_VOLUME_24H:,}")
        print(f"👥 Min İşlem: {MIN_TXNS_24H}")
        print(f"💧 Min Likidite: ${MIN_LIQUIDITY:,}")
        print(f"🛡️  Min Alım: ${MIN_BUY_VALUE_USD}")
        print(f"🔥 Bullish: {BULLISH_WINDOW//60}dk")
        print(f"⏳ Cooldown: {ALERT_COOLDOWN}sn")
        print(f"🌙 Blackout: {sorted(BLACKOUT_HOURS)} → +{BLACKOUT_EXTRA_THRESHOLD}")
        print(f"⏰ Polling: {POLLING_INTERVAL}sn")
        print(f"📦 Batch: {WALLET_BATCH_SIZE} cüzdan/batch")
        print("=" * 60 + "\n")

        # SOL fiyatı al
        self.sol_price = get_sol_price()
        print(f"💰 SOL: ${self.sol_price:.2f}")

        # Slot kontrolü
        slot = get_slot()
        print(f"📦 Güncel Slot: {slot}")

        # Başlangıç bildirimi
        blackout_str = ", ".join(f"{h:02d}:00" for h in sorted(BLACKOUT_HOURS))
        send_status_update(
            f"🟢 SOL Monitor başlatıldı!\n"
            f"• {len(self.wallets)} cüzdan izleniyor\n"
            f"• Alert eşiği: {ALERT_THRESHOLD} cüzdan / {TIME_WINDOW}sn\n"
            f"• Max MCap: ${MAX_MCAP/1e3:.0f}K\n"
            f"• Min Hacim: ${MIN_VOLUME_24H:,}\n"
            f"• Swap Doğrulama: Aktif (8 DEX)\n"
            f"• 🌙 Blackout: {blackout_str}\n"
            f"• Polling: {POLLING_INTERVAL}sn\n"
            f"• SOL: ${self.sol_price:.2f}"
        )

        # Ana polling döngüsü
        await self._poll_wallets()

    async def _poll_wallets(self):
        """Ana polling döngüsü — cüzdanları batch'ler halinde tara."""
        print(f"🔄 Polling başladı (her {POLLING_INTERVAL}sn)...\n")

        while True:
            try:
                cycle_start = time.time()
                self.stats["cycles"] += 1

                # Cüzdanları batch'lere böl
                wallet_list = list(self.wallets_set)
                for i in range(0, len(wallet_list), WALLET_BATCH_SIZE):
                    batch = wallet_list[i:i + WALLET_BATCH_SIZE]
                    await self._process_wallet_batch(batch)

                # Checkpoint kaydet (her 10 cycle)
                if self.stats["cycles"] % 10 == 0:
                    self._save_checkpoints()

                # İstatistik yazdır (her 20 cycle)
                if self.stats["cycles"] % 20 == 0:
                    elapsed = time.time() - self.stats["start_time"]
                    print(f"📊 Cycle {self.stats['cycles']} | "
                          f"{self.stats['swaps_found']} swap | "
                          f"{self.stats['alerts_sent']} alert | "
                          f"Uptime: {elapsed/3600:.1f}h")

                    # Daily report kontrolü
                    try:
                        from scripts.daily_report import check_and_send_if_time
                        check_and_send_if_time()
                    except Exception as e:
                        if "No module" not in str(e):
                            print(f"⚠️ Daily report hatası: {e}")

                    # MCap checker
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

                # Polling bekleme
                cycle_time = time.time() - cycle_start
                wait = max(0.5, POLLING_INTERVAL - cycle_time)
                await asyncio.sleep(wait)

            except KeyboardInterrupt:
                print("\n⏹️ Monitor durduruldu.")
                self._save_checkpoints()
                send_status_update("🔴 SOL Monitor durduruldu.")
                break
            except Exception as e:
                print(f"⚠️ Polling hatası: {e}")
                await asyncio.sleep(10)

    async def _process_wallet_batch(self, wallets: list):
        """Bir batch cüzdanı işle. 429'da batch'i durdurur."""
        # Her cüzdanın son tx signature'larını al
        all_new_signatures = []
        rate_limited = False

        for wallet in wallets:
            try:
                until_sig = self.last_signatures.get(wallet)
                sigs = get_signatures_for_address(wallet, limit=TX_FETCH_LIMIT, until=until_sig)

                if sigs is None:
                    # 429 veya ağır hata — batch'i durdur
                    rate_limited = True
                    break

                if sigs:
                    # En yeni signature'ı checkpoint olarak kaydet
                    self.last_signatures[wallet] = sigs[0]["signature"]

                    # Başarısız tx'leri filtrele
                    valid_sigs = [s for s in sigs if s.get("err") is None]
                    for s in valid_sigs:
                        sig = s["signature"]
                        if sig not in self.processed_signatures:
                            all_new_signatures.append((wallet, sig))

            except Exception as e:
                print(f"⚠️ Signature fetch hatası ({wallet[:8]}...): {e}")

        if rate_limited:
            print(f"⏳ Batch rate limited — 5s bekleniyor...")
            await asyncio.sleep(5)
            return

        if not all_new_signatures:
            return

        # Enhanced TX batch olarak al (max 100)
        sig_list = [s[1] for s in all_new_signatures]
        wallet_map = {s[1]: s[0] for s in all_new_signatures}

        for batch_start in range(0, len(sig_list), 100):
            batch_sigs = sig_list[batch_start:batch_start + 100]
            enhanced_txs = get_enhanced_transactions(batch_sigs)

            for etx in enhanced_txs:
                sig = etx.get("signature", "")
                wallet = wallet_map.get(sig, "")
                if wallet:
                    self.process_swap(wallet, etx)


def main():
    """Ana fonksiyon."""
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    wallets_file = os.path.join(base_dir, "data", "smart_money_wallets.json")
    if not os.path.exists(wallets_file):
        print(f"❌ Cüzdan dosyası bulunamadı: {wallets_file}")
        print("data/smart_money_wallets.json dosyasını oluşturun!")
        return

    print(f"📂 Cüzdan dosyası: {wallets_file}")

    # Database başlat
    if is_db_available():
        init_db()
        print("🗄️  PostgreSQL aktif (sol_ prefix)")
    else:
        print("⚠️ DATABASE_URL yok — DB özellikleri devre dışı")

    # Monitor başlat
    monitor = SolSmartMoneyMonitor(wallets_file)

    if not monitor.wallets:
        print("❌ İzlenecek cüzdan bulunamadı!")
        return

    try:
        asyncio.run(monitor.start_monitoring())
    except KeyboardInterrupt:
        print("\n👋 Çıkış yapılıyor...")


if __name__ == "__main__":
    main()
