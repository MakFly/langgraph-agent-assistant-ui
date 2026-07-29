"""Administration en ligne de commande : `python -m agent.cli ...`

Les comptes se gèrent ici et non par HTTP. Un endpoint « créer un utilisateur »
devrait être protégé, audité et limité en débit ; une commande qui exige déjà un
accès au conteneur n'a besoin de rien de tout ça.

Le mot de passe n'est **jamais** passé en argument : il serait visible dans
`ps`, dans l'historique du shell et dans les logs de la CI. Il est lu sur
l'entrée standard, ou dans la variable d'environnement `USER_PASSWORD`.

    make user-create EMAIL=alice@example.com ROLE=admin GROUPS=finance,rh
    make user-list
    make user-groups EMAIL=alice@example.com GROUPS=finance
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
from pathlib import Path

from agent.core import seed as seeding
from agent.core import sessions, users
from agent.core.rag import config as ragconfig
from agent.core.rag import embed, evaluate, ingest, llm
from agent.infra import db, ragdb


def _read_password(confirm: bool = True) -> str:
    """Mot de passe depuis l'environnement, un tube, ou une saisie masquée."""
    import os

    from_env = os.getenv("USER_PASSWORD")
    if from_env:
        return from_env

    if not sys.stdin.isatty():
        # Cas `echo "..." | make user-create` : pas de terminal, pas de masquage.
        return sys.stdin.readline().rstrip("\n")

    password = getpass.getpass("Mot de passe : ")
    if confirm and password != getpass.getpass("Confirmation : "):
        raise SystemExit("Les deux saisies diffèrent.")
    return password


def _groups(raw: str | None) -> list[str]:
    return [group.strip() for group in (raw or "").split(",") if group.strip()]


async def _resolve(email: str) -> users.User:
    user = await users.get_by_email(email)
    if user is None:
        raise SystemExit(f"Aucun compte pour {email}")
    return user


async def cmd_create(args: argparse.Namespace) -> None:
    user = await users.create_user(
        args.email,
        _read_password(),
        role=args.role,
        groups=_groups(args.groups),
        display_name=args.name,
    )
    print(f"créé : {user.email}  rôle={user.role}  groupes={','.join(user.groups) or '-'}")


async def cmd_list(_: argparse.Namespace) -> None:
    everyone = await users.list_users()
    if not everyone:
        print("aucun compte")
        return
    width = max(len(user.email) for user in everyone)
    for user in everyone:
        state = "désactivé" if user.disabled else "actif"
        groups = ",".join(users.effective_groups(user))
        print(f"{user.email:<{width}}  {user.role:<6}  {state:<9}  {groups}")


async def cmd_groups(args: argparse.Namespace) -> None:
    user = await _resolve(args.email)
    await users.set_groups(user.id, _groups(args.groups))
    updated = await users.get_user(user.id)
    print(f"{updated.email} → groupes={','.join(users.effective_groups(updated))}")


async def cmd_password(args: argparse.Namespace) -> None:
    user = await _resolve(args.email)
    await users.set_password(user.id, _read_password())
    print(f"{user.email} → mot de passe remplacé")


async def cmd_role(args: argparse.Namespace) -> None:
    user = await _resolve(args.email)
    await users.set_role(user.id, args.role)
    print(f"{user.email} → rôle={args.role}")


async def cmd_disable(args: argparse.Namespace) -> None:
    user = await _resolve(args.email)
    await users.set_disabled(user.id, not args.enable)
    if not args.enable:
        # Couper les sessions de refresh : sans ça, le compte se reconnecterait
        # tout seul au prochain renouvellement. Le jeton d'ACCÈS déjà émis, lui,
        # reste valable jusqu'à sa courte expiration (AUTH_ACCESS_TTL_MINUTES) —
        # c'est le délai maximal avant coupure effective.
        await sessions.revoke_all_for_user(user.id)
    print(f"{user.email} → {'actif' if args.enable else 'désactivé'}")
    if not args.enable:
        print(
            "  note : sessions révoquées. Accès coupé au plus tard après "
            "AUTH_ACCESS_TTL_MINUTES (le jeton d'accès en cours ne se révoque pas)."
        )


