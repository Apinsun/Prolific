# TODO

## Vraie reprise d'un job arrêté

**État** : non implémenté.

Aujourd'hui, « Arrêter » pose un drapeau : le job termine l'image en cours, vide la
file, et passe en `stopped`. Il n'y a **pas de reprise possible** après coup.

**Ce que la vraie reprise implique :**

1. **Stop interruptible** : remplacer le drapeau (`threading.Event`) par
   l'`interrupt()` natif de LangGraph, pour que le graphe se mette **en pause sur un
   point propre** (entre deux images ou entre deux lots) au lieu de simplement s'arrêter.
2. **Reprise** : ajouter un endpoint/bouton `resume` qui rappelle
   `graph.invoke(None, config={"configurable": {"thread_id": str(job_id)}})` — LangGraph
   repart alors du dernier checkpoint.
3. **Idempotence du nœud `generate`** : ne pas régénérer les images déjà produites
   (se baser sur `images_done` / les lignes déjà présentes en base).
4. **Statut `paused`** distinct de `stopped`, et gestion du cycle de vie
   interrupt → resume dans l'API et l'UI.

**Impact** : changement du modèle d'exécution (thread + Event → graphe interruptible
avec reprise), et de la machine à états des statuts
(`running / paused / done / stopped / failed`).

## Autres pistes

- Filtre de similarité (Jaccard) contre les « presque doublons » de tags.
- Amélioration de la qualité du RAG (seuil de similarité, filtres par catégorie).
- Wrapper MCP en stdio (en plus du HTTP), pour les outils qui l'exigent.
- Récupération/copie des images vers un dossier propre à Prolific.
