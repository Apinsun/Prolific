# TODO

## ✅ Fait — reprise d'un job en pause

La vraie reprise est implémentée :

- **Pause** : `interrupt()` LangGraph entre deux images → statut `paused`.
- **Reprise** : `POST /api/jobs/{id}/resume` (et bouton UI) → `graph.invoke(None,
  thread_id=job_id)` depuis le checkpoint.
- **Idempotence** : `generate` repart de `images_done` (pas de doublons).
- **Pause/reprise globale** : boutons « Tout en pause » / « Tout reprendre »
  (`/api/jobs/pause-all`, `/api/jobs/resume-all`) ; la file survit au redémarrage
  (reconstruction de l'état depuis la base).
- **Pré-rédaction** : en mode `count`, tous les prompts sont rédigés avant de générer
  (un seul cycle VRAM).

## Autres pistes

- Filtre de similarité (Jaccard) contre les « presque doublons » de tags.
- Réglage du seuil de similarité du RAG (actuellement 0.3).
- Wrapper MCP en stdio (en plus du HTTP), pour les outils qui l'exigent.
- Navigation du lightbox à la molette / tactile (en plus des flèches et boutons).
- Re-rendu incrémental des vignettes pendant la génération.
- Validation des tags rédigés contre l'index danbooru (signaler les tags hallucinés).
- Servir les images depuis Prolific (pour que les miniatures marchent sans ComfyUI).
- Jobs de rédaction parallèles (le worker est unique ; la génération reste limitée
  par la VRAM, mais la rédaction de plusieurs jobs pourrait tourner en parallèle).