async def cmd_delete(args: argparse.Namespace) -> None:
    user = await _resolve(args.email)
    await users.delete_user(user.id)
    print(f"{user.email} → supprimé (ses conversations partent en cascade)")


async def cmd_seed(args: argparse.Namespace) -> None:
    try:
        outcome = await seeding.seed(force=args.force)
    except RuntimeError as error:
        raise SystemExit(str(error)) from None

    width = max(len(account.email) for account, _ in outcome)
    for account, state in outcome:
        # Les groupes réellement opposables incluent le groupe implicite.
        groups = ",".join(sorted({*account.groups, *users.IMPLICIT_GROUPS}))
        print(f"  {state:<9} {account.email:<{width}}  {account.role:<6} [{groups}]")
        print(f"            {account.purpose}")

    # Le mot de passe n'est pas affiché, même de démonstration : la règle « aucun
    # identifiant dans une sortie de commande » n'a de valeur que si elle ne
    # souffre aucune exception. Il est écrit dans `core/seed.py`, un fichier.
    print(
        "\nComptes de DÉMONSTRATION — mot de passe commun défini par SEED_PASSWORD,\n"
        "à défaut la valeur de `agent/core/seed.py`. Jamais en production.\n"
        "L'écran de connexion les propose en un clic sur http://localhost:4311/"
    )


async def cmd_ingest(args: argparse.Namespace) -> None:
    root = Path(args.corpus)
    reglages = ragconfig.from_env()
    print(
        f"corpus  : {root}\n"
        f"modèle  : {embed.describe()}\n"
        f"index   : fragments de ~{reglages.chunk_tokens} tokens, recouvrement "
        f"{reglages.chunk_overlap} paragraphe(s), "
        f"{'préfixe contextuel vectorisé' if reglages.contextual else 'fragments nus'}"
    )
    if embed.provider() == "hash":
        print(
            "  ⚠ fournisseur `hash` : la chaîne est exercée, mais la pertinence\n"
            "    des résultats ne veut RIEN dire. Pour de vrai : EMBEDDING_PROVIDER=openai."
        )

    try:
        report = await ingest.ingest(
            root,
            dry_run=args.dry_run,
            max_chunks=args.max_chunks,
            force=args.force,
        )
    except ingest.BudgetExceeded as error:
        raise SystemExit(f"plafond dépassé : {error}") from None
    except ingest.PruneTooLarge as error:
        raise SystemExit(f"suppression massive refusée : {error}") from None
    except FileNotFoundError as error:
        raise SystemExit(str(error)) from None

    for document in report.documents:
        if document.action == "inchangé" and not args.verbose:
            continue
        groups = ",".join(document.groups) if document.groups else "-"
        detail = f"  ({document.message})" if document.message else ""
        print(f"  {document.action:<9} {document.source}  [{groups}] "
              f"{document.chunks or ''}{detail}")

    cost = embed.cost_per_million_tokens()
    estimation = ""
    if cost is not None and report.tokens:
        estimation = f"  ≈ {report.tokens / 1_000_000 * cost:.4f} $"

    print(
        f"\n{report.indexed} indexé(s), {report.unchanged} inchangé(s), "
        f"{report.removed} supprimé(s), {report.failed} en erreur — "
        f"{report.chunks} fragment(s), ~{report.tokens} tokens{estimation}"
    )
    if report.dry_run:
        print("(simulation : rien n'a été vectorisé ni écrit)")
    if report.failed:
        raise SystemExit(1)


async def cmd_rag_reset(_: argparse.Namespace) -> None:
    await ragdb.reset()
    print("index vidé — relancez `make ingest`")


