"""
Alternance Watcher — multi-sources (La Bonne Alternance + Adzuna + Jooble)
Check les nouvelles offres d'alternance et ping un webhook Discord.

Lancement manuel : python main.py
Lancement auto   : cron (voir README.md)
"""

import os
import sqlite3
import requests
from dotenv import load_dotenv

load_dotenv()

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
DB_PATH = os.path.join(os.path.dirname(__file__), "seen_offers.db")

CITY = os.getenv("CITY", "Caen")
LATITUDE = os.getenv("LATITUDE", "49.2667")
LONGITUDE = os.getenv("LONGITUDE", "-0.4500")
RADIUS_KM = os.getenv("RADIUS_KM", "40")
KEYWORDS = os.getenv("SEARCH_KEYWORDS", "alternance informatique développeur développement web mobile données système réseau")

# ---------- Source 1 : La Bonne Alternance ----------
# Path exact a confirmer sur le swagger une fois le token en main :
# https://api.apprentissage.beta.gouv.fr/fr/documentation-technique#tag/Offre-Emploi/operation/jobSearch
LBA_API_BASE = "https://api.apprentissage.beta.gouv.fr/api"
LBA_SEARCH_PATH = "/job/v1/search"
LBA_TOKEN = os.getenv("LBA_API_TOKEN")
ROME_CODES = os.getenv("ROME_CODES", "M1805,M1802,M1801,M1810,M1806")
TARGET_DIPLOMA_LEVEL = os.getenv("TARGET_DIPLOMA_LEVEL", "6")  # 6 = Bac+3 (Licence/BUT3)


def fetch_lba_offers():
    if not LBA_TOKEN:
        return []
    params = {
        "romes": ROME_CODES,
        "radius": RADIUS_KM,
        "latitude": LATITUDE,
        "longitude": LONGITUDE,
        "target_diploma_level": TARGET_DIPLOMA_LEVEL,
    }
    headers = {"Authorization": f"Bearer {LBA_TOKEN}"}
    resp = requests.get(LBA_API_BASE + LBA_SEARCH_PATH, params=params, headers=headers, timeout=20)
    print(f"[LBA] status={resp.status_code} content-type={resp.headers.get('content-type')} len={len(resp.text)}")
    if resp.status_code != 200 or not resp.text.strip():
        print(f"[LBA] body={resp.text[:800]!r}")
        return []
    data = resp.json()

    raw_offers = data.get("jobs", [])
    print(f"[LBA] {len(raw_offers)} offre(s), recruiters={len(data.get('recruiters', []))}, warnings={data.get('warnings')}")
    normalized = []
    for o in raw_offers:
        ident = o.get("identifier", {})
        offer_id = ident.get("id") or ident.get("partner_job_id")
        normalized.append({
            "id": f"lba:{ident.get('partner_label', '')}:{offer_id}",
            "title": o.get("offer", {}).get("title", "Offre alternance"),
            "company": o.get("workplace", {}).get("name", "Entreprise inconnue"),
            "city": o.get("workplace", {}).get("location", {}).get("address", ""),
            "url": o.get("apply", {}).get("url", ""),
            "source": "La Bonne Alternance",
        })
    return normalized


# ---------- Source 2 : Adzuna ----------
# Cles gratuites : https://developer.adzuna.com/signup
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
        "what": "alternance",
        "where": CITY,
        "distance": RADIUS_KM,
        "content-type": "application/json",
    }
    try:
        resp = requests.get(url, params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        print(f"[Adzuna] erreur : {e}")
        return []

    print(f"[Adzuna] status={resp.status_code} count={data.get('count')} results={len(data.get('results', []))}")
    if not data.get("results"):
        print(f"[Adzuna] body={str(data)[:500]}")
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
# Cle gratuite : https://jooble.org/api/about
JOOBLE_API_KEY = os.getenv("JOOBLE_API_KEY")


def fetch_jooble_offers():
    if not JOOBLE_API_KEY:
        return []
    url = f"https://jooble.org/api/{JOOBLE_API_KEY}"
    payload = {"keywords": "alternance informatique développeur", "location": f"{CITY}, France", "radius": int(RADIUS_KM)}
    try:
        resp = requests.post(url, json=payload, timeout=20)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        print(f"[Jooble] erreur : {e}")
        return []

    print(f"[Jooble] status={resp.status_code} payload={payload} totalCount={data.get('totalCount')} jobs={len(data.get('jobs', []))}")
    if not data.get("jobs"):
        print(f"[Jooble] body={str(data)[:500]}")
    normalized = []
    for o in data.get("jobs", []):
        normalized.append({
            "id": f"jooble:{o.get('id')}",
            "title": o.get("title", "Offre alternance"),
            "company": o.get("company", "Entreprise inconnue"),
            "city": o.get("location", ""),
            "url": o.get("link", ""),
            "source": o.get("source", "Jooble"),
        })
    return normalized


# ---------- Discord + dedup ----------

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS seen (id TEXT PRIMARY KEY)")
    conn.commit()
    return conn


def send_discord_notification(offer: dict):
    payload = {
        "embeds": [{
            "title": offer["title"],
            "description": f"**{offer['company']}**\n{offer['city']}",
            "url": offer["url"] or None,
            "footer": {"text": offer["source"]},
            "color": 5814783,
        }]
    }
    try:
        resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        if resp.status_code >= 300:
            print(f"[Discord] status={resp.status_code} body={resp.text[:300]}")
    except requests.RequestException as e:
        print(f"[Discord] erreur : {e}")


def main():
    if not DISCORD_WEBHOOK_URL:
        raise SystemExit("Configure DISCORD_WEBHOOK_URL dans .env")

    conn = init_db()
    seen_ids = {row[0] for row in conn.execute("SELECT id FROM seen")}

    all_offers = fetch_lba_offers() + fetch_adzuna_offers() + fetch_jooble_offers()

    # LBA est déjà filtré finement (ROME + diplôme) : on lui fait confiance.
    # Adzuna/Jooble font une recherche plein-texte plus permissive : on exige
    # que "alternance" ou "apprenti" apparaisse vraiment dans le titre pour
    # éliminer les faux positifs (infirmier, office manager, etc.).
    filtered_offers = []
    for offer in all_offers:
        if offer["source"] == "La Bonne Alternance":
            filtered_offers.append(offer)
            continue
        title_lower = offer["title"].lower()
        if "alternance" in title_lower or "apprenti" in title_lower:
            filtered_offers.append(offer)

    new_count = 0
    for offer in filtered_offers:
        if offer["id"] in seen_ids:
            continue
        send_discord_notification(offer)
        conn.execute("INSERT OR IGNORE INTO seen (id) VALUES (?)", (offer["id"],))
        new_count += 1

    conn.commit()
    conn.close()
    print(f"{new_count} nouvelle(s) offre(s) sur {len(filtered_offers)} pertinente(s) ({len(all_offers)} brutes, toutes sources).")


if __name__ == "__main__":
    main()