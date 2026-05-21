"""
ANNUAIRE ROMAND — Agent de scraping v4 (respectueux)
─────────────────────────────────────────────────────
Stratégie :
  1. Enrichir 100 entreprises existantes par jour (/company/uid/{uid})
  2. Récupérer les nouvelles publications SOGC de la veille
  3. Classifier les secteurs via Claude API (optionnel)

Garde-fous :
  - 3 secondes minimum entre chaque requête Zefix
  - Maximum 120 requêtes Zefix par exécution
  - Arrêt automatique si erreur 429 (rate limit)
  - Logs détaillés
"""

import os
import time
import sys
from datetime import datetime, timedelta, timezone
import requests
from requests.auth import HTTPBasicAuth
from supabase import create_client, Client

# ═══════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
ZEFIX_USERNAME = os.environ.get("ZEFIX_USERNAME")
ZEFIX_PASSWORD = os.environ.get("ZEFIX_PASSWORD")

ZEFIX_BASE = "https://www.zefix.admin.ch/ZefixPublicREST/api/v1"

# Garde-fous
DELAI_ENTRE_REQUETES = 3.0      # 3 secondes minimum
MAX_REQUETES_PAR_RUN = 120      # Limite dure
MAX_ENRICHISSEMENTS = 100       # Nombre d'entreprises à enrichir par jour

# Cantons romands (pour le filtre SOGC)
CANTONS_ROMANDS = {"GE", "VD", "VS", "FR", "NE", "JU", "BE"}
CANTONS_NOMS = {
    "GE": "Genève", "VD": "Vaud", "VS": "Valais",
    "FR": "Fribourg", "NE": "Neuchâtel", "JU": "Jura", "BE": "Berne",
}

# Compteur global de requêtes
compteur_requetes = 0


# ═══════════════════════════════════════════════════════
# UTILITAIRES
# ═══════════════════════════════════════════════════════

def get_supabase() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def get_auth():
    return HTTPBasicAuth(ZEFIX_USERNAME, ZEFIX_PASSWORD)


def log(msg: str):
    """Log avec horodatage."""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def attendre():
    """Attente entre requêtes pour respecter le rate limit."""
    time.sleep(DELAI_ENTRE_REQUETES)


def call_zefix(method: str, endpoint: str, payload: dict = None) -> dict:
    """
    Appel sécurisé à l'API Zefix avec garde-fous.
    Retourne None si erreur, dict si succès.
    """
    global compteur_requetes
    
    # Garde-fou : limite dure
    if compteur_requetes >= MAX_REQUETES_PAR_RUN:
        log(f"🛑 LIMITE ATTEINTE ({MAX_REQUETES_PAR_RUN} requêtes). Arrêt.")
        return None
    
    url = f"{ZEFIX_BASE}{endpoint}"
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    
    try:
        if method == "GET":
            r = requests.get(url, headers=headers, auth=get_auth(), timeout=30)
        else:
            r = requests.post(url, json=payload, headers=headers, auth=get_auth(), timeout=30)
        
        compteur_requetes += 1
        
        # Rate limit détecté → arrêt immédiat
        if r.status_code == 429:
            log(f"⛔ ZEFIX RATE LIMIT (429) ! Arrêt complet du scraper.")
            sys.exit(0)
        
        if r.status_code == 200:
            return r.json()
        
        if r.status_code == 404:
            return None  # Pas grave, entreprise pas trouvée
        
        log(f"⚠ {endpoint} → status {r.status_code}: {r.text[:200]}")
        return None
        
    except requests.Timeout:
        log(f"⚠ Timeout sur {endpoint}")
        return None
    except Exception as e:
        log(f"⚠ Erreur {endpoint}: {e}")
        return None


# ═══════════════════════════════════════════════════════
# FLUX 1 — ENRICHISSEMENT DES FICHES EXISTANTES
# ═══════════════════════════════════════════════════════

def recuperer_a_enrichir(supabase: Client, limite: int) -> list:
    """Récupère les entreprises non encore enrichies."""
    res = (
        supabase.table("entreprises")
        .select("id, nom, numero_ide")
        .eq("enrichie", False)
        .not_.is_("numero_ide", "null")
        .neq("numero_ide", "")
        .limit(limite)
        .execute()
    )
    return res.data