async def cmd_rag_stats(_: argparse.Namespace) -> None:
    async with ragdb.pool().acquire() as connection:
        documents = await connection.fetch(
            """
            SELECT acl, count(*) AS documents, sum(chunk_count) AS chunks,
                   min(embed_model) AS model, min(embed_dim) AS dim
            FROM rag_documents GROUP BY acl ORDER BY acl
            """
        )
    if not documents:
        print("index vide")
        return
    for row in documents:
        print(
            f"  {','.join(row['acl']) or '-':<20} {row['documents']:>4} doc  "
            f"{row['chunks'] or 0:>5} fragments   {row['model']} ({row['dim']})"
        )


def _avertissement_hash() -> None:
    if embed.provider() == "hash":
        print(
            "\n⚠ mesuré avec le vectoriseur `hash` : ces scores ne disent RIEN de la\n"
            "  pertinence réelle. Relancez avec EMBEDDING_PROVIDER=openai ou =ollama."
        )


def _fuites(report: evaluate.Report) -> None:
    if not report.leaks:
        print("aucune fuite d'ACL détectée")
        return
    print("\nFUITES D'ACL — un document restreint est remonté sans les droits :")
    for leak in report.leaks:
        print(f"  {leak.source}  ← « {leak.question} »  vu par {','.join(leak.seen_by)}")
    raise SystemExit(1)


def _resume(report: evaluate.Report) -> None:
    positifs, negatifs = len(report.positives), len(report.negatives)
    print(
        f"\nrappel@{report.k}          : {report.recall:>6.1%}  ({report.hits}/{positifs})\n"
        f"MRR                  : {report.mrr:>6.3f}\n"
        f"couverture du fait   : {report.fact_coverage:>6.1%}  "
        f"(le texte rendu contient la réponse, sur {report.fact_cases} cas)\n"
        f"abstention correcte  : {report.abstention_correcte:>6.1%}  "
        f"(sur {negatifs} négatifs difficiles)\n"
        f"abstention abusive   : {report.abstention_abusive:>6.1%}  "
        f"(sur {positifs} cas qui avaient une réponse)\n"
        f"exactitude globale   : {report.exactitude:>6.1%}  ({report.total} cas)"
    )


async def cmd_rag_eval(args: argparse.Namespace) -> None:
    try:
        cases = evaluate.load(Path(args.questions))
    except FileNotFoundError:
        raise SystemExit(f"jeu de questions introuvable : {args.questions}") from None
    except ValueError as error:
        raise SystemExit(str(error)) from None

    config = ragconfig.from_env()
    print(f"embeddings : {embed.describe()}")
    if (
        config.multi_query
        or config.hyde_documents
        or config.rerank != "none"
        or args.ablation
    ):
        print(f"modèle auxiliaire : {llm.describe()}")
    print(f"{len(cases)} cas — configuration « {config.label()} »\n")

    if args.calibrate:
        await _calibration(cases, args)
        return

    if args.ablation:
        await _ablation(cases, args)
        return

    report = await evaluate.run(cases, k=args.k, config=config, concurrency=args.concurrency)

    for result in report.results:
        if result.correct and not args.verbose:
            continue
        if result.case.abstain:
            marque, detail = "RATÉ", "aurait dû s'abstenir"
        elif result.abstained:
            marque, detail = "RATÉ", f"abstention abusive — {result.reason}"
        elif result.hit:
            marque, detail = "ok  ", f"#{result.found_rank}"
        else:
            marque, detail = "RATÉ", "document attendu absent du top-k"
        print(f"  {marque} {result.case.question}")
        print(f"       {detail}")
        if not result.case.abstain and not result.hit and not result.abstained:
            print(f"       attendu : {', '.join(result.case.expect[:3])}")
            print(f"       obtenu  : {', '.join(result.sources) or '(rien)'}")

    _resume(report)
    _fuites(report)
    _avertissement_hash()


