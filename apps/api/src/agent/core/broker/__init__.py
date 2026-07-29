"""Verticale courtage : transformer un courriel entrant en dossier préparé.

Ce paquet est une **couche au-dessus** de la messagerie et du moteur de
recherche, pas un logiciel de courtage. Il ne stocke pas de contrat, ne gère pas
de police, ne facture rien. Il lit ce qui arrive, le rattache à ce qui existe
déjà dans l'index documentaire, et prépare le travail du collaborateur.

    message   lecture d'un `.eml`, décitation, pièces jointes
    classify  intention · urgence · entités
    registry  portefeuille reconstruit depuis les métadonnées de l'index
    link      cascade de rattachement, avec sa confiance
    missing   pièces manquantes, d'après le référentiel du corpus
    draft     brouillon de réponse — jamais envoyé
    pipeline  l'enchaînement, et le statut du dossier
    evaluate  la mesure contre la vérité terrain de la boîte de démonstration

La règle qui vaut pour tout le paquet : **rien ne s'envoie, rien ne se décide**.
Le produit réduit le temps entre la réception d'un courriel et son traitement ; il
ne prend aucune position engageant le cabinet.
"""
