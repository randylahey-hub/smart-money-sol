"""
Database Module - PostgreSQL (Neon) ile kalıcı veri depolama.
Base bot ile aynı DB'yi paylaşır, tablolar sol_ prefix ile ayrılır.
"""

import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import DATABASE_URL

# PostgreSQL bağlantısı
_connection = None
_db_available = False

if DATABASE_URL:
    try:
        import psycopg2
        from psycopg2.extras import Json
        _db_available = True
    except ImportError:
        print("⚠️ psycopg2 yüklü değil, DB kullanılamaz")
        _db_available = False


def get_connection():
    """PostgreSQL bağlantısını al veya oluştur."""
    global _connection
    if not _db_available:
        return None

    try:
        if _connection is None or _connection.closed:
            _connection = psycopg2.connect(DATABASE_URL, connect_timeout=10)
            _connection.autocommit = True
            print("✅ PostgreSQL bağlantısı kuruldu (SOL)")
        return _connection
    except Exception as e:
        print(f"❌ PostgreSQL bağlantı hatası: {e}")
        return None


def init_db():
    """sol_ prefix tablolarını oluştur."""
    conn = get_connection()
    if not conn:
        if DATABASE_URL:
            print("⚠️ Database bağlantısı kurulamadı")
        return False

    try:
        cur = conn.cursor()

        # JSONB tablolar
        jsonb_tables = ["sol_smartest_wallets", "sol_fake_alerts"]
        for table in jsonb_tables:
            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS {table} (
                    id SERIAL PRIMARY KEY,
                    data JSONB NOT NULL,
                    updated_at TIMESTAMP DEFAULT NOW()
                )
            """)

        # sol_alert_snapshots
        cur.execute("""
            CREATE TABLE IF NOT EXISTS sol_alert_snapshots (
                id SERIAL PRIMARY KEY,
                token_address VARCHAR(50) NOT NULL,
                token_symbol VARCHAR(20),
                alert_mcap BIGINT,
                wallet_count INT,
                wallets_involved TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)

        # sol_token_evaluations
        cur.execute("""
            CREATE TABLE IF NOT EXISTS sol_token_evaluations (
                id SERIAL PRIMARY KEY,
                token_address VARCHAR(50) NOT NULL,
                token_symbol VARCHAR(20),
                alert_mcap BIGINT,
                mcap_5min BIGINT,
                mcap_30min BIGINT,
                change_5min_pct FLOAT,
                change_30min_pct FLOAT,
                classification VARCHAR(20),
                wallets_involved JSONB,
                alert_time TIMESTAMP,
                ath_mcap BIGINT DEFAULT 0,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_sol_te_token ON sol_token_evaluations(token_address)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_sol_te_class ON sol_token_evaluations(classification)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_sol_te_alert_time ON sol_token_evaluations(alert_time)")

        # ath_mcap sütunu yoksa ekle (mevcut tablolar için migration)
        try:
            cur.execute("ALTER TABLE sol_token_evaluations ADD COLUMN IF NOT EXISTS ath_mcap BIGINT DEFAULT 0")
        except Exception:
            pass

        # sol_wallet_activity
        cur.execute("""
            CREATE TABLE IF NOT EXISTS sol_wallet_activity (
                id SERIAL PRIMARY KEY,
                wallet_address VARCHAR(50) NOT NULL,
                token_address VARCHAR(50) NOT NULL,
                token_symbol VARCHAR(20),
                tx_signature VARCHAR(100),
                is_early BOOLEAN DEFAULT FALSE,
                alert_mcap BIGINT DEFAULT 0,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_sol_wa_wallet ON sol_wallet_activity(wallet_address)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_sol_wa_created ON sol_wallet_activity(created_at)")

        # sol_trade_signals
        cur.execute("""
            CREATE TABLE IF NOT EXISTS sol_trade_signals (
                id SERIAL PRIMARY KEY,
                token_address VARCHAR(50) NOT NULL,
                token_symbol VARCHAR(20),
                entry_mcap BIGINT,
                trigger_type VARCHAR(20),
                wallet_count INT DEFAULT 1,
                status VARCHAR(20) DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT NOW(),
                processed_at TIMESTAMP,
                trade_result JSONB
            )
        """)

        cur.close()
        print(f"✅ SOL Database tabloları hazır (sol_ prefix)")
        return True

    except Exception as e:
        print(f"❌ Tablo oluşturma hatası: {e}")
        return False


def is_db_available() -> bool:
    """DB kullanılabilir mi?"""
    return _db_available and DATABASE_URL != ""


# =============================================================================
# GENERIC CRUD (JSONB tablolar için)
# =============================================================================

def _load_from_db(table_name: str) -> dict:
    """DB'den JSONB verisini oku."""
    conn = get_connection()
    if not conn:
        return None
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT data FROM {table_name} ORDER BY id DESC LIMIT 1")
        row = cur.fetchone()
        cur.close()
        if row:
            return row[0] if isinstance(row[0], dict) else json.loads(row[0])
        return None
    except Exception as e:
        print(f"⚠️ DB okuma hatası ({table_name}): {e}")
        return None


def _save_to_db(table_name: str, data: dict):
    """DB'ye JSONB verisini yaz (upsert)."""
    conn = get_connection()
    if not conn:
        return False
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT id FROM {table_name} LIMIT 1")
        existing = cur.fetchone()
        if existing:
            cur.execute(
                f"UPDATE {table_name} SET data = %s, updated_at = NOW() WHERE id = %s",
                (Json(data), existing[0])
            )
        else:
            cur.execute(
                f"INSERT INTO {table_name} (data) VALUES (%s)",
                (Json(data),)
            )
        cur.close()
        return True
    except Exception as e:
        print(f"⚠️ DB yazma hatası ({table_name}): {e}")
        return False


# =============================================================================
# FAKE ALERTS
# =============================================================================

def load_fake_alerts_db() -> dict:
    if not is_db_available():
        return None
    return _load_from_db("sol_fake_alerts")

def save_fake_alerts_db(data: dict) -> bool:
    if not is_db_available():
        return False
    return _save_to_db("sol_fake_alerts", data)


# =============================================================================
# SMARTEST WALLETS
# =============================================================================

def load_smartest_wallets_db() -> dict:
    if not is_db_available():
        return None
    return _load_from_db("sol_smartest_wallets")

def save_smartest_wallets_db(data: dict) -> bool:
    if not is_db_available():
        return False
    return _save_to_db("sol_smartest_wallets", data)


# =============================================================================
# ALERT SNAPSHOTS
# =============================================================================

def save_alert_snapshot(token_address: str, token_symbol: str, alert_mcap: int,
                        wallet_count: int, wallets_involved: list = None) -> bool:
    """Alert snapshot kaydet."""
    conn = get_connection()
    if not conn:
        return False
    try:
        wallets_str = ",".join(wallets_involved) if wallets_involved else ""
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO sol_alert_snapshots (token_address, token_symbol, alert_mcap,
                                             wallet_count, wallets_involved)
            VALUES (%s, %s, %s, %s, %s)
        """, (token_address, token_symbol, alert_mcap, wallet_count, wallets_str))
        cur.close()
        return True
    except Exception as e:
        print(f"⚠️ Alert snapshot yazma hatası: {e}")
        return False


def get_alerts_by_date_range(start_utc: str, end_utc: str) -> list:
    """
    Belirli UTC tarih aralığındaki alert snapshot'larını getir.
    sol_token_evaluations'tan classification ve ath_mcap bilgisini LEFT JOIN ile çeker.
    """
    conn = get_connection()
    if not conn:
        return []
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT
                a.token_address,
                a.token_symbol,
                CASE WHEN a.alert_mcap > 0 THEN a.alert_mcap ELSE COALESCE(te.alert_mcap, 0) END as alert_mcap,
                a.wallet_count,
                a.created_at,
                te.classification,
                COALESCE(te.ath_mcap, 0) as ath_mcap
            FROM sol_alert_snapshots a
            LEFT JOIN LATERAL (
                SELECT alert_mcap, classification, ath_mcap
                FROM sol_token_evaluations
                WHERE token_address = a.token_address
                ORDER BY ABS(EXTRACT(EPOCH FROM (alert_time - a.created_at))) ASC
                LIMIT 1
            ) te ON true
            WHERE a.created_at >= %s AND a.created_at < %s
            ORDER BY a.created_at ASC
        """, (start_utc, end_utc))
        rows = cur.fetchall()
        cur.close()
        return [{
            "token_address": r[0],
            "token_symbol": r[1],
            "alert_mcap": r[2] or 0,
            "wallet_count": r[3] or 0,
            "created_at": r[4].isoformat() if r[4] else None,
            "classification": r[5] or "unknown",
            "ath_mcap": r[6] or 0,
        } for r in rows]
    except Exception as e:
        print(f"⚠️ Date range alert sorgu hatası: {e}")
        return []


# =============================================================================
# TOKEN EVALUATIONS
# =============================================================================

def save_token_evaluation(token_address: str, token_symbol: str, alert_mcap: int,
                          wallets_involved: list = None, alert_time: str = None,
                          mcap_5min: int = None, mcap_30min: int = None,
                          change_5min_pct: float = None, change_30min_pct: float = None,
                          classification: str = None, ath_mcap: int = None) -> bool:
    """Token değerlendirme kaydı oluştur veya güncelle. ATH MCap her check'te güncellenir."""
    conn = get_connection()
    if not conn:
        return False
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, COALESCE(ath_mcap, 0) FROM sol_token_evaluations
            WHERE token_address = %s AND alert_time = %s
            LIMIT 1
        """, (token_address, alert_time))
        existing = cur.fetchone()

        if existing:
            updates = []
            params = []
            if mcap_5min is not None:
                updates.append("mcap_5min = %s"); params.append(mcap_5min)
            if mcap_30min is not None:
                updates.append("mcap_30min = %s"); params.append(mcap_30min)
            if change_5min_pct is not None:
                updates.append("change_5min_pct = %s"); params.append(change_5min_pct)
            if change_30min_pct is not None:
                updates.append("change_30min_pct = %s"); params.append(change_30min_pct)
            if classification is not None:
                updates.append("classification = %s"); params.append(classification)
            # ATH MCap: sadece mevcut değerden büyükse güncelle
            if ath_mcap is not None:
                current_ath = existing[1] or 0
                if ath_mcap > current_ath:
                    updates.append("ath_mcap = %s"); params.append(ath_mcap)
            if updates:
                params.append(existing[0])
                cur.execute(f"UPDATE sol_token_evaluations SET {', '.join(updates)} WHERE id = %s", params)
        else:
            cur.execute("""
                INSERT INTO sol_token_evaluations
                    (token_address, token_symbol, alert_mcap, wallets_involved, alert_time,
                     mcap_5min, mcap_30min, change_5min_pct, change_30min_pct, classification, ath_mcap)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (token_address, token_symbol, alert_mcap,
                  Json(wallets_involved or []), alert_time,
                  mcap_5min, mcap_30min, change_5min_pct, change_30min_pct, classification,
                  ath_mcap or alert_mcap))  # İlk kayıtta ath_mcap = alert_mcap

        cur.close()
        return True
    except Exception as e:
        print(f"⚠️ Token evaluation yazma hatası: {e}")
        return False


