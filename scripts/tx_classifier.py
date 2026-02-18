"""
Transaction Classifier - Solana transaction'larını sınıflandırır.
Swap doğrulaması, airdrop/dust filtresi.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import DEX_PROGRAM_IDS, DEX_PROGRAM_ID_SET


def classify_enhanced_tx(enhanced_tx: dict) -> dict:
    """
    Helius Enhanced Transaction'ı sınıflandır.

    Returns:
        {
            'type': 'SWAP' | 'TRANSFER' | 'AIRDROP' | 'UNKNOWN',
            'is_swap': True/False,
            'skip': True/False,
            'reason': '...',
            'source': 'RAYDIUM' | 'JUPITER' | ...,
        }
    """
    tx_type = enhanced_tx.get("type", "UNKNOWN")
    source = enhanced_tx.get("source", "UNKNOWN")
    description = enhanced_tx.get("description", "")

    # Helius SWAP tipini doğrudan tanıyor
    if tx_type == "SWAP":
        return {
            "type": "SWAP",
            "is_swap": True,
            "skip": False,
            "reason": "",
            "source": source,
        }

    # Token transfer ama swap değil → muhtemelen airdrop/dust
    if tx_type == "TRANSFER":
        token_transfers = enhanced_tx.get("tokenTransfers", [])

        # Çok fazla alıcı varsa → batch airdrop
        unique_recipients = set()
        for tt in token_transfers:
            to = tt.get("toUserAccount", "")
            if to:
                unique_recipients.add(to)

        if len(unique_recipients) > 5:
            return {
                "type": "AIRDROP",
                "is_swap": False,
                "skip": True,
                "reason": f"Batch transfer: {len(unique_recipients)} farklı alıcı",
                "source": source,
            }

        return {
            "type": "TRANSFER",
            "is_swap": False,
            "skip": True,
            "reason": "Transfer, swap değil",
            "source": source,
        }

    # COMPRESSED_NFT_MINT, NFT_MINT, etc → skip
    if "NFT" in tx_type:
        return {
            "type": tx_type,
            "is_swap": False,
            "skip": True,
            "reason": "NFT işlemi",
            "source": source,
        }

    # Unknown tip — program ID'lerden kontrol et
    account_data = enhanced_tx.get("accountData", [])
    instructions = enhanced_tx.get("instructions", [])

    # Instructions'daki program ID'lerden DEX var mı?
    for ix in instructions:
        program_id = ix.get("programId", "")
        if program_id in DEX_PROGRAM_ID_SET:
            return {
                "type": "SWAP",
                "is_swap": True,
                "skip": False,
                "reason": "",
                "source": DEX_PROGRAM_IDS.get(program_id, source),
            }

    # Inner instructions kontrolü
    for ix in instructions:
        for inner in ix.get("innerInstructions", []):
            program_id = inner.get("programId", "")
            if program_id in DEX_PROGRAM_ID_SET:
                return {
                    "type": "SWAP",
                    "is_swap": True,
                    "skip": False,
                    "reason": "",
                    "source": DEX_PROGRAM_IDS.get(program_id, source),
                }

    # Hiçbir DEX program ID bulunamadı → skip
    return {
        "type": tx_type,
        "is_swap": False,
        "skip": True,
        "reason": f"DEX program ID bulunamadı (tip: {tx_type})",
        "source": source,
    }


def is_valid_swap(enhanced_tx: dict, target_wallet: str) -> dict:
    """
    Enhanced TX'in target_wallet için geçerli bir swap olup olmadığını kontrol et.

    Returns:
        {
            'valid': True/False,
            'token_mint': '...',      # Alınan tokenin mint adresi
            'token_amount': 0.0,      # Alınan miktar
            'sol_spent': 0.0,         # Harcanan SOL
            'source': 'RAYDIUM',      # DEX kaynağı
            'reason': '...',          # Skip nedeni (varsa)
        }
    """
    # 1. Swap doğrulaması
    classification = classify_enhanced_tx(enhanced_tx)
    if not classification["is_swap"]:
        return {
            "valid": False,
            "token_mint": "",
            "token_amount": 0,
            "sol_spent": 0,
            "source": classification["source"],
            "reason": classification["reason"],
        }

    fee_payer = enhanced_tx.get("feePayer", "")
    token_transfers = enhanced_tx.get("tokenTransfers", [])
    native_transfers = enhanced_tx.get("nativeTransfers", [])

    # 2. Target wallet'a gelen token transferini bul
    received_token = None
    for tt in token_transfers:
        if tt.get("toUserAccount", "").lower() == target_wallet.lower():
            # Excluded token mi?
            from config.settings import EXCLUDED_TOKENS
            if tt.get("mint", "") in EXCLUDED_TOKENS:
                continue
            received_token = tt
            break

    if not received_token:
        return {
            "valid": False,
            "token_mint": "",
            "token_amount": 0,
            "sol_spent": 0,
            "source": classification["source"],
            "reason": "Target wallet'a gelen token transferi yok",
        }

    # 3. SOL harcama hesabı
    # a) Native SOL transfer'lerden hesapla
    sol_spent = 0
    for nt in native_transfers:
        if nt.get("fromUserAccount", "").lower() == target_wallet.lower():
            sol_spent += nt.get("amount", 0) / 1e9
    for nt in native_transfers:
        if nt.get("toUserAccount", "").lower() == target_wallet.lower():
            sol_spent -= nt.get("amount", 0) / 1e9
    sol_spent = max(0, sol_spent)

    # b) wSOL token transfer'lerden hesapla (Pump.fun, PumpSwap vb.)
    # Native SOL 0 ise, wSOL harcamasına bak
    WSOL_MINT = "So11111111111111111111111111111111111111112"
    if sol_spent == 0:
        for tt in token_transfers:
            if (tt.get("mint", "") == WSOL_MINT and
                    tt.get("fromUserAccount", "").lower() == target_wallet.lower()):
                sol_spent += float(tt.get("tokenAmount", 0) or 0)
        for tt in token_transfers:
            if (tt.get("mint", "") == WSOL_MINT and
                    tt.get("toUserAccount", "").lower() == target_wallet.lower()):
                sol_spent -= float(tt.get("tokenAmount", 0) or 0)
        sol_spent = max(0, sol_spent)

    return {
        "valid": True,
        "token_mint": received_token.get("mint", ""),
        "token_amount": received_token.get("tokenAmount", 0),
        "sol_spent": sol_spent,
        "source": classification["source"],
        "reason": "",
    }
