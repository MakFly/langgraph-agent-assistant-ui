"""Recherche documentaire : découpage, vectorisation, indexation, récupération.

Le paquet ne connaît ni FastAPI ni le graphe : il expose des modules, que
`agent.core.tools.rag` habille en outil et que `agent.cli` appelle pour indexer.

**Rien n'est réexporté ici volontairement.** Remonter `ingest.ingest` au niveau du
paquet masquerait le module `ingest` derrière la fonction du même nom, et
`from agent.core.rag import ingest` rendrait alors l'un ou l'autre selon l'ordre
des imports — une classe de bug particulièrement pénible à lire.
"""
