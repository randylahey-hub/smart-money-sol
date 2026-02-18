# Smart Money SOL Bot — Mimari Plan

## Proje Özeti
Solana chain üzerinde smart money cüzdanlarını real-time izleyen, 3+ cüzdan aynı tokeni 20sn içinde alınca Telegram'a alert gönderen bot.

---

## 🏗️ MİMARİ KARARLAR

### 1. Monitoring Yaklaşımı: Helius Enhanced WebSocket + Polling Hybrid
**Neden?**
- Solana'da Base gibi `eth.get_logs()` yok — farklı yaklaşım gerekiyor
- **Ana yöntem**: Helius WebSocket (`accountSubscribe`) ile smart money cüzdanlarının token account'larını dinle
- **Yedek yöntem**: Helius Enhanced Transactions API ile polling (WebSocket düşerse)
- **Alternatif**: Helius Webhooks (HTTP POST callback) — en güvenilir ama setup gerektirir

**Seçilen yaklaşım**: Helius Webhooks + Enhanced Transactions API polling hybrid
- Webhook: Real-time bildirim (Helius sunucu tarafında izler, bize POST atar)
- Polling fallback: Her 3sn yeni transaction'ları kontrol et
- Neden webhook tercih?: Koyeb Nano instance'da 200+ cüzdanı WebSocket ile dinlemek memory-intensive olabilir. Webhook'ta Helius izliyor, bize sadece relevant tx geliyor.

> **KARAR GEREKLİ**: Helius ücretsiz plan webhook destekliyor mu? Yoksa polling-only mı gideceğiz?
> **GÜNCELLEME**: İlk fazda POLLING ile başlıyoruz (basit, güvenilir). Webhook/WebSocket optimizasyonu sonra eklenir.

### 2. Swap Doğrulaması (Airdrop Filtresi)
Solana'da swap = bilinen DEX program ID'lerinin instruction'da bulunması

**İzlenecek DEX Program ID'leri:**
| DEX | Program ID | Açıklama |
|-----|-----------|----------|
| Raydium AMM V4 | `675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSdgbctX` | Ana AMM |
| Raydium CLMM | `CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK` | Concentrated Liquidity |
| Raydium CPMM | `CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C` | Constant Product |
| Jupiter V6 | `JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4` | Aggregator |
| Pump.fun | `6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P` | Bonding Curve |
| PumpSwap | `pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA` | Pump AMM |
| Orca Whirlpool | `whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc` | CLMM |
| Meteora DLMM | `LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo` | Dynamic LMM |

**Doğrulama**: Transaction'ın instruction'larında bu program ID'lerden biri varsa = gerçek swap.

### 3. DexScreener API — Aynı Endpoint
DexScreener API chain-agnostic çalışıyor:
- Base: `https://api.dexscreener.com/token-pairs/v1/base/{address}`
- Solana: `https://api.dexscreener.com/token-pairs/v1/solana/{address}`
- Aynı response yapısı, sadece `chainId` değişiyor ✅

### 4. Database — Aynı Neon PostgreSQL, `sol_` prefix
Mevcut Base DB'ye ek tablolar:
- `sol_alert_snapshots`
- `sol_token_evaluations`
- `sol_wallet_activity`
- `sol_trade_signals`
- `sol_smartest_wallets`
- `sol_fake_alerts`