# =============================================================================
# WALLET ACTIVITY
# =============================================================================

def save_wallet_activity(wallet_address: str, token_address: str, token_symbol: str,
                         tx_signature: str = "", is_early: bool = False,
                         alert_mcap: int = 0) -> bool:
    """Cüzdan alım aktivitesini kaydet."""
    conn = get_connection()
    if not conn:
        return False
    try:
        cur = conn.cursor()
        # Dedup: aynı cüzdan+token çifti
        cur.execute("""
            SELECT id FROM sol_wallet_activity
            WHERE wallet_address = %s AND token_address = %s
            LIMIT 1
        """, (wallet_address, token_address))
        if cur.fetchone():
            if is_early:
                cur.execute("""
                    UPDATE sol_wallet_activity SET is_early = TRUE, alert_mcap = %s
                    WHERE wallet_address = %s AND token_address = %s
                """, (alert_mcap, wallet_address, token_address))
            cur.close()
            return True
        cur.execute("""
            INSERT INTO sol_wallet_activity
                (wallet_address, token_address, token_symbol, tx_signature, is_early, alert_mcap)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (wallet_address, token_address, token_symbol, tx_signature, is_early, alert_mcap))
        cur.close()
        return True
    except Exception as e:
        print(f"⚠️ Wallet activity yazma hatası: {e}")
        return False


# =============================================================================
# TRADE SIGNALS
# =============================================================================

def save_trade_signal(token_address: str, token_symbol: str, entry_mcap: int,
                      trigger_type: str, wallet_count: int = 1) -> bool:
    """Trade sinyali kaydet."""
    conn = get_connection()
    if not conn:
        return False
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO sol_trade_signals (token_address, token_symbol, entry_mcap, trigger_type, wallet_count)
            VALUES (%s, %s, %s, %s, %s)
        """, (token_address, token_symbol, entry_mcap, trigger_type, wallet_count))
        cur.close()
        print(f"📡 Trade signal yazıldı: {token_symbol} ({trigger_type})")
        return True
    except Exception as e:
        print(f"⚠️ Trade signal yazma hatası: {e}")
        return False