def enrichir_entreprise(uid: str) -> dict:
    """Récupère les détails complets d'une entreprise via son UID."""
    # L'UID doit être au format CHE-XXX.XXX.XXX ou CHEXXXXXXXXX
    uid_clean = uid.replace(".", "").replace("-", "")
    return call_zefix("GET", f"/company/uid/{uid_clean}")


def extraire_donnees(firm: dict) -> dict:
    """Extrait les champs utiles depuis la réponse Zefix."""
    if not firm:
        return {}
    
    # La réponse peut être une liste (plusieurs résultats par UID)
    # ou un objet direct
    if isinstance(firm, list):
        if not firm:
            return {}
        firm = firm[0]
    
    # Adresse
    address = firm.get("address") or {}
    if isinstance(address, dict):
        rue = address.get("street", "") or ""
        numero = address.get("houseNumber", "") or ""
        adresse_complete = f"{rue} {numero}".strip()
        npa = str(address.get("swissZipCode", "") or "")
        ville_adresse = address.get("town", "") or address.get("city", "") or ""
    else:
        adresse_complete = ""
        npa = ""
        ville_adresse = ""
    
    # Forme juridique
    legal_form = firm.get("legalForm") or {}
    form_names = legal_form.get("name") or {}
    forme_jur = form_names.get("fr", "") if isinstance(form_names, dict) else ""
    
    # Capital
    capital = firm.get("capitalNominal") or firm.get("capital") or None
    
    # Date d'inscription (SOGC)
    sogc_date = firm.get("sogcDate") or None
    
    # Statut
    statut = firm.get("status", "ACTIVE")
    
    # But social (purpose) — peut être dans purpose ou purposeFr
    purpose = firm.get("purpose") or firm.get("purposeFr") or ""
    if isinstance(purpose, dict):
        purpose = purpose.get("fr", "") or purpose.get("de", "")
    
    return {
        "adresse": adresse_complete,
        "npa": npa,
        "ville": ville_adresse,
        "but_social": purpose,
        "forme_juridique": forme_jur,
        "capital_chf": capital,
        "date_inscription": sogc_date,
        "statut": statut,
        "ehraid": str(firm.get("ehraid", "")),
        "enrichie": True,
        "date_enrichissement": datetime.now(timezone.utc).isoformat(),
    }


def mettre_a_jour(supabase: Client, id_entreprise: str, donnees: dict):
    """Met à jour une entreprise dans Supabase."""
    try:
        supabase.table("entreprises").update(donnees).eq("id", id_entreprise).execute()
        return True
    except Exception as e:
        log(f"  ⚠ Erreur update: {str(e)[:100]}")
        return False


def flux_1_enrichissement(supabase: Client) -> int:
    """Enrichit jusqu'à MAX_ENRICHISSEMENTS entreprises."""
    log("=" * 50)
    log(f"📋 FLUX 1 — Enrichissement de {MAX_ENRICHISSEMENTS} fiches")
    log("=" * 50)
    
    entreprises = recuperer_a_enrichir(supabase, MAX_ENRICHISSEMENTS)
    if not entreprises:
        log("✅ Toutes les entreprises sont déjà enrichies !")
        return 0
    
    log(f"  Trouvé {len(entreprises)} entreprises à enrichir")
    
    enrichies = 0
    for i, ent in enumerate(entreprises, 1):
        uid = ent["numero_ide"]
        nom = ent["nom"][:50]
        
        log(f"  [{i}/{len(entreprises)}] {nom} ({uid})")
        
        firm = enrichir_entreprise(uid)
        if firm:
            donnees = extraire_donnees(firm)
            if donnees and mettre_a_jour(supabase, ent["id"], donnees):
                enrichies += 1
        else:
            # Marquer comme tentative (pour éviter de réessayer indéfiniment)
            mettre_a_jour(supabase, ent["id"], {
                "enrichie": True,
                "date_enrichissement": datetime.now(timezone.utc).isoformat(),
            })
        
        attendre()  # Rate limit
    
    log(f"  ✅ {enrichies} entreprises enrichies avec succès")
    return enrichies


# ═══════════════════════════════════════════════════════
# FLUX 2 — DÉCOUVERTE DES NOUVELLES INSCRIPTIONS (SOGC)
# ═══════════════════════════════════════════════════════