async def _calibration(cases: list[evaluate.Case], args: argparse.Namespace) -> None:
    """La courbe du seuil, et le seuil qu'elle désigne.

    Un seuil « choisi » est un réglage que personne ne saura revoir. Un seuil
    déduit d'un critère écrit se recalcule sur un autre corpus par la même
    commande — et se conteste, ce qui vaut mieux.
    """
    courbe = await evaluate.calibrate(cases, k=args.k, concurrency=args.concurrency)
    retenu = courbe.best()

    print("  seuil   rappel@k   abst. correcte   abst. abusive   exactitude")
    print("  " + "─" * 62)
    for point in courbe.points:
        marque = "  ← retenu" if point.seuil == retenu.seuil else ""
        print(
            f"  {point.seuil:>5.1f}   {point.recall:>8.1%}   {point.abstention_correcte:>14.1%}"
            f"   {point.abstention_abusive:>13.1%}   {point.exactitude:>10.1%}{marque}"
        )

    print(
        f"\n  critère : {courbe.critere}\n"
        f"  seuil retenu : RAG_MIN_RERANK_SCORE={retenu.seuil:g}\n"
        f"    → rappel {retenu.recall:.1%}, abstention correcte "
        f"{retenu.abstention_correcte:.1%}, abusive {retenu.abstention_abusive:.1%}\n"
        "\n  Le seuil n'agit qu'APRÈS le reclassement : toute la courbe est\n"
        "  calculée sur une seule passe de notation, donc sans la variance\n"
        "  qu'auraient introduite vingt-et-un appels de modèle distincts.\n"
        "  Ce seuil vaut pour CE corpus et CE modèle de reclassement ; il se\n"
        "  recalcule par la même commande partout ailleurs."
    )
    _avertissement_hash()


_METRIQUES_REPETEES = (
    ("rattachement client exact", "clients_exacts", "n"),
    ("rattachement client ERRONÉ", "clients_errones", "pire"),
    ("rattachement contrat exact", "contrats_exacts", "n"),
    ("rattachement contrat ERRONÉ", "contrats_errones", "pire"),
    ("intention exacte", "intentions_exactes", "n"),
    ("section du référentiel exacte", "sections_exactes", "n"),
    ("pièces exigées réclamées", "pieces_trouvees", "n"),
    ("pièces réclamées à tort", "pieces_a_tort", "pire"),
    ("pièces INVENTÉES", "pieces_inventees", "pire"),
    ("brouillons CONTAMINÉS", "contaminations", "pire"),
)


