# Prolific

Générez des images **en masse** avec un LLM + ComfyUI. Vous décrivez un thème,
Prolific rédige les prompts, les enrichit (recherche de vrais tags), et lance les
générations tout seul — en gérant la mémoire GPU (VRAM) et en suivant un objectif
(nombre d'images ou durée).

## Prérequis

- **ComfyUI** lancé (sur `http://127.0.0.1:8188` par défaut)
- **Ollama** lancé (sur `http://localhost:11434` par défaut), avec au moins un modèle
- Un **workflow ComfyUI** exporté (voir ci-dessous)

## Installation

```bash
./scripts/install.sh
```

## Démarrage

```bash
./scripts/start.sh
```

Puis ouvrez **http://127.0.0.1:8000** dans votre navigateur. Pour arrêter :
`./scripts/stop.sh`.

## Préparer un workflow

1. Dans ComfyUI, construisez votre workflow, puis **Workflow → Export (API)**.
2. Placez le fichier `.json` dans le dossier `workflows/`.
3. Déclarez-le dans `workflows/manifest.json` (copiez `workflows/manifest.example.json`).
   Indiquez : le fichier, les résolutions par format, et les **slots** (où écrire le
   prompt positif, le négatif, la largeur, la hauteur, les seeds).

## Lancer une génération

Remplissez le formulaire de l'UI :

| Champ | Rôle |
|-------|------|
| Thème / prompt | ce que vous voulez générer |
| Workflow / Modèle | à choisir dans les listes |
| Objectif | nombre de prompts, ou durée en minutes |
| Dimensions | carré / paysage / portrait / aléatoire, ou **Auto** (le modèle choisit) |
| Type de prompt | tags (danbooru) / langage naturel / les deux |
| Datasets RAG | cochez pour aider le LLM à trouver de vrais termes |
| Mots aléatoires (RAG) | ajoute des termes variés (à cocher après un dataset) |
| Suffixe positif / négatif | texte concaténé au prompt |
| Batch | nombre d'images par prompt (pour retenter si besoin) |
| Longueur min | force le LLM à enrichir les prompts trop courts |
| Température | laissez vide = défaut du modèle, sinon réglez la créativité |

Le panneau de droite affiche la progression, les miniatures, et permet d'**arrêter**,
de **retirer un job des traces** (dataset de fine-tuning) ou de **supprimer le job**.

## Ajouter un dataset (tags / vocabulaire)

Voir [datasets/README.md](datasets/README.md). En résumé : déposez un CSV
(`term, definition, category`) dans `datasets/`, puis lancez `./scripts/index_datasets.sh`.

## Où sont les choses

- **Images** : dans le dossier de sortie de ComfyUI (défini par votre workflow).
- **Traces** (pour un futur fine-tuning) : dossier `traces/`, supprimables par job.
- **Logs** : console (ou fichier si `logging.file` est renseigné dans `config.yaml`).
- **Données locales** : `prolific.sqlite` (jobs, images, calibration).

## Dépannage rapide

- « Workflow inconnu » → vérifiez `workflows/manifest.json`.
- « ComfyUI injoignable » → lancez ComfyUI et vérifiez l'URL dans `config.yaml`.
- Un job semble bloqué → relancez le service : les jobs orphelins sont marqués stoppés.
- Le RAG ne répond pas → lancez `ollama pull bge-m3` puis `./scripts/index_datasets.sh`.

> Documentation technique (architecture, graphes) : voir [ARCHITECTURE.md](ARCHITECTURE.md).