### 5. Cüzdan Listesi Toplama
- İlk etapta: Manuel liste (Solana smart money tracker'lardan, Arkham, Nansen verileri)
- Sonra: Wallet discoverer modülü (Base'deki gibi) ile otomatik keşif
- Hedef: 100-200 cüzdan ile başla

---

## 📁 PROJE YAPISI

```
smart-money-sol/
├── config/
│   ├── __init__.py
│   └── settings.py              # Tüm konfigürasyon & env variables
├── scripts/
│   ├── wallet_monitor.py        # Ana giriş noktası — polling loop
│   ├── solana_client.py         # Helius API wrapper (RPC + Enhanced TX)
│   ├── telegram_alert.py        # Alert formatlama & Telegram gönderim
│   ├── database.py              # PostgreSQL (sol_ prefix tablolar)
│   ├── tx_classifier.py         # Swap doğrulama & airdrop filtresi
│   ├── mcap_checker.py          # 5dk/30dk MCap timer
│   ├── daily_report.py          # Günlük kapanış raporu
│   ├── wallet_scorer.py         # Smartest wallet skorlama (Faz 3)
│   ├── wallet_evaluator.py      # Cüzdan değerlendirme (Faz 3)
│   ├── wallet_discoverer.py     # Yeni cüzdan keşfi (Faz 3)
│   ├── self_improving_engine.py # 9-faz kalite sistemi (Faz 3)
│   ├── alert_analyzer.py        # Alert analizi (Faz 3)
│   └── data_cleanup.py          # 30 gün veri temizliği
├── data/
│   ├── smart_money_wallets.json # SOL smart money cüzdan listesi
│   └── checkpoints/             # Son işlenen signature vb.
├── logs/
├── tasks/
│   ├── todo.md
│   └── lessons.md
├── requirements.txt
├── Dockerfile
├── Procfile
└── .env.example
```

---

## 🗄️ DATABASE ŞEMASI

### sol_alert_snapshots
```sql
CREATE TABLE sol_alert_snapshots (
    id SERIAL PRIMARY KEY,
    token_address VARCHAR(50) NOT NULL,   -- Solana base58 (44 char)
    token_symbol VARCHAR(20),
    alert_mcap BIGINT,
    wallet_count INT,
    wallets_involved TEXT DEFAULT '',      -- Virgülle ayrılmış cüzdan adresleri
    created_at TIMESTAMP DEFAULT NOW()
);
```

### sol_token_evaluations
```sql
CREATE TABLE sol_token_evaluations (
    id SERIAL PRIMARY KEY,
    token_address VARCHAR(50) NOT NULL,
    token_symbol VARCHAR(20),
    alert_mcap BIGINT,
    mcap_5min BIGINT,
    mcap_30min BIGINT,
    change_5min_pct FLOAT,
    change_30min_pct FLOAT,
    classification VARCHAR(20),           -- trash / short_list / contracts_check
    wallets_involved JSONB,
    alert_time TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW()
);
```

### sol_wallet_activity
```sql
CREATE TABLE sol_wallet_activity (
    id SERIAL PRIMARY KEY,
    wallet_address VARCHAR(50) NOT NULL,
    token_address VARCHAR(50) NOT NULL,
    token_symbol VARCHAR(20),
    tx_signature VARCHAR(100),
    is_early BOOLEAN DEFAULT FALSE,
    alert_mcap BIGINT DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW()
);
```

### sol_fake_alerts
```sql
CREATE TABLE sol_fake_alerts (
    id SERIAL PRIMARY KEY,
    data JSONB NOT NULL,
    updated_at TIMESTAMP DEFAULT NOW()
);
```

### sol_smartest_wallets
```sql
CREATE TABLE sol_smartest_wallets (
    id SERIAL PRIMARY KEY,
    data JSONB NOT NULL,
    updated_at TIMESTAMP DEFAULT NOW()
);
```

---

## 🔍 FİLTRE PIPELINE (Base'den uyarlama)

```
Helius Transaction Parse
    ↓
1. Stablecoin/SOL filtresi (USDC, USDT, wSOL skip)
2. Aynı cüzdan aynı tokeni zaten aldıysa → Skip
3. DEX Program ID kontrolü → Swap yoksa Skip (airdrop/dust)
4. Likidite ≥ $5K → Skip
5. Alım değeri ≥ $5 USD → Skip (dust filtresi)
6. 24s Hacim ≥ $10K → Skip
7. 24s İşlem ≥ 15 → Skip
8. MCap ≤ $700K → Skip
    ↓
✅ Geçerli Alım → Token tracking'e ekle
    ↓
20sn time window'da 3+ unique cüzdan?
    ↓
9. İkinci kat hacim/işlem kontrolü → Başarısız = Fake alert
10. Soft blackout kontrolü (eşik 3→4)
    ↓
✅ Alert Gönder
```

### Solana'ya Özel Ek Filtreler
- **Pump.fun bonding curve**: Henüz Raydium'a migrate olmamış tokenler (çok erken, riskli) — opsiyonel filtre
- **Token freeze authority**: Freeze authority aktifse uyarı ekle
- **Rent-exempt check**: Token account'un gerçek olup olmadığı

---

## ⚙️ MONITORING DÖNGÜSÜ (Solana'ya Özel)

### Polling Yaklaşımı (Faz 1)
```
Ana Loop (her 3 saniye):
  1. Her smart money cüzdanı için:
     - getSignaturesForAddress(wallet, limit=5, until=last_sig)
     - Yeni signature'lar varsa:
       - getTransaction(signature) ile detay al
       - Helius Enhanced TX API ile parse et
  2. SPL token transferlerini filtrele
  3. DEX swap doğrulaması yap
  4. Filtre pipeline'dan geçir
  5. Token tracking'e ekle
  6. Alert kontrolü
```

### Performans Optimizasyonu
- **Batch processing**: 200 cüzdanı 10'arlık gruplar halinde sorgula
- **Rate limiting**: Helius free tier 10 RPC/sn → dikkatli kullan
- **Caching**: Son sorgulanan signature'ları cache'le
- **Checkpoint**: Son işlenen signature'ı kaydet (restart dayanıklılığı)

### Alternatif: Helius Enhanced WebSocket (Faz 2 optimizasyon)
```
WebSocket bağlantısı:
  - accountSubscribe ile her cüzdanın token account'larını dinle
  - Değişiklik olunca → parse et → filtrele → track et
  - Avantaj: Polling'den çok daha hızlı
  - Dezavantaj: 200+ subscription = memory yoğun
```

---

## 📱 TELEGRAM ALERT FORMATI

```
🚨 SOL SMART MONEY ALERT! 🚨

📊 Token: $BONK
📛 Ad: Bonk
📍 Contract: DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263

💰 MCap: $450K
💧 Likidite: $80K
📊 24s Hacim: $120K
📈 24s Değişim: +35%

👛 Alım Yapan Cüzdanlar (3):
  • 7xKXtg...2nGy | 15 SOL | MCap: $420K
  • 3vQB7z...8mPw | 22 SOL | MCap: $435K
  • 9fRTkp...4dXe | 10 SOL | MCap: $440K

💵 Toplam Alım: 47 SOL
⏰ Tespit: 14:35:22

🔗 DEXScreener | Solscan | Birdeye
```

---

## 🚀 FAZLI UYGULAMA PLANI

### FAZ 1: Temel Monitoring + Alert (İlk Sprint)
- [ ] Proje yapısını oluştur
- [ ] config/settings.py — tüm parametreler
- [ ] solana_client.py — Helius RPC wrapper
- [ ] database.py — sol_ prefix tablolar oluştur
- [ ] tx_classifier.py — DEX swap doğrulama
- [ ] wallet_monitor.py — polling loop + filtre pipeline
- [ ] telegram_alert.py — alert formatlama + gönderim
- [ ] Smart money cüzdan listesi (başlangıç seti)
- [ ] Lokal test — birkaç cüzdanla monitoring test et
- [ ] .env.example + Telegram bot/grup setup

### FAZ 2: Kalite + Rapor
- [ ] mcap_checker.py — 5dk/30dk MCap kontrolü
- [ ] daily_report.py — günlük W/L kapanış
- [ ] Soft blackout mekanizması
- [ ] Fake alert tracker
- [ ] data_cleanup.py — 30 gün veri temizliği
- [ ] Alert cooldown (5dk)
- [ ] Bullish alert (2. alert 30dk içinde)

### FAZ 3: Self-Improving Engine
- [ ] alert_analyzer.py — trash/short_list/contracts_check sınıflandırma
- [ ] wallet_scorer.py — smartest wallet skorlama
- [ ] wallet_evaluator.py — cüzdan temizleme (%90+ trash → remove)
- [ ] wallet_discoverer.py — yeni cüzdan keşfi
- [ ] self_improving_engine.py — 9-faz orkestrasyon

### FAZ 4: Deploy + Optimizasyon
- [ ] Dockerfile + Procfile
- [ ] Koyeb deploy (ayrı servis)
- [ ] WebSocket/Webhook optimizasyonu (polling'den geçiş)
- [ ] Performans tuning

---

## ⚠️ BASE'DEN ÖĞRENILEN DERSLER (1. GÜNDEN UYGULANACAK)

1. ✅ Airdrop/multicall filtresi → DEX program ID kontrolü
2. ✅ Soft blackout (hard block değil, eşik yükselt)
3. ✅ wallets_involved 1. günden kaydet
4. ✅ Günlük W/L raporu
5. ✅ Min alım değeri filtresi ($5+)
6. ✅ Swap doğrulaması (gerçek alım mı?)
7. ✅ İki katmanlı filtre (erken eleme + son kontrol)
8. ✅ Checkpoint sistemi (restart sonrası kaldığı yerden devam)

---

## 🔑 GEREKLİ CREDENTIALS (Kullanıcıdan alınacak)

| Ne | Durum |
|----|-------|
| Helius API Key | ❌ Kullanıcı alacak (ücretsiz) |
| Telegram Bot Token | ❌ @BotFather ile oluşturulacak |
| Telegram Chat ID | ❌ Grup oluşturulup ID alınacak |
| Neon PostgreSQL URL | ✅ Mevcut (Base ile paylaşılacak) |

---

## 📝 NOTLAR

- Solana adresleri base58 format (44 karakter) — VARCHAR(50) yeterli
- Solana block süresi ~400ms (Base ~2s) — polling interval 3sn yeterli
- Helius free tier: 500K kredi/gün (~10 RPC/sn) — 200 cüzdan için yeterli olmalı
- SOL fiyatı değişken — USD değer hesabı için DexScreener veya Jupiter Price API
- Pump.fun tokenler çok erken aşamada olabilir — opsiyonel filtre düşünülebilir
