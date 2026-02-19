"""
Solana Client - Helius RPC & Enhanced Transactions API wrapper.
Smart money cüzdanlarının Solana üzerindeki işlemlerini takip eder.
"""

import json
import os
import sys
import time
import requests
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import (
    SOLANA_RPC_HTTP,
    HELIUS_API_KEY,
    HELIUS_API_URL,
    DEX_PROGRAM_ID_SET,
    TX_FETCH_LIMIT,
)

# Rate limit tracking
_last_request_time = 0
_MIN_REQUEST_INTERVAL = 0.15  # ~6.6 req/sn (Helius free: 10 req/sn, güvenli margin)


def _rate_limit():
    """Rate limiter — Helius free tier'a saygı göster."""
    global _last_request_time
    now = time.time()
    elapsed = now - _last_request_time
    if elapsed < _MIN_REQUEST_INTERVAL:
        time.sleep(_MIN_REQUEST_INTERVAL - elapsed)
    _last_request_time = time.time()


def _rpc_call(method: str, params: list = None) -> dict:
    """Solana JSON-RPC çağrısı — 429'da exponential backoff ile retry."""
    max_retries = 3
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params or []
    }
    for attempt in range(max_retries):
        _rate_limit()
        try:
            resp = requests.post(SOLANA_RPC_HTTP, json=payload, timeout=15)
            if resp.status_code == 429:
                wait = 2 ** (attempt + 1)  # 2s, 4s, 8s
                print(f"⏳ Rate limit (429) on {method}, {wait}s bekleniyor... (attempt {attempt+1}/{max_retries})")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            data = resp.json()
            if "error" in data:
                print(f"⚠️ RPC Error ({method}): {data['error']}")
                return None
            return data.get("result")
        except requests.exceptions.HTTPError as e:
            if "429" in str(e):
                wait = 2 ** (attempt + 1)
                print(f"⏳ Rate limit (429) on {method}, {wait}s bekleniyor... (attempt {attempt+1}/{max_retries})")
                time.sleep(wait)
                continue
            print(f"⚠️ RPC HTTP Error ({method}): {e}")
            return None
        except Exception as e:
            print(f"⚠️ RPC Request Error ({method}): {e}")
            return None
    print(f"❌ {method}: {max_retries} retry sonrası başarısız (429)")
    return None


def get_slot() -> int:
    """Güncel slot numarasını al."""
    result = _rpc_call("getSlot")
    return result if result else 0


def get_signatures_for_address(address: str, limit: int = TX_FETCH_LIMIT,
                                before: str = None, until: str = None) -> list:
    """
    Bir adresin son transaction signature'larını al.

    Args:
        address: Solana wallet adresi (base58)
        limit: Kaç tx getir (max 1000)
        before: Bu signature'dan öncekileri getir
        until: Bu signature'a kadar getir

    Returns:
        [{signature, slot, blockTime, err, memo}, ...]
    """
    params = [address, {"limit": limit, "commitment": "confirmed"}]
    if before:
        params[1]["before"] = before
    if until:
        params[1]["until"] = until

    result = _rpc_call("getSignaturesForAddress", params)
    if result is None:
        return None  # 429 veya hata — None döndür (batch pausa tetikler)
    return result if result else []


def get_parsed_transaction(signature: str) -> dict:
    """
    Parsed transaction detayını al (Solana native).

    Returns:
        Full transaction object with parsed instructions
    """
    params = [signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}]
    result = _rpc_call("getTransaction", params)
    return result


def get_enhanced_transactions(signatures: list) -> list:
    """
    Helius Enhanced Transactions API — İnsan okunabilir parsed tx.
    Batch olarak birden fazla signature gönderebilir.
    429'da exponential backoff ile retry yapar.

    Args:
        signatures: Transaction signature listesi (max 100)

    Returns:
        [{description, type, source, fee, nativeTransfers, tokenTransfers, ...}, ...]
    """
    if not signatures:
        return []

    url = f"{HELIUS_API_URL}/transactions?api-key={HELIUS_API_KEY}"
    max_retries = 3
    for attempt in range(max_retries):
        _rate_limit()
        try:
            resp = requests.post(url, json={"transactions": signatures[:100]}, timeout=20)
            if resp.status_code == 429:
                wait = 2 ** (attempt + 1)
                print(f"⏳ Rate limit (429) on Enhanced TX, {wait}s bekleniyor... (attempt {attempt+1}/{max_retries})")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.HTTPError as e:
            if "429" in str(e):
                wait = 2 ** (attempt + 1)
                print(f"⏳ Rate limit (429) on Enhanced TX, {wait}s bekleniyor... (attempt {attempt+1}/{max_retries})")
                time.sleep(wait)
                continue
            print(f"⚠️ Helius Enhanced TX Error: {e}")
            return []
        except Exception as e:
            print(f"⚠️ Helius Enhanced TX Error: {e}")
            return []
    print(f"❌ Enhanced TX: {max_retries} retry sonrası başarısız (429)")
    return []


