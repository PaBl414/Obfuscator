"""Flatten eligible function bodies without extracting their lexical scopes."""

from __future__ import annotations

import ast
import io
import keyword
import random
import string
import tokenize
from dataclasses import dataclass

from .flow_obfuscator import SafetyVisitor
from .dispatch import table_dispatch

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
    ast.AsyncFor,
    ast.AsyncWith,
)
if hasattr(ast, "TryStar"):
    _SUPPORTED_STATEMENTS += (ast.TryStar,)


# Słowa, które zdradzają obfuskator — nigdy nie używać w nazwach stanu.
_OBFUSCATOR_WORDS = frozenset(
    {
        "state", "dispatcher", "dispatch", "flow", "flatten", "obf",
        "obfuscate", "vm", "switch", "jmp", "jump", "label", "node",
        "block", "machine", "fsm", "control", "cfg", "opaque",
    }
)

_STATE_PREFIXES = ("_", "__", "_tmp_", "_v", "_p", "_acc", "_buf",
                   "_res", "_cur", "_last", "_it", "_seq", "_n", "")
_STATE_SUFFIXES = ("", "_", "_0", "_1", "_2", "_p", "_q", "_x",
                   "_idx", "_tmp", "_val", "_res", "_n")


class _FunctionSafetyVisitor(SafetyVisitor):
    """Allow loop control local to nested loops in an eligible function."""

    def visit_Break(self, node: ast.Break) -> None:
        pass

    def visit_Continue(self, node: ast.Continue) -> None:
        pass


@dataclass
class _DispatchBlock:
    statement: ast.stmt | None = None
    test: ast.expr | None = None
    next_state: int = 0
    true_state: int = 0
    false_state: int = 0


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


def _safe_to_transform(function: ast.FunctionDef, allow_introspection: bool = False,
                       extended: bool = False) -> bool:
    return not _skip_reasons(function, allow_introspection, extended)


def _skip_reasons(function, allow_introspection=False, extended=False):
    body = function.body[1:] if _contains_docstring(function) else function.body
    reasons = []
    if function.decorator_list:
        reasons.append("decorated function")
    if not body:
        reasons.append("empty body")
    if not _supported_suite(body):
        reasons.append("unsupported statement")
    if isinstance(function, ast.AsyncFunctionDef) and not extended:
        reasons.append("async flow requires extended_flow")
    if not extended and any(isinstance(node, (ast.Yield, ast.YieldFrom, ast.Await)) for node in ast.walk(function)):
        reasons.append("suspension requires extended_flow")

    safety = _FunctionSafetyVisitor(
        ast.Module(body=[function], type_ignores=[]), allow_introspection=allow_introspection
    )
    safety.visit(function)
    return list(dict.fromkeys(reasons + safety.reasons))


def assess_functions(source: str, *, allow_introspection=False, extended=False):
    """Describe eligibility of input functions; later stages can change it."""
    records = []
    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self.scope = []

        def visit_FunctionDef(self, node):
            self.scope.append(node.name)
            reasons = _skip_reasons(node, allow_introspection, extended)
            records.append({"function": ".".join(self.scope), "line": node.lineno,
                            "eligible": not reasons, "reasons": reasons})
            self.generic_visit(node)
            self.scope.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_ClassDef(self, node):
            self.scope.append(node.name)
            self.generic_visit(node)
            self.scope.pop()
    Visitor().visit(ast.parse(source))
    return records


def _fresh_state_name(rng: random.Random, used: set[str]) -> str:
    """Generate an identifier that looks like an ordinary local variable.

    Uses a random prefix/suffix from a pool of natural-looking tokens, a short
    random core, and avoids names that signal an obfuscator. Prefers short
    names (1–3 chars core) to match typical local variables.
    """
    for _ in range(10_000):
        prefix = rng.choice(_STATE_PREFIXES)
        suffix = rng.choice(_STATE_SUFFIXES)
        core_len = rng.choice((1, 1, 2, 2, 3))
        core = "".join(rng.choice(string.ascii_lowercase) for _ in range(core_len))
        name = f"{prefix}{core}{suffix}"
        if (
            name.isidentifier()
            and not keyword.iskeyword(name)
            and name not in used
            and not any(word in name.lower() for word in _OBFUSCATOR_WORDS)
        ):
            used.add(name)
            return name
    # Awaryjnie: unikalny identyfikator, nadal bez słów-kluczy obfuskatorów.
    while True:
        name = "_" + "".join(rng.choice(string.ascii_lowercase) for _ in range(4))
        if name not in used:
            used.add(name)
            return name


def _state_token(state_id: int, rng: random.Random) -> str:
    """Encode a numeric state id as a short random string token.

    Manglers do not touch string literals, so the dispatcher's case labels
    remain stable and unrecognisable (no large integers to grep for).
    """
    length = rng.choice((4, 5, 6))
    alphabet = string.ascii_lowercase + string.digits
    return "".join(rng.choice(alphabet) for _ in range(length))


def _state_predicate(state_name: str, state_token: str, rng: random.Random) -> str:
    """Return a predicate equivalent to ``state_name == state_token``.

    All variants are correct for any integer or string ``state_name`` when
    ``state_token`` is a string (comparison), and for integers when token is
    an int. None is a tautology, so testing on a random value yields ``False``
    and does not reveal the obfuscation.
    """
    variants = (
        f"({state_name} ^ {state_token}) == 0",
        f"({state_name} - {state_token}) == 0",
        f"not ({state_name} != {state_token})",
        f"not ({state_name} ^ {state_token})",
        f"({state_name} - {state_token}) * ({state_name} - {state_token}) == 0",
        f"({state_name} ^ {state_token}) < 1",
        f"({state_name} ^ {state_token}) <= 0",
    )
    return rng.choice(variants)


