import requests, json, time
from collections import defaultdict

PAIRS = [
    ("8bhczfjnhmfyzedh6jngoqnrhcs2r8khkypgviocnysa", "LIA"),
    ("2snmpvmsyjlmchenx5w2jp71t755bny3xif6d4d2mi35", "neeb"),
    ("ag8bqkguhdbk2sbpquxvkxkxnstvzac5hm7pginmzgdj", "Punch_screener"),
    ("67vz7p9tzvaydzrsa2bq6ftwb6y7f9srrxashy5yz5ar", "PPTAI"),
    ("8gz1ga8mrinapwsvynnwuyz4vsdc7vcunjhnyam4fhd1", "TINA"),
    ("fqekursaxwyxdoth4ovcqjeqj4zmanz7bbsfrphknjwu", "ME8000"),
    ("bchtwr4sohgu3hshd71adm6gs36ktvxsn62ztcrrm8xj", "DYN2XMN"),
    ("2bf5cukcxaxxh8xge3dkatcj3fvymzisxhtxodxsxntu", "fih"),
    ("a6khmifzn9am7vkbtvp4fzny9bco2jp63r9dphaw1vrq", "Punch_original"),
    ("aadjrfmwohvxzhf1ukbhvnc5tqrbpkgdsaxtmytedm2x", "Lobstar"),
]

HELIUS_API_KEY = "4674468d-fc4c-4b71-9915-03999fa67da2"
HELIUS_RPC = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"
DEXSCREENER_URL = "https://api.dexscreener.com/latest/dex/pairs/solana/{}"


def get_mint_address(pair_addr):
    """DexScreener API ile pair'ın token mint adresini al"""
    try:
        resp = requests.get(DEXSCREENER_URL.format(pair_addr), timeout=15)
        data = resp.json()
        pairs = data.get("pairs") or data.get("pair")
        if isinstance(pairs, list) and pairs:
            return pairs[0]["baseToken"]["address"]
        elif isinstance(pairs, dict):
            return pairs["baseToken"]["address"]
    except Exception as e:
        print(f"  ❌ DexScreener hata: {e}")
    return None


def get_top_holders_largest(mint_addr):
    """
    Strateji A: getTokenLargestAccounts → top 20 token account →
    getMultipleAccounts ile owner (wallet adresi) al
    """
    try:
        # 1. Top 20 token hesabı al (balance'a göre sıralı)
        resp = requests.post(HELIUS_RPC, json={
            "jsonrpc": "2.0", "id": 1,
            "method": "getTokenLargestAccounts",
            "params": [mint_addr]
        }, timeout=15)
        accounts = [a["address"] for a in resp.json().get("result", {}).get("value", [])]
        if not accounts:
            return []

        # 2. Token hesaplarından owner (wallet) adresini çek
        resp2 = requests.post(HELIUS_RPC, json={
            "jsonrpc": "2.0", "id": 2,
            "method": "getMultipleAccounts",
            "params": [accounts, {"encoding": "jsonParsed"}]
        }, timeout=15)
        wallets = []
        for acc in resp2.json().get("result", {}).get("value", []) or []:
            if acc and acc.get("data"):
                owner = acc["data"].get("parsed", {}).get("info", {}).get("owner")
                if owner:
                    wallets.append(owner)
        return wallets
    except Exception as e:
        print(f"  ❌ getTokenLargestAccounts hata: {e}")
        return []


def get_top_holders_helius(mint_addr, pages=3):
    """
    Strateji B: Helius getTokenAccounts (enhanced) →
    sayfalama ile 100-300 holder al, owner alanından wallet adresi çek
    """
    wallets = []
    for page in range(1, pages + 1):
        try:
            resp = requests.post(HELIUS_RPC, json={
                "jsonrpc": "2.0",
                "id": f"p{page}",
                "method": "getTokenAccounts",
                "params": {
                    "page": page,
                    "limit": 100,
                    "displayOptions": {"showZeroBalance": False},
                    "mint": mint_addr
                }
            }, timeout=20)
            if resp.status_code != 200:
                print(f"  ⚠️  Helius getTokenAccounts HTTP {resp.status_code}, strateji B atlanıyor")
                return []
            result = resp.json().get("result", {})
            token_accts = result.get("token_accounts", [])
            for acct in token_accts:
                owner = acct.get("owner")
                if owner:
                    wallets.append(owner)
            if len(token_accts) < 100:
                break
            time.sleep(0.2)
        except Exception as e:
            print(f"  ❌ getTokenAccounts hata (sayfa {page}): {e}")
            break
    return wallets


def get_top_holders(mint_addr):
    """Önce Strateji B (daha fazla holder), başarısız olursa A"""
    wallets = get_top_holders_helius(mint_addr, pages=3)
    if wallets:
        return wallets
    print(f"  ↩️  Strateji B başarısız, Strateji A deneniyor...")
    return get_top_holders_largest(mint_addr)


# --- Ana akış ---
wallet_tokens = defaultdict(set)
mint_map = {}

for pair_addr, label in PAIRS:
    print(f"\n[{label}] mint adresi çekiliyor...")
    mint = get_mint_address(pair_addr)
    if not mint:
        print(f"  ⚠️  mint alınamadı, atlanıyor")
        continue
    mint_map[label] = mint
    print(f"  mint: {mint[:20]}...")

    holders = get_top_holders(mint)
    print(f"  {len(holders)} holder bulundu")
    for wallet in holders:
        wallet_tokens[wallet].add(label)
    time.sleep(0.5)

# Cross-reference
tier1 = [w for w, t in wallet_tokens.items() if len(t) >= 4]
tier2 = [w for w, t in wallet_tokens.items() if len(t) == 3]
tier3 = [w for w, t in wallet_tokens.items() if len(t) == 2]
single = [w for w, t in wallet_tokens.items() if len(t) == 1]

print(f"\n=== SONUÇ ===")
print(f"Tier1 (4+ token): {len(tier1)}")
print(f"Tier2 (3 token):  {len(tier2)}")
print(f"Tier3 (2 token):  {len(tier3)}")
print(f"Single (1 token): {len(single)}")

# 200+ hedef
final = list(dict.fromkeys(tier1 + tier2 + tier3))
if len(final) < 200:
    final += [w for w in single if w not in set(final)]
final = final[:250]

print(f"\n✅ Toplam: {len(final)} wallet → data/smart_money_wallets.json")

# Tier1 örneği
if tier1:
    print(f"\n--- Top Tier1 wallets ---")
    for w in tier1[:5]:
        tokens = sorted(wallet_tokens[w])
        print(f"  {w[:20]}... → {tokens}")

with open("data/smart_money_wallets.json", "w") as f:
    json.dump({"wallets": final}, f, indent=4)

print("\n✅ Dosya yazıldı: data/smart_money_wallets.json")
