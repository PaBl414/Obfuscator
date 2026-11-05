"""Flatten straight-line synchronous functions into local state dispatchers."""

from __future__ import annotations

import ast
import io
import random
import tokenize

from .flow_obfuscator import SafetyVisitor

_SUPPORTED_STATEMENTS = (
    ast.Assign,
    ast.AnnAssign,
    ast.AugAssign,
    ast.Expr,
    ast.Return,
    ast.If,
    ast.Pass,
    ast.Assert,
    ast.Raise,
    ast.For,
    ast.While,
    ast.Try,
    ast.With,
    ast.Match,
    ast.FunctionDef,
    ast.ClassDef,
)
if hasattr(ast, "TryStar"):
    _SUPPORTED_STATEMENTS += (ast.TryStar,)


class _FunctionSafetyVisitor(SafetyVisitor):
    """Allow loop control local to nested loops in an eligible function."""

    def visit_Break(self, node: ast.Break) -> None:
        pass

    def visit_Continue(self, node: ast.Continue) -> None:
        pass


def _supported_suite(statements: list[ast.stmt]) -> bool:
    for statement in statements:
        if isinstance(statement, ast.If):
            if not _supported_suite(statement.body) or not _supported_suite(statement.orelse):
                return False
        elif not isinstance(statement, _SUPPORTED_STATEMENTS):
            return False
    return True


def _contains_docstring(function: ast.FunctionDef) -> bool:
    return bool(
        function.body
        and isinstance(function.body[0], ast.Expr)
        and isinstance(function.body[0].value, ast.Constant)
        and isinstance(function.body[0].value.value, str)
    )


def _safe_to_transform(function: ast.FunctionDef) -> bool:
    body = function.body[1:] if _contains_docstring(function) else function.body
    if function.decorator_list or not body or not _supported_suite(body):
        return False
    if any(isinstance(node, (ast.Yield, ast.YieldFrom, ast.Await)) for node in ast.walk(function)):
        return False

    safety = _FunctionSafetyVisitor(ast.Module(body=[function], type_ignores=[]))
    safety.visit(function)
    return not safety.reasons


def _fresh_state_name(rng: random.Random, used: set[str]) -> str:
    while True:
        name = f"_local_flow_{rng.getrandbits(64):016x}"
        if name not in used:
            used.add(name)
            return name


def _dispatcher_source(
    statements: list[ast.stmt], state_name: str, rng: random.Random
) -> str:
    count = len(statements)
    state_ids = rng.sample(range(100_000, 2_147_483_647), count)
    dispatch_order = list(range(count))
    rng.shuffle(dispatch_order)
    valid_states = ", ".join(map(str, state_ids))
    if count == 1:
        valid_states += ","

    lines = [f"{state_name} = {state_ids[0]}", f"while {state_name} in ({valid_states}):"]
    for position, original_index in enumerate(dispatch_order):
        keyword = "if" if position == 0 else "elif"
        state_id = state_ids[original_index]
        next_state = state_ids[original_index + 1] if original_index + 1 < count else 0
        lines.append(f"    {keyword} {state_name} == {state_id}:")
        rendered = ast.unparse(statements[original_index]).splitlines()
        lines.extend(f"        {line}" for line in rendered)
        lines.append(f"        {state_name} = {next_state}")
    lines.append(f"del {state_name}")
    return "\n".join(lines)


def flatten_simple_functions(
    source: str, seed: int | None = None
) -> tuple[str, int]:
    """Flatten supported function bodies and leave other functions unchanged.

    Undecorated synchronous functions with supported statements are eligible.
    Yield, await, dynamic namespace access, and unsupported statements cause
    the function to be skipped. Loops and exception handlers remain intact
    inside a single dispatcher state.
    """
    tree = ast.parse(source, type_comments=True)
    rng = random.Random(seed)
    used = {
        token.string
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type == tokenize.NAME
    }
    transformed_count = 0

    class FunctionTransformer(ast.NodeTransformer):
        def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
            nonlocal transformed_count
            self.generic_visit(node)
            if not _safe_to_transform(node):
                return node

            docstring = [node.body[0]] if _contains_docstring(node) else []
            statements = node.body[1:] if docstring else node.body
            state_name = _fresh_state_name(rng, used)
            dispatcher = ast.parse(
                _dispatcher_source(statements, state_name, rng)
            ).body
            node.body = docstring + dispatcher
            transformed_count += 1
            return node

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AST:
            return self.generic_visit(node)

    transformed_tree = FunctionTransformer().visit(tree)
    if transformed_count == 0:
        return source, 0
    ast.fix_missing_locations(transformed_tree)
    result = ast.unparse(transformed_tree) + "\n"
    ast.parse(result, type_comments=True)
    compile(result, "<function-flow-obfuscation>", "exec")
    return result, transformed_count
