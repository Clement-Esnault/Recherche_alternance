# Alternance Watcher

Check les nouvelles offres d'alternance (API La Bonne Alternance = France Travail,
Indeed, Hellowork, Monster, etc. agrégés) et ping un webhook Discord pour chaque
nouvelle offre. Pas d'interface, juste des notifs.

## Sources agrégées

- **La Bonne Alternance** (service public) — agrège déjà France Travail, Hellowork,
  Monster, Apec, Météojob, Le Bon Coin et ~100 autres partenaires
- **Adzuna** — agrégateur indépendant, couvre d'autres boards (LinkedIn, Indeed, etc. selon les accords)
- **Jooble** — même principe, sources différentes

Chaque source est optionnelle : si tu ne remplis pas ses clés API dans `.env`, elle est juste skip.

## Setup

1. **La Bonne Alternance** : crée un compte et récupère un token sur
   https://api.apprentissage.beta.gouv.fr/fr/compte/profil
2. **Adzuna** : inscription gratuite sur https://developer.adzuna.com/signup
3. **Jooble** : clé gratuite sur https://jooble.org/api/about
4. **Discord** : crée un webhook dans le salon voulu (Paramètres du salon > Intégrations > Webhooks)
5. `cp .env.example .env` puis remplis les clés que tu as
6. `pip install -r requirements.txt`
7. Avant le premier run réel : va sur le swagger LBA (lien dans `main.py`) pour vérifier
   le path exact de la route de recherche et le nom des champs de la réponse JSON
   (`LBA_SEARCH_PATH` et le parsing dans `fetch_lba_offers` sont à ajuster
   en fonction — je n'avais pas accès au swagger interactif qui nécessite le token).
8. Test : `python main.py`

## Lancer en automatique (cron, toutes les 30 min)

```
crontab -e
```

Ajoute :

```
*/30 * * * * cd /chemin/vers/alternance-watcher && /usr/bin/python3 main.py >> watcher.log 2>&1
```

## Notes

- `seen_offers.db` (SQLite) garde en mémoire les offres déjà notifiées pour éviter les doublons.
- Usage de l'API réservé aux usages non lucratifs (pas de revente/facturation des données).
- Si tu veux élargir au-delà de La Bonne Alternance (LinkedIn, Indeed direct, etc.),
  ces sites n'ont pas d'API publique gratuite — faudrait du scraping, plus fragile
  et limite niveau CGU.
