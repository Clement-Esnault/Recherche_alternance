# Alternance Watcher

![Alternance Watcher](https://github.com/Clement-Esnault/Recherche_alternance/actions/workflows/watcher.yml/badge.svg)

Script qui surveille les nouvelles offres d'alternance (informatique, zone
Caen/Bayeux/Creully par défaut, personnalisable) sur 3 sources et ping un
webhook Discord dès qu'une offre pertinente apparaît. Tourne en continu sur
GitHub Actions — pas besoin de garder un PC ou un serveur allumé.

## Sources

- **[La Bonne Alternance](https://api.apprentissage.beta.gouv.fr/fr/documentation-technique)**
  (service public) — agrège France Travail et ~100 partenaires (Hellowork,
  Monster, Apec, Météojob...). Filtrée par code ROME + niveau de diplôme.
- **[Adzuna](https://developer.adzuna.com/signup)** — agrégateur indépendant,
  filtré par catégorie "IT jobs" + mot-clé "alternance".
- **[Jooble](https://jooble.org/api/about)** — même principe, autre pool de
  données.

Chaque source est indépendante et optionnelle : si sa clé API n'est pas
configurée, elle est simplement ignorée (les autres continuent de tourner).

Pour Adzuna et Jooble, dont la recherche est en plein texte (donc plus
bruitée), le script exige que le mot "alternance" ou "apprenti" apparaisse
dans le titre avant de notifier — évite le bruit type offres CDI ou sans
rapport. La Bonne Alternance est déjà filtrée finement en amont (ROME +
diplôme), donc pas re-filtrée.

## Déduplication

`seen_offers.db` (SQLite) garde la liste des offres déjà notifiées. Sur
GitHub Actions, ce fichier est sauvegardé/restauré entre chaque run via
`actions/cache` — sans ça, chaque run serait "à froid" et re-notifierait
tout.

## Setup local (pour tester)

1. `cp .env.example .env` et remplis les clés (voir les liens d'inscription
   dans le fichier — toutes gratuites)
2. `pip install -r requirements.txt`
3. `python main.py`

Pour itérer sur les critères de recherche (mots-clés, codes ROME...) sans
polluer ton Discord ni marquer des offres comme "vues", passe `DRY_RUN=true`
dans ton `.env` : le script affiche ce qu'il aurait envoyé, sans rien
envoyer ni écrire en base.

## Déploiement GitHub Actions (production, tourne tout seul)

1. Push ce repo sur GitHub (le `.gitignore` protège déjà `.env` et
   `seen_offers.db` — ne les commit jamais)
2. Repo GitHub > Settings > Secrets and variables > Actions > crée un secret
   pour chaque clé : `LBA_API_TOKEN`, `ADZUNA_APP_ID`, `ADZUNA_APP_KEY`,
   `JOOBLE_API_KEY`, `DISCORD_WEBHOOK_URL`
3. Le workflow `.github/workflows/watcher.yml` tourne automatiquement toutes
   les 3h (`cron: "0 */3 * * *"`) — modifiable directement dans ce fichier
4. Test manuel : onglet **Actions** du repo > "Alternance Watcher" >
   "Run workflow"

Les critères de recherche (ville, rayon, codes ROME, mots-clés) sont
codés en dur dans le bloc `env:` de `watcher.yml` — à adapter à ta zone et
ton profil si tu réutilises ce projet.

## Limites connues

- LinkedIn et Indeed en direct n'ont pas d'API gratuite — non couverts (LBA
  et Adzuna les intègrent parfois indirectement via leurs partenariats).
- L'API La Bonne Alternance peut renvoyer 0 offre "active" tout en montrant
  des "recruteurs à fort potentiel" — c'est un signal du marché local, pas
  un bug.
- Jooble a un quota de 500 requêtes (période non précisée par leur équipe) —
  surveille les logs si tu resserres le cron en dessous de 3h.