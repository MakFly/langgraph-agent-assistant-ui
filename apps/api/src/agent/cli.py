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
from agent.core.rag import embed, evaluate, ingest
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
    print(
        f"corpus  : {root}\n"
        f"modèle  : {embed.model_name()} ({embed.dimension()} dimensions, "
        f"fournisseur {embed.provider()})"
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


async def cmd_rag_eval(args: argparse.Namespace) -> None:
    try:
        cases = evaluate.load(Path(args.questions))
    except FileNotFoundError:
        raise SystemExit(f"jeu de questions introuvable : {args.questions}") from None

    report = await evaluate.run(cases, k=args.k)

    for result in report.results:
        rang = f"#{result.found_rank}" if result.hit else "absent"
        marque = "ok " if result.hit else "RATÉ"
        print(f"  {marque} {rang:<7} {result.case.question}")
        if not result.hit:
            print(f"        attendu : {', '.join(result.case.expect)}")
            print(f"        obtenu  : {', '.join(result.sources) or '(rien)'}")

    print(
        f"\nrappel@{report.k} : {report.recall:.0%} ({report.hits}/{report.total})"
        f"    MRR : {report.mrr:.3f}"
        f"    modèle : {embed.model_name()}"
    )

    if report.leaks:
        print("\nFUITES D'ACL — un document restreint est remonté sans les droits :")
        for leak in report.leaks:
            print(f"  {leak.source}  ← « {leak.question} »  vu par {','.join(leak.seen_by)}")
        raise SystemExit(1)

    print("aucune fuite d'ACL détectée")

    if embed.provider() == "hash":
        print(
            "\n⚠ mesuré avec le vectoriseur `hash` : ce score ne dit RIEN de la\n"
            "  pertinence réelle. Relancez avec EMBEDDING_PROVIDER=openai."
        )


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

    mesure = rag.add_parser("eval", help="rappel@k, MRR et contrôle de fuite d'ACL")
    mesure.add_argument("--questions", default="/app/eval/questions.yaml")
    mesure.add_argument("--k", type=int, default=None, help="profondeur mesurée")
    mesure.set_defaults(run=cmd_rag_eval, needs=("ragdb",))

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
