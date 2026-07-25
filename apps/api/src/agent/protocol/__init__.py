"""Le protocole AI SDK, dans les deux sens.

`messages.py` convertit l'historique envoyé par le client en messages LangChain,
`stream.py` émet le « UI Message Stream » que consomme assistant-ui. Ces deux fichiers
n'existent que parce que le front parle ce protocole : ce sont eux qu'on remplacerait en
changeant de client, et rien d'autre.
"""