def parse_token_swaps(enhanced_tx: dict) -> list:
    """
    Enhanced transaction'dan token swap'larını çıkar.

    Returns:
        [{
            'type': 'SWAP',
            'source': 'RAYDIUM' / 'JUPITER' / 'PUMP_FUN' / ...,
            'token_in': {'mint': ..., 'amount': ..., 'symbol': ...},
            'token_out': {'mint': ..., 'amount': ..., 'symbol': ...},
            'fee_payer': '...',  # İşlemi yapan cüzdan
            'signature': '...',
        }, ...]
    """
    swaps = []
    tx_type = enhanced_tx.get("type", "")
    source = enhanced_tx.get("source", "")
    signature = enhanced_tx.get("signature", "")
    fee_payer = enhanced_tx.get("feePayer", "")

    # Helius "SWAP" tipini direkt tanıyabilir
    if tx_type == "SWAP":
        token_transfers = enhanced_tx.get("tokenTransfers", [])
        native_transfers = enhanced_tx.get("nativeTransfers", [])

        # Token transferlerinden in/out ayrımı yap
        tokens_sent = []  # Cüzdandan çıkan
        tokens_received = []  # Cüzdana gelen

        for tt in token_transfers:
            if tt.get("fromUserAccount") == fee_payer:
                tokens_sent.append(tt)
            elif tt.get("toUserAccount") == fee_payer:
                tokens_received.append(tt)

        # SOL native transferleri de kontrol et
        sol_sent = sum(
            nt.get("amount", 0) for nt in native_transfers
            if nt.get("fromUserAccount") == fee_payer
        )
        sol_received = sum(
            nt.get("amount", 0) for nt in native_transfers
            if nt.get("toUserAccount") == fee_payer
        )

        if tokens_received:
            for received in tokens_received:
                swap = {
                    "type": "SWAP",
                    "source": source,
                    "token_out": {
                        "mint": received.get("mint", ""),
                        "amount": received.get("tokenAmount", 0),
                        "symbol": received.get("tokenStandard", ""),
                    },
                    "token_in": {},
                    "fee_payer": fee_payer,
                    "signature": signature,
                    "sol_spent": 0,
                }

                # Neyle swap yapıldı?
                if tokens_sent:
                    sent = tokens_sent[0]
                    swap["token_in"] = {
                        "mint": sent.get("mint", ""),
                        "amount": sent.get("tokenAmount", 0),
                        "symbol": sent.get("tokenStandard", ""),
                    }
                elif sol_sent > sol_received:
                    # SOL harcamış
                    swap["token_in"] = {
                        "mint": "So11111111111111111111111111111111111111112",
                        "amount": (sol_sent - sol_received) / 1e9,
                        "symbol": "SOL",
                    }
                    swap["sol_spent"] = (sol_sent - sol_received) / 1e9

                swaps.append(swap)

    return swaps


def is_dex_swap(parsed_tx: dict) -> bool:
    """
    Parsed transaction'da bilinen DEX program ID'si var mı kontrol et.
    Airdrop/dust filtresi için kullanılır.

    Args:
        parsed_tx: getTransaction(jsonParsed) sonucu

    Returns:
        True = DEX swap tespit edildi (gerçek alım)
    """
    if not parsed_tx:
        return False

    try:
        message = parsed_tx.get("transaction", {}).get("message", {})

        # Outer instructions
        for ix in message.get("instructions", []):
            program_id = ix.get("programId", "")
            if program_id in DEX_PROGRAM_ID_SET:
                return True

        # Inner instructions
        meta = parsed_tx.get("meta", {})
        for inner_group in meta.get("innerInstructions", []):
            for ix in inner_group.get("instructions", []):
                program_id = ix.get("programId", "")
                if program_id in DEX_PROGRAM_ID_SET:
                    return True

    except Exception as e:
        print(f"⚠️ DEX swap kontrol hatası: {e}")

    return False


