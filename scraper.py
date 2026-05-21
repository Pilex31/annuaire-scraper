"""
ANNUAIRE ROMAND — Agent de scraping Zefix v3
Format de réponse correct + recherche par suffixes courants
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

ZEFIX_BASE = "https://www.zefix.admin.ch/ZefixPublicREST/api/v1"

CANTONS_ROMANDS = {
    "GE": "Genève",
    "VD": "Vaud",
    "VS": "Valais",
    "FR": "Fribourg",
    "NE": "Neuchâtel",
    "JU": "Jura",
    "BE": "Berne",
}

# Préfixes/mots fréquents dans les noms d'entreprises suisses (min 3 chars)
# On combine les formes juridiques + les mots courants pour maximiser la couverture
TERMES_RECHERCHE = [
    "SA", "SARL", "AG", "GmbH",  # Formes juridiques courantes
    "and", "ass", "bel", "bon", "cap", "cer", "com", "con", "dom", "dur",
    "eco", "edi", "elec", "ent", "esp", "est", "eur", "fid", "fin", "fon",
    "gar", "gen", "ges", "gra", "gro", "hot", "ide", "imm", "ind", "ins",
    "int", "inv", "jar", "lac", "lau", "leg", "lib", "log", "mai", "man",
    "mar", "med", "men", "mer", "min", "mon", "mot", "nat", "neu", "nor",
    "off", "opt", "org", "par", "pat", "per", "pet", "pha", "pla", "pol",
    "pre", "pri", "pro", "pub", "qua", "rap", "ref", "reg", "res", "rev",
    "rou", "san", "sec", "ser", "sit", "sma", "soc", "sol", "spi", "sta",
    "stu", "sui", "sup", "sys", "tec", "tel", "tem", "ter", "the", "tra",
    "tri", "uni", "urb", "val", "ver", "via", "vil", "vin", "voy",
    # Suffixes anglais courants en Suisse
    "tech", "group", "services", "consulting", "solutions", "partners",
    "international", "swiss", "holding", "trading",
]

def get_supabase() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_KEY)

def get_auth():
    return HTTPBasicAuth(ZEFIX_USERNAME, ZEFIX_PASSWORD)

def search_companies(name_term: str, canton: str, max_entries: int = 100) -> list:
    """Recherche les entreprises via /company/search."""
    url = f"{ZEFIX_BASE}/company/search"
    payload = {
        "name": name_term,
        "languageKey": "fr",
        "canton": canton,
        "activeOnly": True,
        "maxEntries": max_entries,
        "offset": 0,
    }
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    try:
        r = requests.post(url, json=payload, headers=headers, auth=get_auth(), timeout=30)
        if r.status_code == 200:
            data = r.json()
            # La réponse est directement une liste (selon le schema)
            if isinstance(data, list):
                return data
            # Ou dans un champ "list"
            return data.get("list", [])
        if r.status_code != 404:
            print(f"  ⚠ {canton}/'{name_term}': status {r.status_code}")
        return []
    except Exception as e:
        print(f"  ⚠ Erreur {canton}/'{name_term}': {e}")
        return []

def existe_deja(supabase: Client, ide_or_uid: str) -> bool:
    if not ide_or_uid:
        return False
    res = supabase.table("entreprises").select("id").eq("numero_ide", ide_or_uid).execute()
    return len(res.data) > 0

def format_entreprise(firm: dict, canton_nom: str) -> dict:
    """Formate une entreprise selon le schema Zefix réel."""
    nom = firm.get("name", "").strip()
    uid = firm.get("uid", "")
    legal_seat = firm.get("legalSeat", "")  # Ville/commune
    
    # Forme juridique en français
    legal_form = firm.get("legalForm") or {}
    form_name = legal_form.get("name") or {}
    forme_jur = form_name.get("fr", "") if isinstance(form_name, dict) else ""
    
    return {
        "nom": nom,
        "adresse": "",  # Pas dans /search, faudrait /company/uid/{id} pour l'avoir
        "npa": "",
        "ville": legal_seat,
        "canton": canton_nom,
        "secteur": "Autre",  # Pas dans /search non plus
        "numero_ide": uid,
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
        print(f"  ⚠ Erreur insertion: {str(e)[:150]}")
        return 0

def scraper_canton(supabase: Client, canton: str, canton_nom: str) -> int:
    print(f"\n📍 Canton {canton_nom}...")
    total_ajoute = 0
    uids_vus = set()  # Dédoublonnage en mémoire
    
    for terme in TERMES_RECHERCHE:
        firms = search_companies(terme, canton, max_entries=100)
        if not firms:
            continue
        
        nouvelles = []
        for firm in firms:
            uid = firm.get("uid", "")
            if not uid or uid in uids_vus:
                continue
            uids_vus.add(uid)
            
            if existe_deja(supabase, uid):
                continue
            
            nom = firm.get("name", "").strip()
            if not nom:
                continue
            
            nouvelles.append(format_entreprise(firm, canton_nom))
        
        if nouvelles:
            ajoute = inserer(supabase, nouvelles)
            total_ajoute += ajoute
            print(f"  '{terme}' → {len(firms)} résultats, +{ajoute} nouvelles ({total_ajoute} total)")
        
        time.sleep(0.3)
    
    print(f"  ✅ {total_ajoute} entreprises ajoutées pour {canton_nom}")
    return total_ajoute

def main():
    print("=" * 50)
    print("🇨🇭 ANNUAIRE ROMAND — Scraping Zefix v3")
    print(f"   Démarrage : {datetime.now().strftime('%d.%m.%Y %H:%M')}")
    print("=" * 50)

    if not ZEFIX_USERNAME or not ZEFIX_PASSWORD:
        print("❌ ZEFIX_USERNAME ou ZEFIX_PASSWORD manquant !")
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
