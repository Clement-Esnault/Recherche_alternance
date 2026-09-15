"""
Alternance Watcher — multi-sources (La Bonne Alternance + Adzuna + Jooble)
Check les nouvelles offres d'alternance et ping un webhook Discord.

Lancement manuel : python main.py
Lancement auto   : GitHub Actions (voir .github/workflows/watcher.yml)
"""

import os
import time
import sqlite3
import logging
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("watcher")

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
# Second webhook optionnel pour les offres de stage (si vide, tout part sur
# le webhook principal).
DISCORD_WEBHOOK_STAGE_URL = os.getenv("DISCORD_WEBHOOK_STAGE_URL") or DISCORD_WEBHOOK_URL
DB_PATH = os.path.join(os.path.dirname(__file__), "seen_offers.db")
PURGE_AFTER_DAYS = int(os.getenv("PURGE_AFTER_DAYS", "60"))
DIGEST_THRESHOLD = int(os.getenv("DIGEST_THRESHOLD", "8"))
DRY_RUN = os.getenv("DRY_RUN", "false").strip().lower() == "true"

CITY = os.getenv("CITY", "Caen")
LATITUDE = os.getenv("LATITUDE", "49.2667")
LONGITUDE = os.getenv("LONGITUDE", "-0.4500")
RADIUS_KM = os.getenv("RADIUS_KM", "40")
KEYWORDS = os.getenv("SEARCH_KEYWORDS", "alternance informatique développeur développement web mobile données système réseau")

MAX_RETRIES = 3
RETRY_BASE_DELAY = 2  # secondes, double à chaque tentative


def request_with_retry(method, url, **kwargs):
    """Requête HTTP avec retry/backoff sur erreurs réseau ou 5xx.
    Les erreurs 4xx (mauvaise clé, requête invalide) ne sont PAS retentées :
    réessayer ne changera rien, elles remontent telles quelles à l'appelant."""
    last_exc = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.request(method, url, **kwargs)
            if resp.status_code >= 500:
                raise requests.HTTPError(f"{resp.status_code} server error")
            return resp
        except requests.RequestException as e:
            last_exc = e
            if attempt < MAX_RETRIES:
                delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                log.warning("Requête %s échouée (%d/%d), retry dans %ds : %s", url, attempt, MAX_RETRIES, delay, e)
                time.sleep(delay)
    raise last_exc


# ---------- Source 1 : La Bonne Alternance ----------
LBA_API_BASE = "https://api.apprentissage.beta.gouv.fr/api"
LBA_SEARCH_PATH = "/job/v1/search"
LBA_TOKEN = os.getenv("LBA_API_TOKEN")
ROME_CODES = os.getenv("ROME_CODES", "M1805,M1802,M1801,M1810,M1806")
# L'API n'accepte qu'un seul niveau de diplôme par appel : on boucle sur
# chaque niveau demandé et on fusionne les résultats (dédupliqués par id).
TARGET_DIPLOMA_LEVELS = [l.strip() for l in os.getenv("TARGET_DIPLOMA_LEVELS", "5,6").split(",") if l.strip()]
DIPLOMA_LABELS = {"3": "CAP", "4": "Bac", "5": "Bac+2", "6": "Bac+3/Licence", "7": "Bac+5"}


def fetch_lba_offers():
    if not LBA_TOKEN:
        return []
    headers = {"Authorization": f"Bearer {LBA_TOKEN}"}
    by_id = {}
    for level in TARGET_DIPLOMA_LEVELS:
        params = {
            "romes": ROME_CODES,
            "radius": RADIUS_KM,
            "latitude": LATITUDE,
            "longitude": LONGITUDE,
            "target_diploma_level": level,
        }
        try:
            resp = request_with_retry("GET", LBA_API_BASE + LBA_SEARCH_PATH, params=params, headers=headers, timeout=20)
        except requests.RequestException as e:
            log.error("[LBA] échec après retries (niveau %s) : %s", level, e)
            continue

        if resp.status_code in (401, 403):
            log.error("[LBA] clé invalide ou expirée (LBA_API_TOKEN) — status %s", resp.status_code)
            return []
        if resp.status_code != 200 or not resp.text.strip():
            log.warning("[LBA] status=%s body=%r (niveau %s)", resp.status_code, resp.text[:500], level)
            continue
        data = resp.json()

        raw_offers = data.get("jobs", [])
        log.info("[LBA] niveau %s (%s) : %d offre(s)", level, DIPLOMA_LABELS.get(level, "?"), len(raw_offers))
        for o in raw_offers:
            ident = o.get("identifier", {})
            offer_id = ident.get("id") or ident.get("partner_job_id")
            diploma_code = (o.get("offer", {}).get("target_diploma") or {}).get("european")
            full_id = f"lba:{ident.get('partner_label', '')}:{offer_id}"
            by_id[full_id] = {
                "id": full_id,
                "title": o.get("offer", {}).get("title", "Offre alternance"),
                "company": o.get("workplace", {}).get("name", "Entreprise inconnue"),
                "city": o.get("workplace", {}).get("location", {}).get("address", ""),
                "url": o.get("apply", {}).get("url", ""),
                "phone": o.get("apply", {}).get("phone", ""),
                "diploma": DIPLOMA_LABELS.get(diploma_code, ""),
                "source": "La Bonne Alternance",
            }
    return list(by_id.values())


