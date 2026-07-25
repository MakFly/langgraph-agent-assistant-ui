"""Surface HTTP — la seule couche qui connaît FastAPI.

Les routers traduisent des requêtes en appels au domaine (`agent.core`) et rien de plus :
pas de règle métier ici. C'est ce qui permet de remplacer le serveur web sans toucher à
l'agent — et de répondre à « pourquoi FastAPI ? » en montrant où il commence et où il
s'arrête.
"""