def extract_token_transfers_from_parsed(parsed_tx: dict, target_wallet: str) -> list:
    """
    Parsed transaction'dan target_wallet'a gelen SPL token transferlerini çıkar.

    Returns:
        [{'mint': ..., 'amount': ..., 'decimals': ..., 'from': ..., 'to': ...}, ...]
    """
    transfers = []
    if not parsed_tx:
        return transfers

    try:
        meta = parsed_tx.get("meta", {})
        pre_balances = meta.get("preTokenBalances", [])
        post_balances = meta.get("postTokenBalances", [])

        # Token balance değişimlerini karşılaştır
        pre_map = {}
        for b in pre_balances:
            owner = b.get("owner", "")
            mint = b.get("mint", "")
            amount = float(b.get("uiTokenAmount", {}).get("uiAmount", 0) or 0)
            key = f"{owner}:{mint}"
            pre_map[key] = amount

        for b in post_balances:
            owner = b.get("owner", "")
            mint = b.get("mint", "")
            post_amount = float(b.get("uiTokenAmount", {}).get("uiAmount", 0) or 0)
            decimals = b.get("uiTokenAmount", {}).get("decimals", 0)
            key = f"{owner}:{mint}"
            pre_amount = pre_map.get(key, 0)

            # Target wallet'a gelen token (artış)
            if owner.lower() == target_wallet.lower() and post_amount > pre_amount:
                diff = post_amount - pre_amount
                if diff > 0:
                    transfers.append({
                        "mint": mint,
                        "amount": diff,
                        "decimals": decimals,
                        "to": owner,
                    })

    except Exception as e:
        print(f"⚠️ Token transfer çıkarma hatası: {e}")

    return transfers


def get_sol_price() -> float:
    """DexScreener'dan SOL/USD fiyatını al."""
    try:
        _rate_limit()
        # wSOL DexScreener pair'inden fiyat al
        url = "https://api.dexscreener.com/latest/dex/tokens/So11111111111111111111111111111111111111112"
        resp = requests.get(url, timeout=10)
        data = resp.json()
        pairs = data.get("pairs", [])
        if pairs:
            # En yüksek likidite pair'inden fiyat
            sol_pairs = [p for p in pairs if p.get("chainId") == "solana"]
            if sol_pairs:
                best = max(sol_pairs, key=lambda x: float(x.get("liquidity", {}).get("usd", 0) or 0))
                # wSOL genelde quote token olarak kullanılır, base token fiyatını al
                price_str = best.get("priceNative", "0")
                # Ya da direkt USD fiyat varsa
                base_symbol = best.get("baseToken", {}).get("symbol", "")
                if base_symbol == "SOL" or base_symbol == "WSOL":
                    price = float(best.get("priceUsd", 0) or 0)
                else:
                    # SOL quote token — 1/priceNative
                    price_native = float(price_str or 0)
                    price_usd = float(best.get("priceUsd", 0) or 0)
                    if price_native > 0 and price_usd > 0:
                        price = price_usd / price_native
                    else:
                        price = 0
                if price > 0:
                    return price
        # Fallback: CoinGecko simple price
        resp2 = requests.get(
            "https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd",
            timeout=10
        )
        cg_price = resp2.json().get("solana", {}).get("usd", 0)
        if cg_price > 0:
            return float(cg_price)
        return 180.0  # Hard fallback
    except Exception:
        return 180.0  # Hard fallback


def get_multiple_signatures_batch(wallets: list, limit: int = TX_FETCH_LIMIT,
                                   last_signatures: dict = None) -> dict:
    """
    Birden fazla cüzdanın son transaction'larını batch olarak al.

    Args:
        wallets: Cüzdan adresleri listesi
        limit: Her cüzdan için kaç tx
        last_signatures: {wallet: last_processed_signature}

    Returns:
        {wallet: [signatures], ...}
    """
    if last_signatures is None:
        last_signatures = {}

    results = {}
    for wallet in wallets:
        until_sig = last_signatures.get(wallet)
        sigs = get_signatures_for_address(wallet, limit=limit, until=until_sig)
        if sigs:
            # Hatalı tx'leri filtrele
            valid_sigs = [s for s in sigs if s.get("err") is None]
            results[wallet] = valid_sigs
    return results


# =============================================================================
# TEST
# =============================================================================
if __name__ == "__main__":
    print("Solana Client Test")
    print("=" * 50)

    # 1. Slot kontrolü
    slot = get_slot()
    print(f"📦 Güncel Slot: {slot}")

    # 2. SOL fiyatı
    sol_price = get_sol_price()
    print(f"💰 SOL Fiyat: ${sol_price:.2f}")

    # 3. Test cüzdanı — Bir bilinen SOL cüzdanı ile
    test_wallet = "vKRuavbKFZSJf13bFsNH5oiyMERxBbPz9ntNqZCPJhu"
    print(f"\n📡 Son TX'ler: {test_wallet[:12]}...")
    sigs = get_signatures_for_address(test_wallet, limit=3)
    for s in sigs[:3]:
        print(f"  • {s['signature'][:20]}... | Slot: {s.get('slot', '?')}")

    if sigs:
        # 4. Enhanced TX test
        print(f"\n🔍 Enhanced TX test...")
        enhanced = get_enhanced_transactions([sigs[0]["signature"]])
        if enhanced:
            tx = enhanced[0]
            print(f"  Type: {tx.get('type', '?')}")
            print(f"  Source: {tx.get('source', '?')}")
            print(f"  Description: {tx.get('description', '?')[:100]}")

    print("\n✅ Solana Client test tamamlandı!")