# ---------- Source 2 : Adzuna ----------
ADZUNA_APP_ID = os.getenv("ADZUNA_APP_ID")
ADZUNA_APP_KEY = os.getenv("ADZUNA_APP_KEY")


def fetch_adzuna_offers():
    if not ADZUNA_APP_ID or not ADZUNA_APP_KEY:
        return []
    url = "https://api.adzuna.com/v1/api/jobs/fr/search/1"
    params = {
        "app_id": ADZUNA_APP_ID,
        "app_key": ADZUNA_APP_KEY,
        "results_per_page": 50,
        "category": "it-jobs",
        "what_or": "alternance stage",
        "where": CITY,
        "distance": RADIUS_KM,
        "content-type": "application/json",
    }
    try:
        resp = request_with_retry("GET", url, params=params, timeout=20)
    except requests.RequestException as e:
        log.error("[Adzuna] échec après retries : %s", e)
        return []

    if resp.status_code in (401, 403):
        log.error("[Adzuna] clé invalide (ADZUNA_APP_ID/ADZUNA_APP_KEY) — status %s", resp.status_code)
        return []
    try:
        data = resp.json()
    except ValueError:
        log.warning("[Adzuna] réponse non-JSON, status=%s", resp.status_code)
        return []

    log.info("[Adzuna] %d résultat(s) (total dispo: %s)", len(data.get("results", [])), data.get("count"))
    normalized = []
    for o in data.get("results", []):
        normalized.append({
            "id": f"adzuna:{o.get('id')}",
            "title": o.get("title", "Offre alternance"),
            "company": o.get("company", {}).get("display_name", "Entreprise inconnue"),
            "city": o.get("location", {}).get("display_name", ""),
            "url": o.get("redirect_url", ""),
            "source": "Adzuna",
        })
    return normalized


# ---------- Source 3 : Jooble ----------
JOOBLE_API_KEY = os.getenv("JOOBLE_API_KEY")


def fetch_jooble_offers():
    if not JOOBLE_API_KEY:
        return []
    url = f"https://jooble.org/api/{JOOBLE_API_KEY}"
    payload = {"keywords": "alternance stage informatique développeur", "location": f"{CITY}, France", "radius": int(RADIUS_KM)}
    try:
        resp = request_with_retry("POST", url, json=payload, timeout=20)
    except requests.RequestException as e:
        log.error("[Jooble] échec après retries : %s", e)
        return []

    if resp.status_code in (401, 403):
        log.error("[Jooble] clé invalide (JOOBLE_API_KEY) — status %s", resp.status_code)
        return []
    try:
        data = resp.json()
    except ValueError:
        log.warning("[Jooble] réponse non-JSON, status=%s", resp.status_code)
        return []

    log.info("[Jooble] %d résultat(s) (total dispo: %s)", len(data.get("jobs", [])), data.get("totalCount"))
    normalized = []
    for o in data.get("jobs", []):
        normalized.append({
            "id": f"jooble:{o.get('id')}",
            "title": o.get("title", "Offre alternance"),
            "company": o.get("company", "Entreprise inconnue"),
            "city": o.get("location", ""),
            "url": o.get("link", ""),
            "source": f"Jooble via {o.get('source', '?')}",
        })
    return normalized


# ---------- Base de données ----------

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS seen (id TEXT PRIMARY KEY)")
    # Migration : ajoute les colonnes manquantes si la base vient d'un cache
    # créé par une version antérieure du script (ex: schéma id-only).
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(seen)")}
    for col in ("title", "company", "source", "first_seen"):
        if col not in existing_cols:
            conn.execute(f"ALTER TABLE seen ADD COLUMN {col} TEXT")
            log.info("Migration DB : colonne '%s' ajoutée à seen_offers.db", col)
    conn.commit()
    return conn


def purge_old_entries(conn):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=PURGE_AFTER_DAYS)).isoformat()
    cur = conn.execute("DELETE FROM seen WHERE first_seen < ?", (cutoff,))
    if cur.rowcount:
        log.info("Purge : %d entrée(s) de plus de %d jours supprimée(s)", cur.rowcount, PURGE_AFTER_DAYS)
    conn.commit()


# ---------- Discord ----------

def send_discord_notification(offer: dict, webhook_url: str):
    extra = [f"Type : {offer.get('type_label', '?')}"]
    if offer.get("diploma"):
        extra.append(f"Niveau visé : {offer['diploma']}")
    if offer.get("phone"):
        extra.append(f"Tél : {offer['phone']}")
    description = f"**{offer['company']}**\n{offer['city']}\n" + " · ".join(extra)
    payload = {
        "embeds": [{
            "title": offer["title"],
            "description": description,
            "url": offer["url"] or None,
            "footer": {"text": offer["source"]},
            "color": 5814783,
        }]
    }
    try:
        resp = request_with_retry("POST", webhook_url, json=payload, timeout=10)
        if resp.status_code >= 300:
            log.warning("[Discord] status=%s body=%s", resp.status_code, resp.text[:300])
    except requests.RequestException as e:
        log.error("[Discord] échec après retries : %s", e)


