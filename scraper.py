"""
ANNUAIRE ROMAND — Agent de scraping
Cherche des entreprises sur Zefix (registre du commerce suisse)
et les ajoute automatiquement dans la base de données Supabase.
"""

import os
import time
import requests
from supabase import create_client, Client
from datetime import datetime

# ── Configuration ──────────────────────────────────────────────
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

# Cantons de Suisse romande
CANTONS_ROMANDS = ["GE", "VD", "VS", "FR", "NE", "JU", "BE"]

# Mapping code canton → nom complet
NOM_CANTON = {
    "GE": "Genève",
    "VD": "Vaud",
    "VS": "Valais",
    "FR": "Fribourg",
    "NE": "Neuchâtel",
    "JU": "Jura",
    "BE": "Berne",
}

# Mapping code NOGA → secteur lisible
SECTEURS = {
    "41": "BTP / Construction",
    "42": "BTP / Construction",
    "43": "BTP / Construction",
    "68": "Immobilier",
    "64": "Finance & Assurance",
    "65": "Finance & Assurance",
    "66": "Finance & Assurance",
    "62": "Informatique & Tech",
    "63": "Informatique & Tech",
    "86": "Santé",
    "87": "Santé",
    "47": "Commerce & Retail",
    "46": "Commerce & Retail",
    "56": "Restauration & Hôtellerie",
    "55": "Restauration & Hôtellerie",
    "10": "Industrie & Manufacture",
    "25": "Industrie & Manufacture",
    "49": "Transport & Logistique",
    "52": "Transport & Logistique",
    "85": "Éducation & Formation",
    "69": "Juridique & Conseil",
    "70": "Juridique & Conseil",
}

# ── Connexion Supabase ─────────────────────────────────────────
def get_supabase() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_KEY)

# ── Récupérer les entreprises depuis Zefix API ─────────────────
def fetch_zefix(canton: str, offset: int = 0, limit: int = 100) -> list:
    url = "https://www.zefix.ch/ZefixREST/api/v1/firm/search.json"
    payload = {
        "cantonAbbreviation": canton,
        "activeOnly": True,
        "offset": offset,
        "maxEntries": limit,
        "legalForms": []
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=30)
        r.raise_for_status()
        data = r.json()
        return data.get("list", [])
    except Exception as e:
        print(f"  ⚠ Erreur Zefix ({canton} offset {offset}): {e}")
        return []

# ── Deviner le secteur depuis le code NOGA ─────────────────────
def get_secteur(firm: dict) -> str:
    noga = firm.get("nogaCode", "")
    if noga:
        prefix = noga[:2]
        return SECTEURS.get(prefix, "Autre")
    return "Autre"

# ── Nettoyer et formater une entreprise ───────────────────────
def format_entreprise(firm: dict, canton: str) -> dict:
    adresse = firm.get("address", {})
    return {
        "nom": firm.get("name", "").strip(),
        "adresse": adresse.get("street", ""),
        "npa": str(adresse.get("swissZipCode", "")),
        "ville": adresse.get("town", ""),
        "canton": NOM_CANTON.get(canton, canton),
        "secteur": get_secteur(firm),
        "numero_ide": firm.get("uid", ""),
        "source": "zefix.ch",
        "mis_a_jour": datetime.utcnow().isoformat(),
    }

# ── Vérifier si l'entreprise existe déjà (par IDE) ────────────
def existe_deja(supabase: Client, numero_ide: str) -> bool:
    if not numero_ide:
        return False
    res = supabase.table("entreprises") \
        .select("id") \
        .eq("numero_ide", numero_ide) \
        .execute()
    return len(res.data) > 0

# ── Insérer en masse dans Supabase ────────────────────────────
def inserer_entreprises(supabase: Client, entreprises: list) -> int:
    if not entreprises:
        return 0
    try:
        supabase.table("entreprises").insert(entreprises).execute()
        return len(entreprises)
    except Exception as e:
        print(f"  ⚠ Erreur insertion: {e}")
        return 0

# ── Scraper un canton complet ──────────────────────────────────
def scraper_canton(supabase: Client, canton: str):
    print(f"\n📍 Canton {NOM_CANTON.get(canton, canton)}...")
    offset = 0
    total_ajoute = 0
    limit = 100

    while True:
        firms = fetch_zefix(canton, offset, limit)
        if not firms:
            break

        nouvelles = []
        for firm in firms:
            ide = firm.get("uid", "")
            if not existe_deja(supabase, ide):
                e = format_entreprise(firm, canton)
                if e["nom"]:
                    nouvelles.append(e)

        ajoute = inserer_entreprises(supabase, nouvelles)
        total_ajoute += ajoute
        print(f"  Lot {offset//limit + 1}: {ajoute} nouvelles entreprises ajoutées")

        if len(firms) < limit:
            break

        offset += limit
        time.sleep(1)

    print(f"  ✅ {total_ajoute} entreprises ajoutées pour {NOM_CANTON.get(canton, canton)}")
    return total_ajoute

# ── Point d'entrée principal ───────────────────────────────────
def main():
    print("=" * 50)
    print("🇨🇭 ANNUAIRE ROMAND — Agent de scraping")
    print(f"   Démarrage : {datetime.now().strftime('%d.%m.%Y %H:%M')}")
    print("=" * 50)

    supabase = get_supabase()
    grand_total = 0

    for canton in CANTONS_ROMANDS:
        total = scraper_canton(supabase, canton)
        grand_total += total
        time.sleep(2)

    print("\n" + "=" * 50)
    print(f"✅ Scraping terminé — {grand_total} entreprises ajoutées au total")
    print("=" * 50)

if __name__ == "__main__":
    main()
