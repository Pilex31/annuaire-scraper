"""
ANNUAIRE ROMAND — Agent de scraping Zefix authentifié
Utilise l'API officielle ZefixPublicREST avec credentials.
"""

import os
import time
import requests
from requests.auth import HTTPBasicAuth
from supabase import create_client, Client
from datetime import datetime

# ── Configuration ──────────────────────────────────────────────
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
ZEFIX_USERNAME = os.environ.get("ZEFIX_USERNAME")
ZEFIX_PASSWORD = os.environ.get("ZEFIX_PASSWORD")

ZEFIX_BASE_URL = "https://www.zefix.admin.ch/ZefixPublicREST/api/v1"

CANTONS_ROMANDS = {
    "GE": "Genève",
    "VD": "Vaud",
    "VS": "Valais",
    "FR": "Fribourg",
    "NE": "Neuchâtel",
    "JU": "Jura",
    "BE": "Berne",
}

SECTEURS = {
    "41": "BTP / Construction", "42": "BTP / Construction", "43": "BTP / Construction",
    "68": "Immobilier",
    "64": "Finance & Assurance", "65": "Finance & Assurance", "66": "Finance & Assurance",
    "62": "Informatique & Tech", "63": "Informatique & Tech",
    "86": "Santé", "87": "Santé",
    "47": "Commerce & Retail", "46": "Commerce & Retail",
    "56": "Restauration & Hôtellerie", "55": "Restauration & Hôtellerie",
    "10": "Industrie & Manufacture", "25": "Industrie & Manufacture",
    "49": "Transport & Logistique", "52": "Transport & Logistique",
    "85": "Éducation & Formation",
    "69": "Juridique & Conseil", "70": "Juridique & Conseil",
}

def get_supabase() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_KEY)

def get_auth():
    return HTTPBasicAuth(ZEFIX_USERNAME, ZEFIX_PASSWORD)

def get_secteur(noga):
    if noga and len(str(noga)) >= 2:
        return SECTEURS.get(str(noga)[:2], "Autre")
    return "Autre"

def fetch_zefix(canton: str, offset: int = 0, max_entries: int = 100) -> list:
    """
    Recherche les entreprises actives d'un canton via l'API Zefix authentifiée.
    """
    url = f"{ZEFIX_BASE_URL}/firm/search.json"
    payload = {
        "name": "",
        "languageKey": "fr",
        "canton": canton,
        "activeOnly": True,
        "maxEntries": max_entries,
        "offset": offset,
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    try:
        r = requests.post(url, json=payload, headers=headers, auth=get_auth(), timeout=30)
        if r.status_code == 200:
            return r.json().get("list", [])
        else:
            print(f"  ⚠ Zefix ({canton}, offset {offset}): status {r.status_code}")
            print(f"    Détail: {r.text[:200]}")
            return []
    except Exception as e:
        print(f"  ⚠ Zefix ({canton}): {e}")
        return []

def fetch_company_details(ehraid: str) -> dict:
    """
    Récupère les détails complets d'une entreprise (incluant NOGA, adresse complète).
    """
    if not ehraid:
        return {}
    url = f"{ZEFIX_BASE_URL}/company/{ehraid}"
    try:
        r = requests.get(url, auth=get_auth(), timeout=15)
        if r.status_code == 200:
            return r.json()
        return {}
    except:
        return {}

def existe_deja(supabase: Client, numero_ide: str, nom: str) -> bool:
    if numero_ide:
        res = supabase.table("entreprises").select("id").eq("numero_ide", numero_ide).execute()
        if res.data:
            return True
    res = supabase.table("entreprises").select("id").eq("nom", nom).execute()
    return len(res.data) > 0

def format_entreprise(firm: dict, canton_nom: str) -> dict:
    """Formate une entreprise depuis la réponse Zefix."""
    adresse = firm.get("address", {})
    noga = ""
    # Le code NOGA peut être dans différents champs selon la version
    if "purpose" in firm:
        noga = firm.get("nogaCode", "")
    
    return {
        "nom": firm.get("name", "").strip(),
        "adresse": adresse.get("street", "") or adresse.get("addressLine1", ""),
        "npa": str(adresse.get("swissZipCode", "")),
        "ville": adresse.get("town", "") or adresse.get("city", ""),
        "canton": canton_nom,
        "secteur": get_secteur(noga),
        "numero_ide": firm.get("uid", ""),
        "source": "zefix.admin.ch",
        "mis_a_jour": datetime.utcnow().isoformat(),
    }

def inserer(supabase: Client, entreprises: list) -> int:
    if not entreprises:
        return 0
    try:
        supabase.table("entreprises").insert(entreprises).execute()
        return len(entreprises)
    except Exception as e:
        print(f"  ⚠ Erreur insertion: {e}")
        return 0

def scraper_canton(supabase: Client, canton: str, canton_nom: str) -> int:
    print(f"\n📍 Canton {canton_nom}...")
    offset = 0
    total_ajoute = 0
    max_entries = 100
    max_iterations = 200  # Limite de sécurité

    iteration = 0
    while iteration < max_iterations:
        firms = fetch_zefix(canton, offset, max_entries)
        if not firms:
            break

        nouvelles = []
        for firm in firms:
            ide = firm.get("uid", "")
            nom = firm.get("name", "").strip()
            if not nom:
                continue
            if existe_deja(supabase, ide, nom):
                continue
            e = format_entreprise(firm, canton_nom)
            nouvelles.append(e)

        ajoute = inserer(supabase, nouvelles)
        total_ajoute += ajoute
        print(f"  Lot {iteration + 1} (offset {offset}): +{ajoute} ({total_ajoute} total)")

        if len(firms) < max_entries:
            break

        offset += max_entries
        iteration += 1
        time.sleep(0.5)

    print(f"  ✅ {total_ajoute} entreprises ajoutées pour {canton_nom}")
    return total_ajoute

def main():
    print("=" * 50)
    print("🇨🇭 ANNUAIRE ROMAND — Scraping Zefix authentifié")
    print(f"   Démarrage : {datetime.now().strftime('%d.%m.%Y %H:%M')}")
    print("=" * 50)

    if not ZEFIX_USERNAME or not ZEFIX_PASSWORD:
        print("❌ ERREUR : ZEFIX_USERNAME ou ZEFIX_PASSWORD manquant !")
        return

    supabase = get_supabase()
    grand_total = 0

    for canton, canton_nom in CANTONS_ROMANDS.items():
        total = scraper_canton(supabase, canton, canton_nom)
        grand_total += total
        time.sleep(2)

    print("\n" + "=" * 50)
    print(f"✅ Terminé — {grand_total} entreprises ajoutées au total")
    print("=" * 50)

if __name__ == "__main__":
    main()
