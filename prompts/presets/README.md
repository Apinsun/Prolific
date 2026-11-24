# Presets de prompt

Un **preset** est un fichier texte qui remplace le system prompt par défaut
(`prompts/draft_system.txt`). Il permet de tester rapidement différentes
« personnalités » de rédaction sans toucher au code.

## Où les mettre

Déposez vos fichiers `.txt` (ou `.md`) dans ce dossier `prompts/presets/`.
Le **nom du fichier** (sans extension) devient le nom du preset, visible dans le
dropdown « Prompt de base » de l'UI et utilisable via l'API/MCP (`preset`).

> Les fichiers dont le nom commence par `_` ou `.` (et `README`) sont ignorés.

Presets fournis : `expert-danbooru`, `minimal`, `cascade` (recherche RAG en cascade
pour générer des traces multi-hop — voir `FINETUNING.md`).

## Comment ça marche

Le contenu du fichier est utilisé **tel quel** comme system prompt. Il peut :

- **utiliser des placeholders** remplacés au runtime :
  - `$style_directive` → la directive de style (tags/prose/hybrid, depuis `style_*.txt`)
  - `$datasets_hint`   → la mention des outils RAG (si un dataset est sélectionné)
  - `$random_hint`     → les termes aléatoires suggérés (si « mots aléatoires » est coché)
- **ou être 100 % autonome** : n'incluez aucun placeholder — le style, le RAG et les
  mots aléatoires n'auront alors **aucun effet**.

> Seuls ces trois placeholders sont reconnus ; un `$inconnu` est laissé tel quel.

---

## Exemple 1 — avec placeholders (`expert-danbooru.txt`)

```text
Tu es un expert en tags danbooru pour un générateur d'images.

$style_directive

$datasets_hint

$random_hint

Règles supplémentaires :
- Ordonne les tags du plus général au plus spécifique.
- Pas plus de 35 tags par prompt.

Réponds UNIQUEMENT au format JSON :
{"prompts": ["...", "..."], "orientations": ["carre", "paysage", ...]}
```

Ici le style choisi dans l'UI, les outils RAG et les mots aléatoires continuent de
s'appliquer (les placeholders sont remplacés).

---

## Exemple 2 — sans placeholder (`minimal.txt`)

```text
Tu es un rédacteur de prompts minimalistes et percutants.
Pour chaque prompt : sujet, action, décor. Rien d'autre.

Réponds UNIQUEMENT au format JSON :
{"prompts": ["...", "..."], "orientations": ["carre", "paysage", ...]}
```

Aucun placeholder : la sélection du style, les datasets RAG et les mots aléatoires
n'ont **aucun effet** avec ce preset.