def is_duplicate_signal(token_address: str, cooldown_seconds: int = 300) -> bool:
    """Aynı token için son 5dk içinde sinyal var mı?"""
    conn = get_connection()
    if not conn:
        return False
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT COUNT(*) FROM sol_trade_signals
            WHERE token_address = %s
              AND status IN ('pending', 'processing', 'executed')
              AND created_at > NOW() - INTERVAL '%s seconds'
        """, (token_address, cooldown_seconds))
        count = cur.fetchone()[0]
        cur.close()
        return count > 0
    except Exception as e:
        return False


# =============================================================================
# CLEANUP
# =============================================================================

def cleanup_old_data(days: int = 30) -> dict:
    """Eski verileri temizle."""
    conn = get_connection()
    if not conn:
        return {}
    results = {}
    try:
        cur = conn.cursor()
        for table in ["sol_wallet_activity", "sol_alert_snapshots", "sol_token_evaluations"]:
            cur.execute(f"DELETE FROM {table} WHERE created_at < NOW() - INTERVAL '%s days'", (days,))
            results[table] = cur.rowcount
        cur.close()
        total = sum(results.values())
        if total > 0:
            print(f"🗑️ {total} eski kayıt temizlendi: {results}")
    except Exception as e:
        print(f"⚠️ Cleanup hatası: {e}")
    return results


# =============================================================================
# TEST
# =============================================================================
if __name__ == "__main__":
    print("SOL Database Module Test")
    print("=" * 50)
    print(f"DATABASE_URL: {'***' + DATABASE_URL[-20:] if DATABASE_URL else 'YOK'}")
    print(f"DB Available: {is_db_available()}")

    if is_db_available():
        success = init_db()
        print(f"Init DB: {'✅' if success else '❌'}")
