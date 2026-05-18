"""
ANNUAIRE ROMAND — Agent de scraping
Utilise l'API officielle UID du gouvernement suisse
pour récupérer les entreprises de Suisse romande.
"""

import os
import time
import requests
from supabase import create_client, Client
from datetime import datetime

# ── Configuration ──────────────────────────────────────────────
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

# Cantons de Suisse romande avec leurs codes OFS
CANTONS_ROMANDS = {
    "GE": "Genève",
    "VD": "Vaud",
    "VS": "Valais",
    "FR": "Fribourg",
    "NE": "Neuchâtel",
    "JU": "Jura",
    "BE": "Berne",
}

# Mapping NOGA 2 chiffres → secteur
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

# ── Appel API UID admin.ch ─────────────────────────────────────
def fetch_uid_api(canton: str, offset: int = 0, limit: int = 500) -> list:
    """
    API SOAP/REST officielle du registre UID suisse.
    Documentation: https://www.uid.admin.ch/
    """
    url = "https://www.uid.admin.ch/TecDocService/TecDocServices.svc/json/SearchByName"
    params = {
        "name": "*",
        "legalSeatCanton": canton,
        "maxEntries": limit,
        "firstPosition": offset,
        "validOnly": "true",
    }
    headers = {"Accept": "application/json"}
    try:
        r = requests.get(url, params=params, headers=headers, timeout=30)
        if r.status_code == 200:
            data = r.json()
            return data if isinstance(data, list) else []
        else:
            print(f"  ⚠ UID API ({canton}): status {r.status_code}")
            return []
    except Exception as e:
        print(f"  ⚠ UID API ({canton}): {e}")
        return []

# ── Fallback: recherche par nom générique sur Zefix ───────────
def fetch_zefix_v2(canton: str, offset: int = 0) -> list:
    """Nouvelle tentative Zefix avec format corrigé."""
    url = "https://www.zefix.ch/ZefixREST/api/v1/firm/search.json"
    payload = {
        "name": "",
        "languageKey": "fr",
        "canton": canton,
        "activeOnly": True,
        "maxEntries": 100,
        "offset": offset,
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0",
    }
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=30)
        if r.status_code == 200:
            data = r.json()
            return data.get("list", [])
        return []
    except Exception as e:
        print(f"  ⚠ Zefix v2 ({canton}): {e}")
        return []

# ── Formater depuis UID API ────────────────────────────────────
def format_uid(entry: dict, canton_nom: str) -> dict:
    adresse = entry.get("address", {})
    noga = entry.get("noga", "")
    secteur = SECTEURS.get(noga[:2], "Autre") if noga else "Autre"
    uid = entry.get("uid", {})
    numero = f"CHE-{uid.get('uidOrganisationId', '')}" if uid else ""
    return {
        "nom": entry.get("organisationName", "").strip(),
        "adresse": adresse.get("street", ""),
        "npa": str(adresse.get("zipCode", "")),
        "ville": adresse.get("town", ""),
        "canton": canton_nom,
        "secteur": secteur,
        "numero_ide": numero,
        "source": "uid.admin.ch",
        "mis_a_jour": datetime.utcnow().isoformat(),
    }

# ── Formater depuis Zefix ──────────────────────────────────────
def format_zefix(firm: dict, canton_nom: str) -> dict:
    adresse = firm.get("address", {})
    noga = firm.get("nogaCode", "")
    secteur = SECTEURS.get(noga[:2], "Autre") if noga else "Autre"
    return {
        "nom": firm.get("name", "").strip(),
        "adresse": adresse.get("street", ""),
        "npa": str(adresse.get("swissZipCode", "")),
        "ville": adresse.get("town", ""),
        "canton": canton_nom,
        "secteur": secteur,
        "numero_ide": firm.get("uid", ""),
        "source": "zefix.ch",
        "mis_a_jour": datetime.utcnow().isoformat(),
    }

# ── Vérifier doublon ───────────────────────────────────────────
def existe_deja(supabase: Client, numero_ide: str, nom: str) -> bool:
    if numero_ide:
        res = supabase.table("entreprises").select("id").eq("numero_ide", numero_ide).execute()
        if res.data:
            return True
    res = supabase.table("entreprises").select("id").eq("nom", nom).execute()
    return len(res.data) > 0

# ── Insérer par batch ──────────────────────────────────────────
def inserer(supabase: Client, entreprises: list) -> int:
    if not entreprises:
        return 0
    try:
        supabase.table("entreprises").insert(entreprises).execute()
        return len(entreprises)
    except Exception as e:
        print(f"  ⚠ Erreur insertion: {e}")
        return 0

# ── Scraper un canton ──────────────────────────────────────────
def scraper_canton(supabase: Client, canton: str, canton_nom: str) -> int:
    print(f"\n📍 Canton {canton_nom}...")
    total = 0

    # Essai 1 : Zefix v2
    firms = fetch_zefix_v2(canton)
    if firms:
        print(f"  Zefix: {len(firms)} entreprises trouvées")
        nouvelles = []
        for f in firms:
            e = format_zefix(f, canton_nom)
            if e["nom"] and not existe_deja(supabase, e["numero_ide"], e["nom"]):
                nouvelles.append(e)
        total += inserer(supabase, nouvelles)
        print(f"  ✅ {total} ajoutées via Zefix")
        return total

    # Essai 2 : UID admin.ch
    print(f"  Zefix indisponible, essai UID admin.ch...")
    entries = fetch_uid_api(canton)
    if entries:
        print(f"  UID API: {len(entries)} entreprises trouvées")
        nouvelles = []
        for e in entries:
            fmt = format_uid(e, canton_nom)
            if fmt["nom"] and not existe_deja(supabase, fmt["numero_ide"], fmt["nom"]):
                nouvelles.append(fmt)
        total += inserer(supabase, nouvelles)
        print(f"  ✅ {total} ajoutées via UID admin.ch")
        return total

    print(f"  ⚠ Aucune source disponible pour {canton_nom}")
    return 0

# ── Main ───────────────────────────────────────────────────────
def main():
    print("=" * 50)
    print("🇨🇭 ANNUAIRE ROMAND — Agent de scraping")
    print(f"   Démarrage : {datetime.now().strftime('%d.%m.%Y %H:%M')}")
    print("=" * 50)

    supabase = get_supabase()
    grand_total = 0

    for canton, canton_nom in CANTONS_ROMANDS.items():
        total = scraper_canton(supabase, canton, canton_nom)
        grand_total += total
        time.sleep(2)

    print("\n" + "=" * 50)
    print(f"✅ Terminé — {grand_total} entreprises ajoutées")
    print("=" * 50)

if __name__ == "__main__":
    main()
