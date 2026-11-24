# Fine-tuning du rédacteur de prompts — guide pour l'agent IA

> Ce document oriente le futur agent (IA ou humain) qui travaillera sur le
> **fine-tuning d'un modèle local** destiné à remplacer Leila_fast dans Prolific.
> À lire en début de session, avec `ARCHITECTURE.md` et `README.md`.

---

## 1. Objectif

Entraîner un **petit modèle (~4–9B)** à **rédiger des prompts en tags danbooru** avec
**tool calling RAG**, pour remplacer le 27B (Leila_fast) dans Prolific.

Gains visés : vitesse, VRAM libérée pour le modèle d'image, comportement plus
prévisible.

**Le fine-tune vise UNIQUEMENT la rédaction** (`draft`). Le reste de Prolific
(génération ComfyUI, swap VRAM, queue, traces) n'est pas concerné.

---

## 2. Les données : `traces/`

Deux fichiers JSONL (gitignorés), écrits par `src/prolific/traces.py` :

### `traces/drafts.jsonl` — la source principale
Une ligne = **un chunk de rédaction** (un appel au graphe `draft`). Champs :

| Champ | Contenu |
|-------|---------|
| `job_id`, `timestamp`, `theme` | contexte du job |
| `style` | `tags` / `prose` / `hybrid` (le v1 vise **tags**) |
| `datasets`, `temperature`, `model` | config de rédaction |
| `n` | nombre de prompts demandés |
| `prompts`, `orientations` | sortie finale (les cibles) |
| `tool_calls` | liste `{name, args, result}` des recherches RAG |
| `conversation` | échange complet `{role, content, tool_calls?, thinking?}` (system/user/assistant/tool) |

### `traces/images.jsonl` — secondaire
Prompt final → résultat (seed, dimensions, statut, durée). Utile plus tard pour un
éventuel score de qualité d'image (RLHF/DPO), pas pour le SFT de rédaction.

### Nature des données
C'est de la **distillation** : le « gold » est la sortie de **Leila_fast (27B)**, pas
un corpus relu par un humain. Conséquences :

- ✅ Le format conversationnel + tool calls est déjà exploitable pour un SFT.
- ⚠️ Les **tics du teacher** sont dedans (ex. sur-utilisation de `bizarre_rain`,
  `drama_layer`, `egasumi`, `glowing_tears`, `gradient_background`…). → **nettoyer
  avant entraînement** (fréquence des tags, validation contre l'index danbooru).
- ⚠️ Volume : viser quelques centaines de drafts / plusieurs milliers de prompts.
  On peut en générer plus à la demande (voir §7).

---

## 3. Référence : `~/Code/Projet14`

Un fine-tune SFT **Unsloth LoRA** de Qwen (1.7B puis 4B) y a déjà été mené pour un
autre projet (triage médical). Réutiliser ce savoir :

- `scripts/train_sft.py` est **générique** (`--model Qwen/Qwen3.5-4B`, LoRA `r`/`alpha`,
  4-bit, `SFTTrainer`, fusion `merged_16bit`).
- `scripts/train-4b.sh` + venv dédié `.venv-train-4b` (Python **3.12**, pas le 3.14 de
  Prolific) + `LD_LIBRARY_PATH` vers les libs nvidia.
- Conventions éprouvées : `AGENTS.md`, `reports/README.md` (index obligatoire),
  `validate_dataset.py`, harness d'éval sur jeu gold, CI.

Différence clé : Projet14 servait via **vLLM** ; ici on sert via **Ollama** (Prolific
parle déjà à Ollama).

---

## 4. Décisions à respecter (ne pas re-débattre)

1. **Style `tags` uniquement** pour la v1 (61/63 des drafts existants).
2. **Avec tool calling** : on garde `search_terms` / `list_categories` / `random_term`.
   La base doit donc être une variante instruct qui supporte les tools.
3. **Non censuré** : le dataset doit produire du NSFW. Utiliser une base
   **abliterated** (ex. `huihui_ai/qwen3.5-abliterated:9b`, déjà en local), PAS la
   version instruct officielle (qui refuse le sexuel).
4. **Base ~9B** (Qwen3.5-9B abliterated) : faisable sur RTX 3090 24 Go en QLoRA 4-bit
   + gradient checkpointing.
5. **Serving Ollama** : fusionner le LoRA → GGUF → Modelfile. Pas de vLLM.

---

## 5. Pipeline cible

```
traces/drafts.jsonl
  └─ 1) data prep : chat-format + nettoyage + split train/eval (gold)
        └─ 2) SFT Unsloth (LoRA 4-bit) sur la base 9B
              └─ 3) fusion 16-bit → GGUF → Ollama Modelfile
                    └─ 4) évaluation (métriques) + smoke test d'intégration
                          └─ 5) Prolific : config.yaml → model: <nouveau modèle>
```

### Format SFT cible (chat Qwen)

```
system  : le system prompt de Prolific (draft_system.txt OU un preset, ex. cascade.txt)
user    : le prompt utilisateur (thème + n + historique)
assistant: la réponse JSON {"prompts": [...], "orientations": [...]}
          (avec, si tool calling, les tool_calls et leurs tool results dans l'échange)
```

**Point critique d'intégration** : entraîner avec **le même system prompt** que celui
envoyé à l'inférence par Prolific (templates `prompts/*.txt`), sinon décalage de
distribution.

---

## 6. Évaluation (à coder dans le nouveau repo)

- **Validation dataset** (avant entraînement) : schéma OK, JSON valide, pas de prompt
  vide, `n` respecté, pas de doublons.
- **Harness sur un jeu gold figé** (thèmes jamais vus à l'entraînement), métriques :
  1. **parse rate** — % de réponses JSON valides ;
  2. **validité des tags** — % de tags présents dans l'index danbooru
     (`search_tags` / `~/Documents/dataset_nettoye.csv`) → anti-hallucination ;
  3. **diversité** — tags uniques, pas de doublons exacts vs l'historique ;
  4. **min_words / orientations** si actifs ;
  5. **fidélité** — distance vs les sorties de Leila_fast sur les mêmes thèmes (baseline).
- **Test d'intégration Prolific** : pointer `config.yaml` sur le nouveau modèle et
  lancer un vrai job (1 batch) pour vérifier `draft_prompts_agent` → ComfyUI de bout
  en bout, sans erreur de parse.

---

## 7. Générer plus de traces (si besoin)

- Job type pour maximiser le tool calling : `draft_batch_size=5`, `forced_searches=5`,
  `datasets=["danbooru", ...]`, `random_tags=true`, `enable_traces=true`.
- **Preset `cascade`** (`prompts/presets/cascade.txt`) : impose la recherche en cascade
  (chercher → lire la définition → chercher les concepts dérivés → rédiger). C'est le
  meilleur générateur de traces multi-hop.
- Les jobs de la campagne récente (100–107) ont produit **164 tool calls** (60 + 104)
  sur des sujets variés, dont NSFW.

---

## 8. Environnement & conventions

- Python **3.12** pour l'écosystème ML (Unsloth/TRL) ; venv séparé pour l'entraînement.
- Artefacts lourds (modèles, datasets, logs) gitignorés ; rapports dans `reports/`
  avec un index `reports/README.md` à jour (règle d'or héritée de Projet14).
- Lint + tests verts avant commit.
- Le fine-tune vit dans un **nouveau repo** (pas dans Prolific) ; il lit `traces/`
  en lecture seule et documente le mapping trace → SFT.
