# Prolific — Spécification

> **Statut** : v0.1 — décisions actées, à figer en code.
> **Contexte** : voir `idees-originelles.md`.

---

## 1. Objectif

Application de **génération d'images en masse** pilotée par un LLM, s'appuyant sur une
instance ComfyUI (locale). Elle orchestre la rédaction des prompts, la génération, la
gestion VRAM, et tient un objectif (« N images » ou « T minutes ») de façon **résiliente**
et **supervisable** par un humain.

Deux interfaces de pilotage :
- **Interface web** : pour l'humain (créer un job, suivre, stopper).
- **Serveur MCP** : pour qu'un agent LLM externe (pi, Claude, …) pilote l'app.

### Non-objectifs (v2 ou plus tard)
- Providers LLM autres qu'Ollama (OpenAI-compatible, vLLM, runpod…).
- Évaluation automatique de la qualité des images (vision).
- Fine-tuning d'un modèle local (mais on prépare la **capture de traces**, cf. §9).
- ComfyUI distant / multi-GPU.

---

## 2. Principes d'architecture

### 2.1 L'app est la machine à états
Le LLM **ne tient jamais la boucle**. Il est un « cerveau » stateless appelé ponctuellement :
rédiger N prompts → on le décharge (si swap) → on génère → on le recharge pour éventuellement
ajuster. L'app détient : l'objectif, le budget temps, le compteur d'images, les timeouts,
les erreurs. C'est ce qui résout les « arrêts arbitraires » des modèles locaux.

### 2.2 Deux rôles LLM distincts
| Rôle | Sens | Interface |
|------|------|-----------|
| Rédaction de prompts | l'app **appelle** un LLM (Ollama) | client HTTP Ollama |
| Pilotage externe | l'app **expose** des outils à un agent | serveur MCP (HTTP) |

Ces deux flux sont indépendants et configurés séparément.

### 2.3 Un seul service
Un process FastAPI sert **à la fois** : l'API interne, l'interface web, et le MCP
(Streamable HTTP). État partagé en mémoire + SQLite.

### 2.4 Interruptible à tout moment
L'orchestrateur peut être stoppé proprement (finir l'image en cours, vider la file).
L'état du job est persisté pour reprendre après un crash.

---

## 3. Vue d'ensemble

```
                 ┌────────────────────────── Prolific ──────────────────────────┐
                 │                                                              │
  Agent externe ─┤► MCP (HTTP) ──┐                                              │
  (pi, Claude)   │                │                                             │
                 │                ▼                                             │
  Humain ────────┤► Web UI ──► Orchestrateur (machine à états)                  │
                 │                │        │        │                           │
                 │                ▼        ▼        ▼                           │
                 │           LLM (Ollama)  RAG    ComfyUI                       │
                 │           rédaction    (ChromaDB)  génération                │
                 │                                                              │
                 │           SQLite (jobs, traces)                              │
                 └──────────────────────────────────────────────────────────────┘
```

---

## 4. Boucle d'orchestration (cœur de l'app)

### 4.1 Objectif « N images »
1. L'utilisateur (ou l'agent MCP) crée un job : `workflow`, `N`, `thème`, `swap_vram`,
   `datasets actifs`, `orientation`.
2. L'app demande au LLM de rédiger **N prompts** (le LLM peut appeler `search_terms`).
3. Si `swap_vram` : décharger Ollama → générer les N images → libérer ComfyUI (`/free`)
   → recharger Ollama. Sinon : générer directement.
4. Rapport final (statut, images produites, erreurs).

> Le principe directeur : **un seul cycle VRAM par lot** — on rédige tout, puis on génère tout.

