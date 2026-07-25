"""Calculatrice à évaluateur AST sur liste blanche.

L'expression vient du LLM, lui-même orienté par l'entrée utilisateur et par ce que
les outils web ramènent : c'est de la donnée non fiable. Plutôt que de durcir un
`eval()` (jeu perdu d'avance) ou d'ajouter une dépendance d'évaluation, on parse
l'expression et on n'exécute que des nœuds explicitement autorisés.

Est refusé par construction : accès aux attributs, indexation, comprehensions,
lambdas, noms libres, et tout appel hors de la table `FUNCTIONS`.
"""

from __future__ import annotations

import ast
import math
import operator
from typing import Any

from langchain_core.tools import tool

from agent.infra.http import tool_json

MAX_EXPRESSION_LENGTH = 500
MAX_EXPONENT = 1000  # 2**10**9 mettrait le worker à genoux

BINARY_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}

FUNCTIONS: dict[str, Any] = {
    name: getattr(math, name)
    for name in (
        "sqrt", "log", "log2", "log10", "exp", "sin", "cos", "tan",
        "asin", "acos", "atan", "atan2", "floor", "ceil", "fabs",
        "degrees", "radians", "hypot", "factorial",
    )
}
FUNCTIONS.update({"abs": abs, "round": round, "min": min, "max": max})

CONSTANTS = {"pi": math.pi, "e": math.e, "tau": math.tau}


def evaluate(expression: str) -> float | int:
    if len(expression) > MAX_EXPRESSION_LENGTH:
        raise ValueError(f"Expression trop longue (> {MAX_EXPRESSION_LENGTH} caractères)")

    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as error:
        raise ValueError(f"Expression invalide : {error.msg}") from error

    return _eval(tree.body)


def _eval(node: ast.AST) -> Any:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, int | float):
            raise ValueError("Seuls les nombres sont acceptés")
        return node.value

    if isinstance(node, ast.Name):
        if node.id not in CONSTANTS:
            raise ValueError(f"Nom inconnu : {node.id}")
        return CONSTANTS[node.id]

    if isinstance(node, ast.BinOp):
        op = BINARY_OPS.get(type(node.op))
        if op is None:
            raise ValueError(f"Opérateur non autorisé : {type(node.op).__name__}")
        left, right = _eval(node.left), _eval(node.right)
        if op is operator.pow and abs(right) > MAX_EXPONENT:
            raise ValueError(f"Exposant trop grand (> {MAX_EXPONENT})")
        return op(left, right)

    if isinstance(node, ast.UnaryOp):
        op = UNARY_OPS.get(type(node.op))
        if op is None:
            raise ValueError(f"Opérateur unaire non autorisé : {type(node.op).__name__}")
        return op(_eval(node.operand))

    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in FUNCTIONS:
            raise ValueError("Appel de fonction non autorisé")
        if node.keywords:
            raise ValueError("Arguments nommés non autorisés")
        return FUNCTIONS[node.func.id](*(_eval(arg) for arg in node.args))

    raise ValueError(f"Syntaxe non autorisée : {type(node).__name__}")


@tool(parse_docstring=True)
async def calculator(expression: str) -> str:
    """Évalue une expression mathématique (arithmétique, pourcentages, trigonométrie).

    À utiliser systématiquement pour tout calcul — ne jamais calculer de tête.
    Les conversions d'unités ne sont pas supportées : convertis toi-même en
    multipliant, par exemple 90 km/h en m/s s'écrit '90 / 3.6'.

    Args:
        expression: Expression Python arithmétique, ex. '(1234 * 0.2) + 15' ou 'sqrt(16)'.
    """

    async def run() -> dict:
        return {"expression": expression, "result": evaluate(expression)}

    return await tool_json(run)