def flux_2_nouvelles(supabase: Client) -> int:
    """Récupère les nouvelles entreprises créées hier via SOGC."""
    log("=" * 50)
    log("📰 FLUX 2 — Nouvelles inscriptions du jour")
    log("=" * 50)
    
    hier = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    log(f"  Recherche des publications du {hier}")
    
    data = call_zefix("GET", f"/sogc/bydate/{hier}")
    if not data:
        log("  ℹ Aucune publication trouvée pour cette date")
        return 0
    
    if isinstance(data, dict):
        publications = data.get("list", []) or data.get("publications", [])
    else:
        publications = data or []
    
    log(f"  Trouvé {len(publications)} publications")
    
    ajoutees = 0
    for pub in publications:
        # Filtrer sur les cantons romands
        # (selon le format SOGC, le canton peut être dans différents champs)
        canton_pub = ""
        if isinstance(pub, dict):
            canton_pub = (
                pub.get("canton", "")
                or (pub.get("registryOfCommerce") or {}).get("canton", "")
                or ""
            )
        
        if canton_pub not in CANTONS_ROMANDS:
            continue
        
        uid = pub.get("uid", "") if isinstance(pub, dict) else ""
        if not uid:
            continue
        
        # Vérifier qu'elle n'est pas déjà en base
        existe = supabase.table("entreprises").select("id").eq("numero_ide", uid).execute()
        if existe.data:
            continue
        
        # Récupérer les détails complets
        firm = enrichir_entreprise(uid)
        if not firm:
            continue
        
        donnees = extraire_donnees(firm)
        nom = pub.get("name", "") if isinstance(pub, dict) else ""
        donnees["nom"] = nom
        donnees["canton"] = CANTONS_NOMS.get(canton_pub, canton_pub)
        donnees["numero_ide"] = uid
        donnees["source"] = "zefix.admin.ch (SOGC)"
        
        try:
            supabase.table("entreprises").insert(donnees).execute()
            ajoutees += 1
            log(f"  ✨ Nouvelle : {nom[:50]} ({canton_pub})")
        except Exception as e:
            log(f"  ⚠ Erreur insertion: {str(e)[:100]}")
        
        attendre()
    
    log(f"  ✅ {ajoutees} nouvelles entreprises ajoutées")
    return ajoutees


# ═══════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════

def main():
    log("=" * 50)
    log("🇨🇭 ANNUAIRE ROMAND — Scraper v4 (léger)")
    log(f"   {datetime.now().strftime('%d.%m.%Y %H:%M')}")
    log(f"   Limite : {MAX_REQUETES_PAR_RUN} req max, {DELAI_ENTRE_REQUETES}s entre")
    log("=" * 50)
    
    if not all([SUPABASE_URL, SUPABASE_KEY, ZEFIX_USERNAME, ZEFIX_PASSWORD]):
        log("❌ Variables d'environnement manquantes !")
        log(f"   SUPABASE_URL: {'OK' if SUPABASE_URL else 'MANQUANT'}")
        log(f"   SUPABASE_KEY: {'OK' if SUPABASE_KEY else 'MANQUANT'}")
        log(f"   ZEFIX_USERNAME: {'OK' if ZEFIX_USERNAME else 'MANQUANT'}")
        log(f"   ZEFIX_PASSWORD: {'OK' if ZEFIX_PASSWORD else 'MANQUANT'}")
        return
    
    supabase = get_supabase()
    
    # FLUX 1 : Enrichir l'existant
    enrichies = flux_1_enrichissement(supabase)
    
    # FLUX 2 : Récupérer les nouvelles (seulement s'il reste du budget)
    if compteur_requetes < MAX_REQUETES_PAR_RUN - 20:
        nouvelles = flux_2_nouvelles(supabase)
    else:
        log("⏭ Flux 2 sauté (budget de requêtes presque épuisé)")
        nouvelles = 0
    
    # Rapport final
    log("=" * 50)
    log(f"📊 RAPPORT")
    log(f"   Enrichies : {enrichies}")
    log(f"   Nouvelles : {nouvelles}")
    log(f"   Requêtes Zefix utilisées : {compteur_requetes}/{MAX_REQUETES_PAR_RUN}")
    log("=" * 50)
    log("✅ Terminé proprement")


if __name__ == "__main__":
    main()