### 4.2 Objectif « T minutes »
Boucle **planifier → exécuter → adapter**, itérative (pas un seul batch à l'aveugle) :

1. **Planifier** : s'il existe une durée moyenne calibrée pour ce workflow, dimensionner
   un lot raisonnable ; sinon partir sur un **petit lot de sonde** (2-3 images).
2. **Exécuter** : rédiger les prompts du lot → (swap VRAM) → générer → (recharger).
3. **Adapter** : mesurer le temps réel, mettre à jour la moyenne glissante, recalculer
   le budget restant (avec marge), dimensionner le lot suivant.
4. **Répéter** jusqu'à épuisement du budget ou stop utilisateur.

> Compromis assumé : chaque lot = un cycle VRAM. Plus de lots → meilleure adaptation,
> mais plus de swaps. La calibration sert précisément à grossir les lots et réduire leur
> nombre au fil des jobs.

#### Calibration automatique
Durée moyenne par image stockée en SQLite, mise à jour après chaque génération terminée
(moyenne glissante). Graine initiale optionnelle dans le manifest (`avg_seconds`, mesuré
ou estimé). Aucune saisie manuelle obligatoire : au pire, le premier job sonde puis itère.

### 4.3 Stop utilisateur
À tout moment : finir l'image en cours, vider la file, marquer le job `stopped`,
persister l'état.

---

## 5. Configuration

### 5.1 `config.yaml` (édition manuelle ou via UI)
```yaml
llm:
  provider: ollama            # MVP : uniquement ollama
  base_url: http://localhost:11434
  model: ""                   # défaut, surchargé par job / dropdown

comfyui:
  url: http://127.0.0.1:8188

output:
  dir: ./output

server:
  host: 127.0.0.1
  port: 8000

datasets:
  dir: ./datasets            # CSVs indexés
```

### 5.2 Paramètres par job
- `workflow` (nom dans le manifest)
- `objectif` : `{type: "count", n}` ou `{type: "duration", minutes}`
- `thème` / consigne de rédaction
- `swap_vram` : bool (opt-in)
- `orientation` : `carre` | `paysage` | `portrait`
- `datasets` : liste des datasets exposés au LLM (cases à cocher)
- `style` (optionnel) : surcharge du style du workflow (`tags` | `prose` | `hybrid`)
- `seed` (optionnel), `batch` (images par prompt, optionnel)

### 5.3 UI
Dropdown modèle = `GET /api/tags` d'Ollama. Derniers choix persistés (équivalent
`QSettings`, côté serveur).

---

## 6. Providers LLM

Abstraction minimale : `LLMProvider` avec `base_url`, `model`, `generate(prompt)`.

- **MVP** : `OllamaProvider` uniquement (endpoint `/api/generate` ou `/api/chat`).
- Interface conçue pour ajouter `OpenACompatProvider` en v2 sans refonte.

---

## 7. Workflows (manifest.json)

Reprend l'approche du skill `comfyui` :
- Un dossier de workflows exportés (format API ComfyUI).
- Un `manifest.json` qui déclare, pour chaque workflow : `file`, `description`,
  `formats` (résolutions par orientation), et les **slots** à patcher.

```json
{
  "illustrious_ladybug": {
    "file": "illustrious-ladybug.json",
    "formats": { "carre": "1024x1024", "paysage": "1216x832", "portrait": "832x1216" },
    "slots": {
      "positive": { "node": "16", "field": "string_a" },
      "negative": { "node": "12", "field": "text" },
      "width":    { "node": "15", "field": "value" },
      "height":   { "node": "14", "field": "value" },
      "seeds":    ["10", "9"]
    }
  }
}
```

Règles :
- L'app ne patche **que les slots déclarés**. Checkpoint, LoRA et force, tags qualité,
  négatif par défaut restent figés dans le workflow.
- **Dimensions codées en dur** par orientation (pas de dimensions libres du LLM).
- Slot optionnel `style` : `tags` (danbooru/Illustrious/Pony), `prose` (krea2/Flux),
  ou `hybrid` (modèles type Anima qui acceptent les deux) — simple *indice* de formatage,
  **surchargable par job**.

#### Formatage du prompt (style)
Le `style` pilote une **directive de format** injectée dans le system prompt du LLM,
**sans few-shot** (aucun exemple de contenu, pour ne pas biaiser le modèle) :
- `tags` → « produis une ligne de tags séparés par des virgules ».
- `prose` → « produis une description en langage naturel ».
- `hybrid` → « tags et/ou langage naturel autorisés, au choix ».

Trois filets de sécurité (la forme est contrainte, jamais le contenu) :
1. **Directive** : consigne de format zéro-shot.
2. **Sortie structurée** : quand le provider le permet (Ollama `format: json`), un schéma
   lâche `{positive: str, negative: str|null}`.
3. **Normalisateur** : post-traitement si le modèle ignore la consigne (split virgules,
   nettoyage, une seule ligne…).

---

## 8. RAG / Datasets

### 8.1 Principe
Le RAG est un **outil de recherche optionnel**, pas un garde-fou. Le LLM l'appelle s'il
en a besoin. **Aucune validation ni rejet de prompt** basé sur la base.

### 8.2 Schéma unifié d'un dataset
CSV déposé dans `datasets/`, indexé en ChromaDB (embedding via Ollama
`nomic-embed-text`) :
```
term, definition, category
```
- `category` **optionnelle** (ex. danbooru n'en a pas aujourd'hui ; un CSV krea2 peut
  avoir `vêtement`, `angle de caméra`, `lumière`, `technique picturale`…).

