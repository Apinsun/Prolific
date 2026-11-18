ComdyUI, production en masse


Ce que ça cherche à résoudre


Quand on veut générer une grande quantité d’image avec un modele de génération d’image, on pourrait laisser cela à un agent IA et le laisser faire des générations via comfyUI. Cette approche fonctionne relativement bien mais à quelques problèmes :

	- Lorsque ComfyUI ET l’agent tournent en local on peut avoir à swapper la vram si on en a pas assez. Sur une installation naïve aucun des deux ne vide la ram avant de lancer la génération ou l’agent si besoin ce qui donne des lenteurs potentiellement rendant le tout complétement inutilisable. Sur une installation un peu moins naïve ou on viderait (si besoin) la vram pour mettre le llm ou le modele de génération d’image, il faut faire attention à limiter le plus possible ces swaps car chaque swap implique une assez forte latence.

	- Les modèles via API ont le soucis d’être payant et souvent censuré. Les modèles locaux non censurés sont généralement dégradés en ce qui concerne le tool call et ne sont pas forcement très imaginatifs, pire encore ils ont par exemple du mal à respecter la syntaxe danbooru lorsqu’on génère avec eds modèles type illustrious ou anima, ils inventent également souvent des tags inexistants dans le train du modele de generation d’image.

	- Les modeles locaux tout particulièrement, ont du mal à être laisser seuls sur une tâche. Il est compliqué de lui dire «génère pendant 20 minutes et on se revoit », soit le modele ne respecte pas la consigne lié au temps, soit elle s’arrete de manière complétement arbitraire, sans raison, sans avoir fait le travail.

	- Pour générer via comfyui+agent il peu être difficile d’envoyer des worklflow compliqués, avec loras + multipass, etc. 


Solutions proposés


Pour répondres à ses problèmes j’ai pensé à une application devant se connecter à un llm provider (local ou non) et une instance comfyui (qu’on considérera local pour le moment, à voir par la suite). Cette application doit être simple d’installation et proposer une interface web pour interragir avec mais également un serveur MCP afin d’être configurable facilement par un llm. Cette application va résoudre les problèmes mentionnés précédemment de la manière suivante :

	- proposer de swapper la ram avant la génération d’image, puis à la fin de la génération d’image, décharger le modele de génération pour remmetre le LLM. Éviter les swaps en faisant d’abord rédiger tous les prompts au llm PUIS de lancer toutes les générations le plus possible. Ainsi si on dit au modele de produire 50 images, le modele rédige les 50 prompts puis on les génères. Si on lui dit de travailler pendant un certains nombre de minutes, il faudra rédiger un certains nombres de prompts avant de faire le swap de vram et lancer les générations.

	- L’application fourni une recherche RAG avec une syntaxe simple. Ainsi si le modele fonctionne avec des tags danboorus le modèle aura la possibilité de recherché de vrais tags danvooru grace au rag (on peut également imaginer une recherche random pour introduire de l’aléatoire dans la géénration). Afin d’avoir à terme de meilleures performances avec des LLM locaux il faudra stocker d’une manière ou d’une autre les traces de raisonnement et de tool call afin de créer un dataset permettant ultérieurement le fine tuning d’un petit model local spécialiser pour notre application.

	- L’application devra etre résiliente, si l’utilisateur demande un certains nombres d’images ou de travailler pendant un certains temps, il faudra que l’application détecte les erreurs, les arrets trop tot ou trop tard etc.

	- l’app devra prendre un workflow déjà fait sur comfyui (exporté au format json), permettre de concaténer à l’output du modele du texte pour le prompt positif et négatifs


Autres points importants :

- Il faudra pour chaque workflow spécifié la dimensions en pixels pour que le modele puisse donner de bonne dimensions (par exemple pour du 1mpx il faudra du 1024\*1024 ou du 832\*1216 etc), à déterminer si on donne le choix au llm de donner les dimensions ou s’il faut les coder en dur et juste demander si l’output doit etre carré, portrait, paysage, etc.

- l’app devra tourner sur un container afin de faciliter son déploiement, se lancer via un script start.sh et stop.sh AINSI que s’installer via un install.sh, on utilisera poetry pour les dépendances, git pour le versionning