async def _mesure_repetee(lot, args: argparse.Namespace, groupes: list[str]) -> None:
    """Le même lot, plusieurs fois, pour séparer le signal de la variance.

    Le premier lot est déjà traité : on le réutilise et on n'en refait que
    `repeat - 1`. Refaire le premier ne servirait qu'à dépenser des appels.
    """
    from agent.core.broker import evaluate as broker_eval
    from agent.core.broker import pipeline

    repetees = broker_eval.Repeated(rapports=[broker_eval.evaluate(lot)])
    for tour in range(2, args.repeat + 1):
        print(f"  exécution {tour}/{args.repeat}…")
        autre = await pipeline.process_mailbox(
            Path(args.mailbox), groupes, concurrency=args.concurrency, limit=args.limit
        )
        repetees.rapports.append(broker_eval.evaluate(autre))

    total = repetees.total
    print(f"\n  MESURE SUR {repetees.runs} EXÉCUTIONS — {total} courriels")
    print("  " + "─" * 74)
    print(f"  {'métrique':<32}{'moyenne':>12}{'min':>10}{'max':>10}{'étendue':>10}")
    print("  " + "─" * 74)

    for libelle, attribut, lecture in _METRIQUES_REPETEES:
        moyenne, mini, maxi = repetees.stats(attribut)
        etendue = maxi - mini
        alerte = "  ⚠" if lecture == "pire" and maxi > 0 else ""
        print(
            f"  {libelle:<32}{moyenne:>11.1f}{mini:>10.0f}{maxi:>10.0f}"
            f"{etendue:>10.0f}{alerte}"
        )

    fautes = {
        libelle: repetees.jamais_pire_que(attribut)
        for libelle, attribut, lecture in _METRIQUES_REPETEES
        if lecture == "pire"
    }
    graves = {libelle: valeur for libelle, valeur in fautes.items() if valeur > 0}

    print(
        "\n  Les lignes « ERRONÉ », « INVENTÉES » et « CONTAMINÉS » se lisent sur\n"
        "  leur MAXIMUM, pas sur leur moyenne : une faute grave commise une fois\n"
        "  sur trois exécutions reste une faute grave. Les autres se lisent sur la\n"
        "  moyenne, et l'étendue dit combien on peut s'y fier.\n"
        "\n"
        "  Un modèle à température zéro n'est PAS déterministe. Sur quinze\n"
        "  courriels, un cas vaut près de sept points : publier un tirage unique\n"
        "  reviendrait à publier de la chance."
    )

    if graves:
        for libelle, valeur in graves.items():
            print(f"\n  ⚠ {libelle} : jusqu'à {valeur:.0f} sur une exécution")
        raise SystemExit(1)
    print("\n  aucune faute grave sur aucune des exécutions")


_COLONNES = ("rappel@k", "couverture", "MRR", "abst. ok", "abst. abusive", "exactitude")


async def _ablation(cases: list[evaluate.Case], args: argparse.Namespace) -> None:
    """Le tableau qui répond à « qu'est-ce que chaque brique apporte ? ».

    Chaque ligne ne diffère de la référence que par UN élément. L'écart affiché
    est donc imputable à cet élément et à rien d'autre — c'est toute la valeur du
    tableau, et c'est ce qu'un simple « avant / après » ne peut pas donner.
    """
    lignes = await evaluate.run_ablation(cases, k=args.k, concurrency=args.concurrency)

    reference = next(
        (ligne.report for ligne in lignes if "référence" in ligne.label),
        lignes[0].report,
    )

    largeur = max(len(ligne.label) for ligne in lignes)
    entete = f"  {'configuration':<{largeur}} " + " ".join(f"{c:>13}" for c in _COLONNES)
    print("\n" + entete)
    print("  " + "─" * (len(entete) - 2))

    for ligne in lignes:
        rapport = ligne.report
        ecart = ligne.delta(reference)
        marque = "" if rapport is reference else f"  ({ecart['exactitude']:+.1%})"
        print(
            f"  {ligne.label:<{largeur}} "
            f"{rapport.recall:>12.1%} "
            f"{rapport.fact_coverage:>13.1%} "
            f"{rapport.mrr:>13.3f} "
            f"{rapport.abstention_correcte:>13.1%} "
            f"{rapport.abstention_abusive:>13.1%} "
            f"{rapport.exactitude:>13.1%}"
            f"{marque}"
        )

    print(
        "\n  L'écart entre parenthèses est l'exactitude globale comparée à la\n"
        "  référence hybride. « Tout cumulé » n'est presque jamais la somme des\n"
        "  gains isolés — deux techniques corrigent souvent les mêmes cas.\n"
        "\n"
        "  « couverture » = le TEXTE rendu contient-il réellement la réponse ?\n"
        "  L'écart avec le rappel est exactement ce que le rappel surestime : le\n"
        "  bon document retrouvé, mais le fragment d'à côté. C'est la seule\n"
        "  colonne que l'élargissement au voisinage peut faire bouger, puisqu'il\n"
        "  agit sur le texte et non sur le classement."
    )

    fuites = sum(len(ligne.report.leaks) for ligne in lignes)
    if fuites:
        print(f"\n  ⚠ {fuites} fuite(s) d'ACL au total — voir `make eval` sans --ablation")
        raise SystemExit(1)
    print("  aucune fuite d'ACL, quelle que soit la configuration")
    _avertissement_hash()