### 8.3 Tool call exposé au LLM
```
search_terms(concept: str, dataset: str|None, top_k: int) -> [{term, definition, category, dataset}]
```
- `dataset` null = recherche sur tous les datasets **actifs** du job.

### 8.4 Sélection par job
L'utilisateur coche/décoche dans l'UI quels datasets sont fournis au LLM pour ce job.
Danbooru n'est qu'un dataset parmi d'autres.

---

## 9. Traces (fine-tuning futur)

Capture en JSONL, **locale, opt-in**, consciente du contenu potentiellement NSFW :
- **Traces de rédaction** : consigne → raisonnement → appels `search_terms` → prompt final.
- **Traces d'orchestration** : tâche → séquence d'appels MCP → résultats.

Objectif : constituer un dataset pour spécialiser un petit modèle local (rédaction et
pilotage). Le fine-tuning lui-même est **hors MVP**.

---

## 10. Résilience

- **État du job persisté** en SQLite (reprise après crash).
- **Taxonomie d'erreurs** (chacune avec politique de retry dédiée) :
  - ComfyUI injoignable / timeout de génération
  - OOM / erreur de queue ComfyUI
  - prompt vide / invalide
  - workflow introuvable
  - interruption (stop utilisateur / crash)
- **Retry** avec backoff, **abandon** après N échecs, rapport clair.

---

## 11. Serveur MCP

Transport : **Streamable HTTP** (même process que l'app). Outils exposés (v1) :
- `create_job(...)`, `stop_job(id)`, `get_job(id)`, `list_jobs()`
- `list_workflows()`, `list_datasets()`
- `search_terms(concept, dataset?, top_k)`

Un wrapper stdio (petit script proxy) pourra être ajouté plus tard si un outil local
l'exige — sans réécrire les outils.

---

## 12. Stack & déploiement

- **Python** + **Poetry** (dépendances), `ruff` (lint), `pytest`.
- **FastAPI** (API + UI + MCP), **fastmcp**, **chromadb**, **SQLite** (`sqlite3` stdlib).
- **Docker** pour le déploiement.
- Scripts : `install.sh`, `start.sh`, `stop.sh`.
- ComfyUI et Ollama **ne sont pas lancés par l'app** — l'utilisateur les démarre.
- **Versioning** : git. Le `.gitignore` exclut impérativement :
  - `traces/` et `*.jsonl` (dataset d'entraînement futur — **jamais commité**),
  - `workflows/` (workflows exportés — **hors repo**, seul `manifest.example.json` est
    versionné),
  - `.venv/`, `output/`, `chroma_db/`, `*.sqlite`, `__pycache__/`, `.env`.

---

## 13. MVP (périmètre livrable)

**Inclus :**
- Job « N images » **et** « T minutes ».
- Provider : Ollama uniquement.
- Swap VRAM opt-in (décharge Ollama → génère → `/free` ComfyUI → recharge).
- Bibliothèque de workflows via `manifest.json` (slots + formats).
- Datasets CSV → ChromaDB, tool call `search_terms`, sélection par case à cocher.
- UI web minimale : création de job, choix workflow/datasets/orientation, **flux de
  miniatures**, bouton **stop**.
- Persistance SQLite + reprise.
- MCP HTTP avec les outils v1.
- Capture de traces JSONL (opt-in).
- `install.sh` / `start.sh` / `stop.sh`, `config.yaml`.

**Exclu (v2) :**
- Providers OpenAI-compatible / runpod / vLLM.
- Évaluation automatique de qualité, ajustement par vision.
- Fine-tuning (traces seulement).
- Wrapper MCP stdio, ComfyUI distant, multi-GPU.

---

## 14. Décisions actées (récapitulatif)

1. L'app est la machine à états ; le LLM est stateless.
2. RAG = outil de recherche optionnel (`term/definition/category`), **aucune** invalidation
   de prompt ; datasets sélectionnables par job.
3. Workflows via `manifest.json` (même format slots que le skill `comfyui`).
4. MCP en Streamable HTTP, dans le service.
5. Mode « durée » inclus au MVP.
6. Dimensions en dur par orientation (carré/paysage/portrait), résolutions par workflow.
7. Swap VRAM opt-in par job.
8. Stack : Python/Poetry/FastAPI/fastmcp/ChromaDB/SQLite + Docker.
9. `style` (`tags`/`prose`/`hybrid`) = directive de format **zéro-shot** (pas de few-shot),
   surchargeable par job ; sortie structurée + normalisateur en filet.
10. Mode « T minutes » = boucle itérative planifier→exécuter→adapter, calibration auto
    (moyenne glissante) ; jamais un seul gros batch à l'aveugle.
11. `.gitignore` : traces et workflows **hors repo**.
```
