"""Isolation commune de la suite.

Le produit peut tourner avec un profil RAG coûteux. Les tests ne doivent jamais
hériter silencieusement de ce profil depuis ``apps/api/.env`` : ceux qui exercent
HyDE ou le reranking les activent explicitement et remplacent le modèle.
"""

import pytest


@pytest.fixture(autouse=True)
def _profil_rag_de_test(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("RAG_PROFILE", "custom")
