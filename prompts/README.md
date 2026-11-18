# Templates de prompts

Ces fichiers sont **éditables sans toucher au code**. Le module `prompting.py` les
charge et interpole les placeholders `$...` (via `string.Template`).

## Fichiers

| Fichier | Rôle | Placeholders |
|---------|------|--------------|
| `draft_system.txt` | system prompt du LLM de rédaction | `$style_directive`, `$datasets_hint`, `$random_hint` |
| `draft_user.txt`   | requête utilisateur (thème + quantité) | `$n`, `$theme` |
| `style_tags.txt`   | directive de format danbooru | — |
| `style_prose.txt`  | directive de format langage naturel | — |
| `style_hybrid.txt` | directive de format mixte | — |
| `datasets_hint.txt`| mention des outils RAG (`search_terms`, `list_categories`, `random_term`) | `$datasets` |
| `random_hint.txt`  | termes aléatoires suggérés (mots aléatoires) | `$terms` |
| `snippets.md`      | consignes prêtes à copier-coller dans le thème | — |

> ⚠️ Ne mets pas d'exemple de contenu (tags précis, scènes…) dans `style_*.txt` :
> cela biaiserait le modèle. Ces directives ne portent que sur la **structure**.
