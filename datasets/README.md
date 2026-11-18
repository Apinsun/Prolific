# Ajouter un dataset

Un **dataset** est un fichier CSV qui liste des termes avec leur définition. Il aide le
LLM à employer les bons mots : tags danbooru, vocabulaire technique, noms de vêtements,
angles de caméra, etc.

## Format du CSV

Colonnes (dans n'importe quel ordre) :

| Colonne | Requis | Description |
|---------|--------|-------------|
| `term` (ou `tag`) | oui | le terme exact, ex. `corset`, `goggles` |
| `definition` (ou `description`) | oui | une courte définition |
| `category` | non | ex. `vêtement`, `accessoire`, `angle de caméra` |

Exemple (`datasets/vetements.csv`) :

```csv
term,definition,category
kimono,longue robe traditionnelle japonaise,vêtement
obi,large ceinture portée avec le kimono,vêtement
goggles,lunettes de protection,accessoire
```

## Étapes

1. Déposez le fichier `.csv` dans le dossier `datasets/`.
2. Indexez-le :
   ```bash
   ./scripts/index_datasets.sh
   ```
3. Le dataset apparaît dans l'UI (cases à cocher « Datasets RAG »).

## Remarques

- Le **nom du dataset** = le nom du fichier sans l'extension
  (`vetements.csv` → `vetements`).
- Après avoir **modifié** un CSV, relancez le script pour réindexer.
- Privilégiez des définitions courtes (1 à 2 phrases) : ce sont elles qui sont utilisées
  pour la recherche de similarité.