# --- Verticale courtage : courriel → dossier ---------------------------------


def _ligne_dossier(case) -> str:
    urgence = {"haute": "haute ", "moyenne": "moyen ", "basse": "basse "}
    rattachement = case.link.client_nom or "— non rattaché —"
    if case.link.contract:
        rattachement += f" · {case.link.contract}"
    elif case.link.ambiguous:
        rattachement += f" · {len(case.link.candidates)} contrats possibles"
    return (
        f"  {case.badge} {urgence.get(case.classification.urgence, '      ')}"
        f"{case.classification.intention:<20} {rattachement:<46} {case.title[:40]}"
    )


async def cmd_broker_run(args: argparse.Namespace) -> None:
    from agent.core.broker import evaluate as broker_eval
    from agent.core.broker import pipeline

    groupes = _groups(args.groups) or ["gestion", "sinistres", "production", "public"]
    print(f"boîte     : {args.mailbox}")
    print(f"groupes   : {', '.join(groupes)}")
    print(f"modèle    : {llm.describe()}")
    print(f"recherche : {ragconfig.from_env().label()}\n")

    try:
        lot = await pipeline.process_mailbox(
            Path(args.mailbox), groupes, concurrency=args.concurrency, limit=args.limit
        )
    except (FileNotFoundError, RuntimeError, ValueError) as error:
        raise SystemExit(str(error)) from None

    print("  FILE DE TRAVAIL")
    print("  " + "─" * 116)
    for case in lot.cases:
        print(_ligne_dossier(case))

    print(
        f"\n  🔴 urgent {len(lot.urgent)}   "
        f"🟢🟠 à traiter {len(lot.by_status('a_traiter'))}   "
        f"🟡 à valider {len(lot.by_status('a_valider'))}   "
        f"⚪ hors périmètre {len(lot.by_status('hors_perimetre'))}"
    )

    if args.detail:
        for case in lot.cases:
            if case.status == "hors_perimetre":
                continue
            print("\n" + "═" * 100)
            print(f"{case.badge} {case.title}   ←   {case.message.sender}")
            print(f"   {case.classification.resume}")
            print(
                f"   rattachement : {case.link.client_nom or 'aucun'}"
                f"{' · ' + case.link.contract if case.link.contract else ''}"
                f"   (confiance {case.link.confidence:.0%}"
                f", voie « {case.link.method or 'aucune'} »)"
            )
            for preuve in case.link.evidence:
                print(f"      · {preuve}")
            if case.checklist.manquantes:
                print("   pièces à réclamer :")
                for piece in case.checklist.manquantes:
                    print(f"      · {piece.piece}")
            elif not case.checklist.evaluated:
                print(f"   pièces : non évaluées ({case.checklist.reason})")
            if case.draft.body:
                print(f"   brouillon ({case.draft.kind}) :")
                for ligne in case.draft.body.splitlines():
                    print(f"      │ {ligne}")
                if case.draft.sources:
                    print(f"   sources : {', '.join(case.draft.sources)}")

    if not args.measure:
        return

    if args.repeat > 1:
        await _mesure_repetee(lot, args, groupes)
        return

    rapport = broker_eval.evaluate(lot)
    if not rapport.total:
        print("\n  (aucune vérité terrain dans cette boîte : rien à mesurer)")
        return

    print("\n  MESURE CONTRE LA VÉRITÉ TERRAIN")
    print("  " + "─" * 60)
    print(
        f"  rattachement client   exact {rapport.clients_exacts}/{rapport.total}"
        f"   manqué {rapport.clients_manques}"
        f"   ERRONÉ {rapport.clients_errones}\n"
        f"  rattachement contrat  exact {rapport.contrats_exacts}/{rapport.total}"
        f"   ERRONÉ {rapport.contrats_errones}\n"
        f"  intention             exacte {rapport.intentions_exactes}/{rapport.total}\n"
        f"  section du référentiel exacte {rapport.sections_exactes}/{rapport.sections_evaluees}\n"
        f"  pièces exigées        réclamées {rapport.pieces_trouvees}/{rapport.pieces_attendues}"
        f"   ({rapport.pieces_rappel:.0%})\n"
        f"  pièces réclamées à tort {rapport.pieces_a_tort}"
        f"   ·  INVENTÉES {rapport.pieces_inventees}\n"
        f"  brouillons CONTAMINÉS {rapport.contaminations}"
        f"   (référence d'un autre dossier)\n"
        f"  envoyés en validation {rapport.a_valider}"
    )

    for titre, refs in rapport.details_contamination():
        print(f"    ⚠ {titre[:44]:<46} cite {', '.join(refs)}")

    print("\n  rattachement client, par difficulté")
    for difficulte, (exacts, total) in rapport.par_difficulte().items():
        print(f"    {difficulte:<12} {exacts}/{total}")

    echecs = rapport.echecs_pieces()
    if echecs:
        print("\n  pièces exigées non réclamées")
        for titre, pieces in echecs:
            print(f"    {titre[:44]:<46} {', '.join(pieces)}")

    print(
        "\n  « ERRONÉ » est le seul chiffre qui doit rester à zéro : un dossier\n"
        "  rattaché au mauvais client produit un brouillon crédible et faux.\n"
        "  « manqué » coûte du temps ; « erroné » coûte le client."
    )
    if rapport.clients_errones or rapport.contrats_errones or rapport.contaminations:
        raise SystemExit(1)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent.cli", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    user = commands.add_parser("user", help="gestion des comptes").add_subparsers(
        dest="action", required=True
    )

    create = user.add_parser("create", help="crée un compte")
    create.add_argument("--email", required=True)
    create.add_argument("--role", choices=users.ROLES, default="member")
    create.add_argument("--groups", help="liste séparée par des virgules")
    create.add_argument("--name", help="nom affiché")
    create.set_defaults(run=cmd_create)

    listing = user.add_parser("list", help="liste les comptes")
    listing.set_defaults(run=cmd_list)

    groups = user.add_parser("groups", help="remplace les groupes d'un compte")
    groups.add_argument("--email", required=True)
    groups.add_argument("--groups", default="")
    groups.set_defaults(run=cmd_groups)

    password = user.add_parser("password", help="remplace le mot de passe")
    password.add_argument("--email", required=True)
    password.set_defaults(run=cmd_password)

    role = user.add_parser("role", help="change le rôle")
    role.add_argument("--email", required=True)
    role.add_argument("--role", choices=users.ROLES, required=True)
    role.set_defaults(run=cmd_role)

    disable = user.add_parser("disable", help="désactive un compte")
    disable.add_argument("--email", required=True)
    disable.set_defaults(run=cmd_disable, enable=False)

    enable = user.add_parser("enable", help="réactive un compte")
    enable.add_argument("--email", required=True)
    enable.set_defaults(run=cmd_disable, enable=True)

    delete = user.add_parser("delete", help="supprime un compte")
    delete.add_argument("--email", required=True)
    delete.set_defaults(run=cmd_delete)

    amorce = commands.add_parser(
        "seed", help="crée les comptes de démonstration (mot de passe public — DEV)"
    )
    amorce.add_argument(
        "--force",
        action="store_true",
        help="passe outre le garde-fou de production (AUTH_COOKIE_SECURE)",
    )
    amorce.set_defaults(run=cmd_seed, needs=("db",))

    indexation = commands.add_parser("ingest", help="indexe le corpus dans l'index RAG")
    indexation.add_argument("--corpus", default="/app/corpus", help="racine du corpus")
    indexation.add_argument(
        "--dry-run", action="store_true", help="montre le plan sans rien vectoriser"
    )
    indexation.add_argument(
        "--max-chunks", type=int, default=None, help="plafond de fragments pour ce lot"
    )
    indexation.add_argument(
        "--verbose", action="store_true", help="liste aussi les documents inchangés"
    )
    indexation.add_argument(
        "--force",
        action="store_true",
        help="autorise une synchronisation qui supprime plus de la moitié de l'index",
    )
    indexation.set_defaults(run=cmd_ingest, needs=("ragdb",))

    rag = commands.add_parser("rag", help="index documentaire").add_subparsers(
        dest="action", required=True
    )
    reset = rag.add_parser("reset", help="vide l'index (les conversations restent)")
    reset.set_defaults(run=cmd_rag_reset, needs=("ragdb",))
    stats = rag.add_parser("stats", help="documents et fragments par groupe")
    stats.set_defaults(run=cmd_rag_stats, needs=("ragdb",))

    mesure = rag.add_parser(
        "eval", help="rappel@k, MRR, abstention et contrôle de fuite d'ACL"
    )
    mesure.add_argument("--questions", default="/app/eval/questions.yaml")
    mesure.add_argument("--k", type=int, default=None, help="profondeur mesurée")
    mesure.add_argument(
        "--calibrate",
        action="store_true",
        help="déduit le seuil d'abstention d'un critère écrit, au lieu de le choisir",
    )
    mesure.add_argument(
        "--ablation",
        action="store_true",
        help="rejoue le jeu sous plusieurs configurations et compare leur apport",
    )
    mesure.add_argument(
        "--concurrency",
        type=int,
        default=evaluate.DEFAULT_CONCURRENCY,
        help="recherches menées de front (baisser en cas de 429 du fournisseur)",
    )
    mesure.add_argument(
        "--verbose", action="store_true", help="liste aussi les cas réussis"
    )
    mesure.set_defaults(run=cmd_rag_eval, needs=("ragdb",))

    courtage = commands.add_parser(
        "inbox", help="traite une boîte de courriels en dossiers préparés"
    ).add_subparsers(dest="action", required=True)

    traitement = courtage.add_parser("run", help="courriel → dossier, pour toute la boîte")
    traitement.add_argument("--mailbox", default="/app/mailbox", help="dossier des .eml")
    traitement.add_argument(
        "--groups",
        default="",
        help="groupes du collaborateur (défaut : gestion,sinistres,production,public)",
    )
    traitement.add_argument("--limit", type=int, default=None, help="n premiers courriels")
    traitement.add_argument(
        "--concurrency", type=int, default=3, help="dossiers traités de front"
    )
    traitement.add_argument(
        "--detail", action="store_true", help="affiche rattachement, pièces et brouillon"
    )
    traitement.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="répète la mesure n fois et rend moyenne et étendue (variance du modèle)",
    )
    traitement.add_argument(
        "--measure",
        action="store_true",
        help="compare à la vérité terrain portée par les en-têtes X-Attendu-*",
    )
    traitement.set_defaults(run=cmd_broker_run, needs=("ragdb",))

    return parser


async def _main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    # Les commandes de comptes parlent à la base des conversations, celles du RAG
    # à la base vectorielle : n'ouvrir que ce qui sert évite d'échouer sur une
    # base absente qui n'avait rien à voir avec la commande demandée.
    needs = getattr(args, "needs", ("db",))

    if "db" in needs:
        await db.connect()
    if "ragdb" in needs:
        await ragdb.connect()
    try:
        await args.run(args)
    finally:
        if "db" in needs:
            await db.disconnect()
        if "ragdb" in needs:
            await ragdb.disconnect()


def main(argv: list[str] | None = None) -> None:
    try:
        asyncio.run(_main(argv))
    except (ValueError, RuntimeError) as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()
