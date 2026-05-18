"""
ANNUAIRE ROMAND — Agent de scraping
Source : SHAB (Journal officiel suisse du commerce)
Tourne automatiquement chaque nuit sur Railway.
"""

import os
import time
import requests
from supabase import create_client, Client
from datetime import datetime

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

CANTONS_ROMANDS = {
    "GE": "Genève",
    "VD": "Vaud",
    "VS": "Valais",
    "FR": "Fribourg",
    "NE": "Neuchâtel",
    "JU": "Jura",
    "BE": "Berne",
}

def get_supabase() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_KEY)

def fetch_shab(canton: str, page: int = 0) -> list:
    url = "https://www.shab.ch/api/v1/publications"
    params = {
        "cantons": canton,
        "rubrics": "HR01,HR02,HR03",
        "pageSize": 100,
        "page": page,
    }
    headers = {"Accept": "application/json", "User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(url, params=params, headers=headers, timeout=30)
        if r.status_code == 200:
            return r.json().get("content", [])
        print(f"  ⚠ SHAB ({canton}): status {r.status_code}")
        return []
    except Exception as e:
        print(f"  ⚠ SHAB ({canton}): {e}")
        return []

def existe_deja(supabase: Client, nom: str, ville: str) -> bool:
    res = supabase.table("entreprises").select("id").eq("nom", nom).eq("ville", ville).execute()
    return len(res.data) > 0

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
    total = 0
    page = 0
    while True:
        pubs = fetch_shab(canton, page)
        if not pubs:
            break
        nouvelles = []
        for pub in pubs:
            meta = pub.get("meta", {})
            nom = meta.get("legalName", "").strip() or pub.get("title", "").strip()
            if not nom:
                continue
            adresse = meta.get("address", {})
            ville = adresse.get("city", "")
            if existe_deja(supabase, nom, ville):
                continue
            nouvelles.append({
                "nom": nom,
                "adresse": adresse.get("street", ""),
                "npa": str(adresse.get("swissZipCode", "")),
                "ville": ville,
                "canton": canton_nom,
                "secteur": "Autre",
                "numero_ide": meta.get("uid", ""),
                "source": "shab.ch",
                "mis_a_jour": datetime.utcnow().isoformat(),
            })
        ajoute = inserer(supabase, nouvelles)
        total += ajoute
        print(f"    Page {page}: +{ajoute} ({total} total)")
        if len(pubs) < 100:
            break
        page += 1
        time.sleep(1)
    return total

def main():
    print("=" * 50)
    print("🇨🇭 ANNUAIRE ROMAND — Agent de scraping")
    print(f"   Démarrage : {datetime.now().strftime('%d.%m.%Y %H:%M')}")
    print("=" * 50)
    supabase = get_supabase()
    grand_total = 0
    for canton, canton_nom in CANTONS_ROMANDS.items():
        print(f"\n📍 {canton_nom}...")
        total = scraper_canton(supabase, canton, canton_nom)
        print(f"  ✅ {total} ajoutées pour {canton_nom}")
        grand_total += total
        time.sleep(2)
    print("\n" + "=" * 50)
    print(f"✅ Terminé — {grand_total} entreprises ajoutées au total")
    print("=" * 50)

if __name__ == "__main__":
    main()
