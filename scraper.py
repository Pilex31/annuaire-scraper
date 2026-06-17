"""
ANNUAIRE ROMAND — Agent de scraping v7 (volume 1000/jour)
─────────────────────────────────────────────────
Basé sur v6 (sécurisé). Seuls les plafonds ont été relevés :
  - MAX_ENRICHISSEMENTS : 100 → 1000 (objectif 1000 fiches/run)
  - MAX_REQUETES_PAR_RUN : 120 → 1150 (marge pour Flux 1 + Flux 2)
  - MAX_NOUVELLES_PAR_RUN : 20 → 100 (Flux 2 SOGC)
  - SEUIL_ANTI_DOUBLE_RUN : 50 → 1100 (cohérent avec le nouveau volume)
  - DELAI_ENTRE_REQUETES gardé à 3.0s (sécurité anti-blocage Zefix)

⚠️ Durée estimée d'un run : ~50-60 min (1000 req × 3s).
   Le garde-fou 429 (rate limit Zefix) reste actif : si Zefix bloque,
   le run s'arrête proprement sans rien casser.
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

DELAI_ENTRE_REQUETES = 3.0
MAX_REQUETES_PAR_RUN = 1150   # marge pour 1000 enrichissements + Flux 2
MAX_ENRICHISSEMENTS = 1000    # objectif : 1000 fiches enrichies par run

# ── Garde-fous v7 pour le Flux 2 (SOGC) ──
MAX_NOUVELLES_PAR_RUN = 100   # Jamais plus de 100 nouvelles entreprises par run
SEUIL_ANTI_DOUBLE_RUN = 1100  # Si déjà >1100 créées aujourd'hui, on saute le Flux 2

CANTONS_ROMANDS = {"GE", "VD", "VS", "FR", "NE", "JU", "BE"}
CANTONS_NOMS = {
    "GE": "Genève", "VD": "Vaud", "VS": "Valais",
    "FR": "Fribourg", "NE": "Neuchâtel", "JU": "Jura", "BE": "Berne",
}

compteur_requetes = 0


# ═══════════════════════════════════════════════════════
# UTILITAIRES
# ═══════════════════════════════════════════════════════

def get_supabase() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def get_auth():
    return HTTPBasicAuth(ZEFIX_USERNAME, ZEFIX_PASSWORD)


def log(msg: str):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def attendre():
    time.sleep(DELAI_ENTRE_REQUETES)


def call_zefix(method: str, endpoint: str, payload: dict = None):
    global compteur_requetes
    
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
        
        if r.status_code == 429:
            log(f"⛔ ZEFIX RATE LIMIT (429) ! Arrêt complet.")
            sys.exit(0)
        
        if r.status_code == 200:
            return r.json()
        
        if r.status_code == 404:
            return None
        
        log(f"⚠ {endpoint} → status {r.status_code}: {r.text[:200]}")
        return None
        
    except requests.Timeout:
        log(f"⚠ Timeout sur {endpoint}")
        return None
    except Exception as e:
        log(f"⚠ Erreur {endpoint}: {e}")
        return None


# ═══════════════════════════════════════════════════════
# EXTRACTION DES DONNÉES (corrigée pour le vrai format Zefix)
# ═══════════════════════════════════════════════════════

def extraire_donnees(response) -> dict:
    """
    Extrait les champs utiles depuis la réponse Zefix.
    La réponse est TOUJOURS une liste [{...}], on prend le premier élément.
    """
    if not response:
        return {}
    
    # Déballer la liste si c'est une liste
    if isinstance(response, list):
        if not response:
            return {}
        firm = response[0]
    else:
        firm = response
    
    if not isinstance(firm, dict):
        return {}
    
    # ─── Adresse ───
    addr = firm.get("address") or {}
    street = addr.get("street", "") or ""
    house_number = addr.get("houseNumber", "") or ""
    adresse_complete = f"{street} {house_number}".strip()
    
    npa = str(addr.get("swissZipCode", "") or "")
    ville_addr = addr.get("city", "") or ""
    
    # ─── Forme juridique (français) ───
    legal_form = firm.get("legalForm") or {}
    form_names = legal_form.get("name") or {}
    forme_jur = ""
    if isinstance(form_names, dict):
        forme_jur = form_names.get("fr", "") or form_names.get("de", "")
    
    # ─── Capital ───
    capital = firm.get("capitalNominal")
    
    # ─── Date d'inscription ───
    sogc_date = firm.get("sogcDate")
    
    # ─── Statut ───
    statut = firm.get("status", "ACTIVE")
    
    # ─── But social (purpose) ───
    purpose = firm.get("purpose", "") or ""
    
    # ─── Canton ───
    canton_code = firm.get("canton", "")
    canton_nom = CANTONS_NOMS.get(canton_code, "")
    
    # ─── EHRAID ───
    ehraid = firm.get("ehraid")
    ehraid_str = str(ehraid) if ehraid else ""
    
    donnees = {
        "adresse": adresse_complete,
        "npa": npa,
        "ville": ville_addr,
        "but_social": purpose[:2000] if purpose else "",  # Limite raisonnable
        "forme_juridique": forme_jur,
        "capital_chf": capital,
        "date_inscription": sogc_date,
        "statut": statut,
        "ehraid": ehraid_str,
        "enrichie": True,
        "date_enrichissement": datetime.now(timezone.utc).isoformat(),
    }
    
    # Mise à jour du canton seulement si on a une valeur valide
    if canton_nom:
        donnees["canton"] = canton_nom
    
    return donnees


# ═══════════════════════════════════════════════════════
# FLUX 1 — ENRICHISSEMENT DES FICHES
# ═══════════════════════════════════════════════════════

def recuperer_a_enrichir(supabase: Client, limite: int) -> list:
    """
    Récupère les entreprises non encore enrichies.
    On récupère AUSSI celles qui ont enrichie=true mais adresse vide
    (les ratées du précédent run).
    """
    # Priorité 1 : celles jamais traitées
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


def recuperer_ratees(supabase: Client, limite: int) -> list:
    """
    Récupère les entreprises marquées enrichie=true mais sans adresse (ratées).
    Compatible supabase 1.2.0 : on récupère les fiches enrichies avec
    adresse vide, puis on filtre les NULL côté Python.
    """
    ratees = []

    # Cas 1 : adresse = chaîne vide
    try:
        res_vide = (
            supabase.table("entreprises")
            .select("id, nom, numero_ide, adresse")
            .eq("enrichie", True)
            .eq("adresse", "")
            .not_.is_("numero_ide", "null")
            .neq("numero_ide", "")
            .limit(limite)
            .execute()
        )
        ratees.extend(res_vide.data or [])
    except Exception as e:
        log(f"  ⚠ recuperer_ratees (vide): {str(e)[:80]}")

    # Cas 2 : adresse IS NULL (si on a encore de la place)
    if len(ratees) < limite:
        try:
            res_null = (
                supabase.table("entreprises")
                .select("id, nom, numero_ide, adresse")
                .eq("enrichie", True)
                .is_("adresse", "null")
                .not_.is_("numero_ide", "null")
                .neq("numero_ide", "")
                .limit(limite - len(ratees))
                .execute()
            )
            ratees.extend(res_null.data or [])
        except Exception as e:
            log(f"  ⚠ recuperer_ratees (null): {str(e)[:80]}")

    # Nettoyer : on enlève la clé "adresse" qu'on a ajoutée juste pour le filtre
    for r in ratees:
        r.pop("adresse", None)

    return ratees[:limite]


def enrichir_entreprise(uid: str):
    """Appelle /company/uid/{uid} pour récupérer les détails."""
    uid_clean = uid.replace(".", "").replace("-", "")
    return call_zefix("GET", f"/company/uid/{uid_clean}")


def mettre_a_jour(supabase: Client, id_entreprise, donnees: dict) -> bool:
    try:
        supabase.table("entreprises").update(donnees).eq("id", id_entreprise).execute()
        return True
    except Exception as e:
        log(f"  ⚠ Erreur update: {str(e)[:100]}")
        return False


def flux_1_enrichissement(supabase: Client) -> tuple:
    """
    Enrichit en priorité les fiches ratées du précédent run,
    puis les fiches jamais traitées.
    Retourne (enrichies, ratees_recuperees).
    """
    log("=" * 50)
    log(f"📋 FLUX 1 — Enrichissement (priorité aux ratées du run précédent)")
    log("=" * 50)
    
    # D'abord les ratées
    ratees = recuperer_ratees(supabase, MAX_ENRICHISSEMENTS)
    log(f"  Fiches ratées au run précédent à récupérer : {len(ratees)}")
    
    # Compléter avec les jamais traitées si on a du budget
    budget_restant = MAX_ENRICHISSEMENTS - len(ratees)
    nouvelles = []
    if budget_restant > 0:
        nouvelles = recuperer_a_enrichir(supabase, budget_restant)
        log(f"  Fiches jamais traitées à enrichir : {len(nouvelles)}")
    
    entreprises = ratees + nouvelles
    if not entreprises:
        log("✅ Toutes les entreprises sont enrichies correctement !")
        return 0, 0
    
    log(f"  Total à traiter ce run : {len(entreprises)}")
    
    enrichies = 0
    ratees_recuperees = 0
    
    for i, ent in enumerate(entreprises, 1):
        uid = ent["numero_ide"]
        nom = (ent["nom"] or "")[:50]
        is_ratee = i <= len(ratees)
        prefix = "🔄" if is_ratee else "  "
        
        log(f"  {prefix} [{i}/{len(entreprises)}] {nom} ({uid})")
        
        response = enrichir_entreprise(uid)
        if response:
            donnees = extraire_donnees(response)
            if donnees and donnees.get("adresse"):
                # Vraie réussite : on a au moins l'adresse
                if mettre_a_jour(supabase, ent["id"], donnees):
                    enrichies += 1
                    if is_ratee:
                        ratees_recuperees += 1
                    log(f"        ✓ {donnees.get('adresse', '')[:40]}, {donnees.get('npa', '')} {donnees.get('ville', '')}")
            else:
                log(f"        ⚠ Données extraites vides")
                # On marque comme tenté mais on ne réessaie pas
                if not is_ratee:
                    mettre_a_jour(supabase, ent["id"], {
                        "enrichie": True,
                        "date_enrichissement": datetime.now(timezone.utc).isoformat(),
                    })
        else:
            log(f"        ✗ Aucune réponse de l'API")
            if not is_ratee:
                mettre_a_jour(supabase, ent["id"], {
                    "enrichie": True,
                    "date_enrichissement": datetime.now(timezone.utc).isoformat(),
                })
        
        attendre()
    
    log(f"  ✅ {enrichies} entreprises enrichies (dont {ratees_recuperees} ratées récupérées)")
    return enrichies, ratees_recuperees


# ═══════════════════════════════════════════════════════
# FLUX 2 — NOUVELLES INSCRIPTIONS (SOGC)
# ═══════════════════════════════════════════════════════

def compter_creees_aujourdhui(supabase: Client) -> int:
    """
    Garde-fou anti-double-run : compte les entreprises créées aujourd'hui.
    Si le scraper a déjà tourné, ce nombre sera élevé.
    """
    aujourdhui = datetime.now().strftime("%Y-%m-%d")
    try:
        res = (
            supabase.table("entreprises")
            .select("id", count="exact")
            .gte("cree_le", f"{aujourdhui}T00:00:00")
            .execute()
        )
        return res.count or 0
    except Exception as e:
        log(f"  ⚠ Impossible de compter les créations du jour: {str(e)[:80]}")
        return 0


def flux_2_nouvelles(supabase: Client) -> int:
    log("=" * 50)
    log("📰 FLUX 2 — Nouvelles inscriptions du jour")
    log("=" * 50)

    # ── GARDE-FOU 1 : anti-runs-multiples ──
    deja_creees = compter_creees_aujourdhui(supabase)
    if deja_creees > SEUIL_ANTI_DOUBLE_RUN:
        log(f"  ⛔ {deja_creees} entreprises déjà créées aujourd'hui (seuil: {SEUIL_ANTI_DOUBLE_RUN})")
        log(f"  ⛔ Le scraper a déjà tourné aujourd'hui. Flux 2 SAUTÉ par sécurité.")
        return 0
    log(f"  ✓ {deja_creees} entreprises créées aujourd'hui (sous le seuil, on continue)")

    hier = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    log(f"  Recherche des publications du {hier}")

    data = call_zefix("GET", f"/sogc/bydate/{hier}")
    if not data:
        log("  ℹ Aucune publication trouvée")
        return 0

    publications = data if isinstance(data, list) else data.get("list", [])
    log(f"  Trouvé {len(publications)} publications au total")
    log(f"  Limite stricte : maximum {MAX_NOUVELLES_PAR_RUN} nouvelles ce run")

    ajoutees = 0
    examinees = 0

    for pub in publications:
        # ── GARDE-FOU 2 : limite dure sur le nombre de nouvelles ──
        if ajoutees >= MAX_NOUVELLES_PAR_RUN:
            log(f"  🛑 Limite de {MAX_NOUVELLES_PAR_RUN} nouvelles atteinte. Arrêt du Flux 2.")
            break

        # ── GARDE-FOU 3 : budget de requêtes Zefix épuisé → BREAK (pas continue) ──
        if compteur_requetes >= MAX_REQUETES_PAR_RUN:
            log(f"  🛑 Budget de requêtes épuisé ({compteur_requetes}). Arrêt du Flux 2.")
            break

        if not isinstance(pub, dict):
            continue

        canton_pub = (
            pub.get("registryOfCommerceCanton", "")
            or pub.get("canton", "")
        )
        if canton_pub not in CANTONS_ROMANDS:
            continue

        uid = pub.get("uid", "")
        if not uid:
            continue

        # Déjà en base ?
        existe = supabase.table("entreprises").select("id").eq("numero_ide", uid).execute()
        if existe.data:
            continue

        examinees += 1

        # Récupérer les détails complets
        response = enrichir_entreprise(uid)
        if not response:
            # Si c'est la limite de requêtes qui a renvoyé None, on arrête
            if compteur_requetes >= MAX_REQUETES_PAR_RUN:
                log(f"  🛑 Budget épuisé pendant l'enrichissement. Arrêt du Flux 2.")
                break
            continue

        donnees = extraire_donnees(response)
        if not donnees:
            continue

        firm = response[0] if isinstance(response, list) and response else response
        nom = firm.get("name", "") if isinstance(firm, dict) else ""

        donnees["nom"] = nom
        donnees["numero_ide"] = uid
        donnees["source"] = "zefix.admin.ch (SOGC)"

        try:
            supabase.table("entreprises").insert(donnees).execute()
            ajoutees += 1
            log(f"  ✨ [{ajoutees}/{MAX_NOUVELLES_PAR_RUN}] {nom[:50]} ({canton_pub})")
        except Exception as e:
            log(f"  ⚠ Erreur insertion: {str(e)[:100]}")

        attendre()

    log(f"  ✅ {ajoutees} nouvelles entreprises ajoutées ({examinees} examinées)")
    return ajoutees


# ═══════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════

def main():
    log("=" * 50)
    log("🇨🇭 ANNUAIRE ROMAND — Scraper v6 (sécurisé)")
    log(f"   {datetime.now().strftime('%d.%m.%Y %H:%M')}")
    log(f"   Max {MAX_REQUETES_PAR_RUN} req Zefix, {DELAI_ENTRE_REQUETES}s entre")
    log(f"   Flux 2 plafonné à {MAX_NOUVELLES_PAR_RUN} nouvelles/run")
    log("=" * 50)

    if not all([SUPABASE_URL, SUPABASE_KEY, ZEFIX_USERNAME, ZEFIX_PASSWORD]):
        log("❌ Variables d'environnement manquantes !")
        return

    supabase = get_supabase()

    # FLUX 1 — Enrichissement
    enrichies, ratees_recup = flux_1_enrichissement(supabase)

    # FLUX 2 — Nouvelles (si budget restant)
    if compteur_requetes < MAX_REQUETES_PAR_RUN - 20:
        nouvelles = flux_2_nouvelles(supabase)
    else:
        log("⏭ Flux 2 sauté (budget de requêtes presque épuisé)")
        nouvelles = 0

    # Rapport
    log("=" * 50)
    log("📊 RAPPORT")
    log(f"   Enrichies ce run : {enrichies}")
    log(f"   Dont ratées récupérées : {ratees_recup}")
    log(f"   Nouvelles ajoutées : {nouvelles}")
    log(f"   Requêtes Zefix : {compteur_requetes}/{MAX_REQUETES_PAR_RUN}")
    log("=" * 50)
    log("✅ Terminé proprement")


if __name__ == "__main__":
    main()
