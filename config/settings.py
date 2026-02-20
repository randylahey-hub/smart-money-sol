"""
Solana Smart Money Alert Bot - Konfigürasyon Dosyası
Koyeb deployment için environment variables destekler.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# =============================================================================
# TELEGRAM AYARLARI (Ayrı bot + ayrı grup)
# =============================================================================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8326659948:AAGWFh0q3SzN59llUR5-rTSsnZN4ZNVrFFA")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "-5107926571")

# =============================================================================
# HELIUS RPC AYARLARI (Solana Mainnet)
# =============================================================================
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "234d6c60-9839-4fe5-88db-d9419615de8e")
HELIUS_RPC_URL = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"
HELIUS_API_URL = f"https://api.helius.xyz/v0"

# =============================================================================
# ALCHEMY RPC (Primary — Helius kredit tasarrufu için)
# =============================================================================
ALCHEMY_API_KEY = os.getenv("ALCHEMY_API_KEY", "")
ALCHEMY_RPC_URL = f"https://solana-mainnet.g.alchemy.com/v2/{ALCHEMY_API_KEY}" if ALCHEMY_API_KEY else ""

# Primary RPC: Alchemy varsa onu kullan (standart çağrılar), yoksa Helius
SOLANA_RPC_HTTP = ALCHEMY_RPC_URL if ALCHEMY_API_KEY else HELIUS_RPC_URL

# =============================================================================
# WEBHOOK AYARLARI (Helius Enhanced Webhooks)
# =============================================================================
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
WEBHOOK_ID_FILE = "data/webhook_id.txt"

# =============================================================================
# ALERT AYARLARI
# =============================================================================
# Kaç cüzdan aynı tokeni almalı ki alert tetiklensin
ALERT_THRESHOLD = int(os.getenv("ALERT_THRESHOLD", "3"))

# Maksimum market cap filtresi (USD)
MAX_MCAP = int(os.getenv("MAX_MCAP", "700000"))  # $700K

# Zaman penceresi (saniye) - Bu süre içinde alımlar olmalı
TIME_WINDOW = int(os.getenv("TIME_WINDOW", "20"))

# Aynı token için tekrar alert gönderilmeden önce bekleme süresi (saniye)
ALERT_COOLDOWN = int(os.getenv("ALERT_COOLDOWN", "300"))  # 5 dakika

# Minimum 24 saatlik hacim (USD)
MIN_VOLUME_24H = int(os.getenv("MIN_VOLUME_24H", "10000"))  # $10K

# Minimum 24 saatlik işlem sayısı (buys + sells)
MIN_TXNS_24H = int(os.getenv("MIN_TXNS_24H", "15"))

# Minimum alım değeri (USD) - Bunun altı dust/airdrop kabul edilir
MIN_BUY_VALUE_USD = int(os.getenv("MIN_BUY_VALUE_USD", "5"))  # $5

# Minimum likidite (USD)
MIN_LIQUIDITY = int(os.getenv("MIN_LIQUIDITY", "5000"))  # $5K

# Bullish tekrarlayan alert penceresi (saniye)
BULLISH_WINDOW = int(os.getenv("BULLISH_WINDOW", "1800"))  # 30 dakika

# Fake alarm eşiği
FAKE_ALERT_FLAG_THRESHOLD = int(os.getenv("FAKE_ALERT_FLAG_THRESHOLD", "3"))

# Data retention (gün)
DATA_RETENTION_DAYS = int(os.getenv("DATA_RETENTION_DAYS", "30"))

# Soft blackout saatleri (UTC+3)
# NOT: Sol için henüz analiz yapılmadı, şimdilik devre dışı.
# TODO (23 Şubat 2026): 5 günlük veri toplandıktan sonra Sol'a özel blackout saatleri hesapla.
BLACKOUT_HOURS_STR = os.getenv("BLACKOUT_HOURS", "")
BLACKOUT_HOURS = [int(h.strip()) for h in BLACKOUT_HOURS_STR.split(",") if h.strip()]
BLACKOUT_EXTRA_THRESHOLD = int(os.getenv("BLACKOUT_EXTRA_THRESHOLD", "1"))

# =============================================================================
# SOLANA DEX PROGRAM ID'LERİ (Swap doğrulaması için)
# =============================================================================
DEX_PROGRAM_IDS = {
    "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSdgbctX": "Raydium AMM V4",
    "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK": "Raydium CLMM",
    "CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C": "Raydium CPMM",
    "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4": "Jupiter V6",
    "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P": "Pump.fun",
    "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA": "PumpSwap",
    "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc": "Orca Whirlpool",
    "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo": "Meteora DLMM",
}

DEX_PROGRAM_ID_SET = set(DEX_PROGRAM_IDS.keys())

# =============================================================================
# EXCLUDED TOKENS (Alert dışı bırakılacak tokenlar - Solana)
# =============================================================================
EXCLUDED_TOKENS = [
    "So11111111111111111111111111111111111111112",    # Wrapped SOL (wSOL)
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",   # USDT
    "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So",    # mSOL
    "7dHbWXmci3dT8UFYWYZweBLXgycu7Y3iL6trKn1Y7ARj",   # stSOL
    "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn",   # JitoSOL
    "bSo13r4TkiE4KumL71LsHTPpL2euBYLFx6h9HP3piy1",    # bSOL
    "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",   # BONK (çok büyük)
    "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",     # JUP
]

EXCLUDED_SYMBOLS = ["SOL", "WSOL", "USDC", "USDT", "MSOL", "STSOL", "JITOSOL", "BSOL", "JUP"]

# =============================================================================
# SMARTEST WALLET DETECTION
# =============================================================================
EARLY_BUY_THRESHOLD = int(os.getenv("EARLY_BUY_THRESHOLD", "3"))
MAX_TOKENS_PER_WEEK = int(os.getenv("MAX_TOKENS_PER_WEEK", "20"))
MIN_EARLY_HIT_RATE = float(os.getenv("MIN_EARLY_HIT_RATE", "0.15"))
WALLET_SCORING_WINDOW_DAYS = int(os.getenv("WALLET_SCORING_WINDOW_DAYS", "30"))
SMARTEST_WALLET_TARGET = int(os.getenv("SMARTEST_WALLET_TARGET", "15"))

# =============================================================================
# DATABASE AYARLARI (Neon PostgreSQL - Base ile paylaşımlı, sol_ prefix)
# =============================================================================
DATABASE_URL = os.getenv("DATABASE_URL", "")

# =============================================================================
# SELF-IMPROVING ENGINE AYARLARI
# =============================================================================
SELF_IMPROVE_ENABLED = os.getenv("SELF_IMPROVE_ENABLED", "false").lower() == "true"
SHORT_LIST_THRESHOLD = float(os.getenv("SHORT_LIST_THRESHOLD", "0.20"))
CONTRACTS_CHECK_THRESHOLD = float(os.getenv("CONTRACTS_CHECK_THRESHOLD", "0.50"))
DEAD_TOKEN_MCAP = int(os.getenv("DEAD_TOKEN_MCAP", "20000"))
TRASH_WARN_THRESHOLD = float(os.getenv("TRASH_WARN_THRESHOLD", "0.70"))
TRASH_REMOVE_THRESHOLD = float(os.getenv("TRASH_REMOVE_THRESHOLD", "0.90"))
MIN_APPEARANCES_FOR_REMOVAL = int(os.getenv("MIN_APPEARANCES_FOR_REMOVAL", "5"))

# =============================================================================
# İZLEME AYARLARI
# =============================================================================
LOG_FILE = "logs/monitor.log"
CHECKPOINT_FILE = "data/checkpoints/last_signatures.json"

# Polling interval (saniye) — Yedek polling için (webhook modda 300s)
POLLING_INTERVAL = int(os.getenv("POLLING_INTERVAL", "300"))

# Batch size - Kaç cüzdanı paralel sorgula
WALLET_BATCH_SIZE = int(os.getenv("WALLET_BATCH_SIZE", "25"))

# Her cüzdan için son kaç tx kontrol edilsin
TX_FETCH_LIMIT = int(os.getenv("TX_FETCH_LIMIT", "5"))
