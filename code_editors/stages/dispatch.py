"""Build table-driven dispatch without extracting statements into closures."""

from __future__ import annotations

import ast
import random
from collections.abc import Sequence


def table_dispatch(
    branch: ast.If,
    state_name: str,
    state_keys: Sequence[int | str],
    rng: random.Random,
) -> ast.Match:
    """Replace a generated if/elif chain with lookup and literal match cases.

    The caller supplies keys in the same order as the generated branch chain.
    All original suites stay in the original function/module scope.
    """
    labels = rng.sample(range(100_000, 2_147_483_647), len(state_keys))
    cases: list[ast.match_case] = []
    current = branch
    for index, _ in enumerate(state_keys):
        cases.append(ast.match_case(
            pattern=ast.MatchValue(value=ast.Constant(labels[index])),
            guard=None,
            body=current.body,
        ))
        if index < len(state_keys) - 1:
            if len(current.orelse) != 1 or not isinstance(current.orelse[0], ast.If):
                raise ValueError("Invalid generated dispatch chain")
            current = current.orelse[0]
    if current.orelse:
        raise ValueError("Unexpected fallback in generated dispatch chain")
    pairs = list(zip(state_keys, labels))
    rng.shuffle(pairs)
    subject = ast.Subscript(
        value=ast.Dict(
            keys=[ast.Constant(key) for key, _ in pairs],
            values=[ast.Constant(label) for _, label in pairs],
        ),
        slice=ast.Name(id=state_name, ctx=ast.Load()),
        ctx=ast.Load(),
    )
    return ast.Match(subject=subject, cases=cases)
