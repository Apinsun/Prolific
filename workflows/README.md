# Workflows

Déposer ici les workflows ComfyUI exportés en **format API**, puis les déclarer dans
`manifest.json` (voir `manifest.example.json`).

Ce dossier est **gitignoré** : seul `manifest.example.json` et ce README sont versionnés.
(`manifest.json`, les `.json` de workflows et les images produites ne sont pas commités.)

## Comment faire pour qu'un workflow s'intègre bien

Prolific injecte dans le workflow uniquement ce que le manifest déclare : il ne
devine rien. Un workflow s'intègre bien si on lui dit **où** écrire ces 5 choses.

### 1. Exporter en format API

Dans ComfyUI : menu **Workflow → Export (API)**. On obtient un JSON dont les clés
sont des **numéros de nœuds** (`"1"`, `"2"`, …). Ce sont ces numéros qu'on déclare
dans le manifest.

### 2. Les 5 slots à repérer dans le JSON

| Slot | Nœud attendu | Pourquoi |
|------|--------------|----------|
| `positive` | `CLIPTextEncode` (le prompt positif) | champ `text` remplacé par le prompt rédigé |
| `negative` | `CLIPTextEncode` (le prompt négatif) | champ `text` remplacé par le négatif du job (sinon celui du workflow est gardé) |
| `width` / `height` | `INTConstant` **ou** le `EmptyLatentImage` directement | dimensions selon l'orientation choisie |
| `seeds` | **tous** les `KSampler` | re-seedés à chaque lot (même seed par lot) |
| *(auto)* | `EmptyLatentImage` | `batch_size` = nombre d'images par prompt |
| *(auto)* | `SaveImage` | `filename_prefix` redirigé vers le sous-dossier de sortie |

`positive`, `negative`, `width`, `height`, `seeds` se déclarent dans le manifest ;
`EmptyLatentImage` et `SaveImage` sont détectés automatiquement par leur `class_type`
(aucune déclaration nécessaire).

### 3. Déclarer dans manifest.json

```json
"mon-workflow": {
  "file": "mon-workflow.json",
  "description": "…",
  "style": "tags",
  "formats": {
    "carre": "1024x1024",
    "paysage": "1216x832",
    "portrait": "832x1216"
  },
  "slots": {
    "positive": { "node": "1", "field": "text" },
    "negative": { "node": "12", "field": "text" },
    "width":    { "node": "15", "field": "value" },
    "height":   { "node": "14", "field": "value" },
    "seeds":    ["9", "10"]
  }
}
```

- `width`/`height` : si le `EmptyLatentImage` reçoit `"width": ["15", 0]`, alors le
  nœud **15** est la largeur (un `INTConstant`), etc. Regardez les liens du
  `EmptyLatentImage` pour trouver les bons numéros.
- `seeds` : listez **tous** les `KSampler` (un workflow Hi-Res 2 passes en a deux).

### 4. Règles pour éviter les mauvaises surprises

- **Trigger de LoRA** : mettez-le dans le champ **« Préfixe positif »** du job (ou
  laissez le LLM l'ajouter). Prolific ne touche jamais aux nœuds de LoRA.
- **Chemins des modèles/LoRAs** : sous Linux, utilisez des **slashs** `/`
  (ex. `Illustrious/laddybug_w0man_illustrious_v01.safetensors`).
- **Négatif par défaut** : si le nœud négatif contient un texte, il ne sera utilisé
  que si le job ne fournit pas de négatif. Évitez d'y mettre des mots qui contredisent
  le thème (ex. « ladybug » pour un thème coccinelle).
- **Batch** : le `EmptyLatentImage` doit rester câblé sur les `KSampler` ; Prolific ne
  modifie que son `batch_size`.
- **Sortie** : au moins un nœud `SaveImage` relié à la sortie finale. Sinon seules les
  previews (`type=temp`) seront récupérées.

### 5. Vérifier

Après déclaration, lancer un job de test à 1 image. Si le job se termine `done`, le
workflow est intégré. L'image finale doit être dans
`output/<sous-dossier>/<prefix>_00001_.png` (taille = dimensions × scale_by du
workflow).