def send_discord_digest(offers: list, webhook_url: str, title: str):
    # Un embed Discord ne supporte que 25 champs max : on découpe en paquets.
    for i in range(0, len(offers), 25):
        chunk = offers[i:i + 25]
        fields = [
            {
                "name": o["title"][:256],
                "value": (
                    f"{o['company']} — {o['city']}"
                    + (f" · {o['diploma']}" if o.get("diploma") else "")
                    + f"\n🏷️ {o.get('type_label', '?')}"
                    + f"\n[Voir l'offre]({o['url']}) · _{o['source']}_"
                )[:1024],
                "inline": False,
            }
            for o in chunk
        ]
        payload = {
            "embeds": [{
                "title": title,
                "color": 5814783,
                "fields": fields,
            }]
        }
        try:
            resp = request_with_retry("POST", webhook_url, json=payload, timeout=10)
            if resp.status_code >= 300:
                log.warning("[Discord] status=%s body=%s", resp.status_code, resp.text[:300])
        except requests.RequestException as e:
            log.error("[Discord] échec après retries : %s", e)


def notify_group(offers: list, webhook_url: str, label: str):
    if not offers:
        return
    if len(offers) > DIGEST_THRESHOLD:
        log.info("Envoi d'un digest groupé — %s (%d offres)", label, len(offers))
        send_discord_digest(offers, webhook_url, title=f"{len(offers)} nouvelles offres — {label}")
    else:
        for offer in offers:
            send_discord_notification(offer, webhook_url)


# ---------- Main ----------

def main():
    if not DISCORD_WEBHOOK_URL:
        raise SystemExit("Configure DISCORD_WEBHOOK_URL dans .env")

    if DRY_RUN:
        log.info("=== DRY_RUN activé : aucune notif Discord ne sera envoyée, seen_offers.db ne sera pas modifiée ===")

    conn = init_db()
    if not DRY_RUN:
        purge_old_entries(conn)
    seen_ids = {row[0] for row in conn.execute("SELECT id FROM seen")}

    all_offers = fetch_lba_offers() + fetch_adzuna_offers() + fetch_jooble_offers()
    for o in all_offers:
        log.debug("[raw] %s — %s", o["title"], o["source"])

    # LBA est déjà filtré finement (ROME + diplôme), Adzuna déjà filtré par
    # son propre paramètre what="alternance" (le mot apparaît dans l'annonce,
    # pas forcément dans le titre) : on leur fait confiance tels quels.
    # Jooble a une recherche plein-texte peu fiable : on exige que le titre
    # contienne "alternance" ou "apprenti" pour éliminer les faux positifs
    # (infirmier, office manager, etc.).
    filtered_offers = []
    for offer in all_offers:
        if not offer["source"].lower().startswith("jooble"):
            filtered_offers.append(offer)
            continue
        title_lower = offer["title"].lower()
        if "alternance" in title_lower or "apprenti" in title_lower or "stage" in title_lower:
            filtered_offers.append(offer)

    new_offers = [o for o in filtered_offers if o["id"] not in seen_ids]

    # Route vers le channel "stage" les offres dont le titre mentionne un
    # stage sans mentionner alternance/apprentissage ; le reste va sur le
    # channel principal.
    stage_offers, main_offers = [], []
    for o in new_offers:
        title_lower = o["title"].lower()
        is_stage_only = "stage" in title_lower and "alternance" not in title_lower and "apprenti" not in title_lower
        o["type_label"] = "Stage" if is_stage_only else "Alternance"
        (stage_offers if is_stage_only else main_offers).append(o)

    if new_offers:
        log.info("Répartition : %d alternance, %d stage", len(main_offers), len(stage_offers))
        if DRY_RUN:
            log.info("[DRY_RUN] %d offre(s) alternance, %d offre(s) stage — rien n'est envoyé :", len(main_offers), len(stage_offers))
            for o in new_offers:
                log.info("  - %s — %s (%s)", o["title"], o["company"], o["source"])
        else:
            notify_group(main_offers, DISCORD_WEBHOOK_URL, "Alternance")
            notify_group(stage_offers, DISCORD_WEBHOOK_STAGE_URL, "Stage")

            now = datetime.now(timezone.utc).isoformat()
            conn.executemany(
                "INSERT OR IGNORE INTO seen (id, title, company, source, first_seen) VALUES (?, ?, ?, ?, ?)",
                [(o["id"], o["title"], o["company"], o["source"], now) for o in new_offers],
            )
            conn.commit()

    conn.close()
    log.info(
        "%d nouvelle(s) offre(s) sur %d pertinente(s) (%d brutes, toutes sources)",
        len(new_offers), len(filtered_offers), len(all_offers),
    )


if __name__ == "__main__":
    main()