def _state_predicate_for_token(
    state_name: str, state_token: str, rng: random.Random
) -> str:
    """Predicate equivalent to ``state_name == state_token`` for string tokens.

    Uses only string-safe operations: equality and membership in a singleton
    tuple. No ``is`` (would emit ``SyntaxWarning`` and break after XOR
    encryption, which destroys string interning), no arithmetic, no
    tautologies.
    """
    variants = (
        f"({state_name} == {state_token!r})",
        f"({state_name} in ({state_token!r},))",
        f"not ({state_name} != {state_token!r})",
        f"({state_name} == {state_token!r} and {state_name} == {state_token!r})",
        f"({state_token!r} == {state_name})",
    )
    predicate = rng.choice(variants)
    # Losowo podwójna negacja — łamie regex ``if .* == ...:``
    if rng.random() < 0.3:
        predicate = f"not not ({predicate})"
    return predicate


def _dispatcher_source(
    statements: list[ast.stmt], state_name: str, rng: random.Random,
    dispatch_mode: str = "branches",
) -> str:
    blocks: list[_DispatchBlock] = []

    def build_suite(suite: list[ast.stmt], successor: int) -> int:
        entry = successor
        for statement in reversed(suite):
            if isinstance(statement, ast.If):
                true_state = build_suite(statement.body, entry)
                false_state = build_suite(statement.orelse, entry)
                blocks.append(
                    _DispatchBlock(
                        test=statement.test,
                        true_state=true_state,
                        false_state=false_state,
                    )
                )
            else:
                blocks.append(
                    _DispatchBlock(statement=statement, next_state=entry)
                )
            entry = len(blocks)
        return entry

    entry_state = build_suite(statements, 0)
    count = len(blocks)
    # Unikalne tokeny stanów (stringi) — mangler ich nie tknie.
    tokens: list[str] = []
    seen: set[str] = set()
    while len(tokens) < count:
        token = _state_token(0, rng)
        if token not in seen:
            seen.add(token)
            tokens.append(token)
    dispatch_order = list(range(count))
    rng.shuffle(dispatch_order)
    valid_tokens = ", ".join(repr(t) for t in tokens)
    if count == 1:
        valid_tokens += ","

    def encoded_state(logical_state: int) -> str:
        return repr("end") if logical_state == 0 else repr(tokens[logical_state - 1])

    # Mniej rozpoznawalna forma pętli: ``while state:`` + wewnętrzny break.
    lines = [
        f"{state_name} = {encoded_state(entry_state)}",
        f"while {state_name}:",
        f"    if {state_name} not in ({valid_tokens}):",
        "        break",
    ]
    for position, original_index in enumerate(dispatch_order):
        keyword = "if" if position == 0 else "elif"
        token = tokens[original_index]
        block = blocks[original_index]
        predicate = _state_predicate_for_token(state_name, token, rng)
        # Wcięcie o jeden poziom głębiej niż w oryginale (pętla + if/elif).
        lines.append(f"    {keyword} {predicate}:")
        if block.test is not None:
            lines.append(f"        if {ast.unparse(block.test)}:")
            lines.append(f"            {state_name} = {encoded_state(block.true_state)}")
            lines.append("        else:")
            lines.append(f"            {state_name} = {encoded_state(block.false_state)}")
        else:
            rendered = ast.unparse(block.statement).splitlines()
            lines.extend(f"        {line}" for line in rendered)
            lines.append(f"        {state_name} = {encoded_state(block.next_state)}")
    if rng.random() < 0.5:
        lines.append(f"del {state_name}")
    result = "\n".join(lines)
    if dispatch_mode == "table":
        tree = ast.parse(result)
        loop = tree.body[1]
        loop.body[1] = table_dispatch(
            loop.body[1], state_name, [tokens[index] for index in dispatch_order], rng
        )
        ast.fix_missing_locations(tree)
        return ast.unparse(tree)
    return result


def flatten_simple_functions(
    source: str, seed: int | None = None, dispatch_mode: str = "branches",
    allow_introspection: bool = False,
    extended: bool = False,
) -> tuple[str, int]:
    """Flatten supported function bodies and leave other functions unchanged.

    Undecorated functions with supported statements are eligible. Generators
    and coroutines require ``extended=True``; dynamic namespace access and
    unsupported statements cause a function to be skipped. Nested ``if`` suites become separate states;
    loops and exception handlers remain intact inside a single dispatcher
    state to preserve their control semantics.
    """
    if dispatch_mode not in {"branches", "table"}:
        raise ValueError("Unknown dispatch mode")
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
            if not _safe_to_transform(node, allow_introspection=allow_introspection, extended=extended):
                return node

            docstring = [node.body[0]] if _contains_docstring(node) else []
            statements = node.body[1:] if docstring else node.body
            state_name = _fresh_state_name(rng, used)
            dispatcher = ast.parse(
                _dispatcher_source(statements, state_name, rng, dispatch_mode)
            ).body
            node.body = docstring + dispatcher
            transformed_count += 1
            return node

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AST:
            if extended:
                return self.visit_FunctionDef(node)
            return self.generic_visit(node)

    transformed_tree = FunctionTransformer().visit(tree)
    if transformed_count == 0:
        return source, 0
    ast.fix_missing_locations(transformed_tree)
    result = ast.unparse(transformed_tree) + "\n"
    ast.parse(result, type_comments=True)
    compile(result, "<function-flow-obfuscation>", "exec")
    return result, transformed_count
