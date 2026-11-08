#!/usr/bin/env python3
"""Add opaque predicates to a file already flattened by the flow obfuscator.

This script does not flatten control flow. It recognizes the dispatch loop
emitted by the supplied ``_flow_state_<hex>`` flow obfuscator and adds
side-effect-free predicates that are true for every integer state.
"""

from __future__ import annotations

import argparse
import ast
import random
import re
import sys
import tokenize
from pathlib import Path


STATE_NAME = re.compile(r"^_flow_state_[0-9a-f]{16}$")


def _char_col(line: str, byte_col: int) -> int:
    return len(line.encode("utf-8")[:byte_col].decode("utf-8", errors="ignore"))


def _span(source: str, node: ast.AST) -> tuple[int, int]:
    position_attributes = ("lineno", "col_offset", "end_lineno", "end_col_offset")
    if not all(hasattr(node, attr) for attr in position_attributes):
        raise ValueError("Python did not provide AST node positions.")
    lines = source.splitlines(keepends=True)
    starts: list[int] = []
    total = 0
    for line in lines:
        starts.append(total)
        total += len(line)
    start_line = int(getattr(node, "lineno")) - 1
    end_line = int(getattr(node, "end_lineno")) - 1
    start = starts[start_line] + _char_col(lines[start_line], int(getattr(node, "col_offset")))
    end = starts[end_line] + _char_col(lines[end_line], int(getattr(node, "end_col_offset")))
    return start, end


def _is_integer(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, int)
        and not isinstance(node.value, bool)
    )


def _dispatcher_state(tree: ast.Module) -> tuple[ast.While, str] | None:
    def membership_name(test: ast.expr) -> str | None:
        if isinstance(test, ast.Compare):
            if (
                isinstance(test.left, ast.Name)
                and test.ops
                and isinstance(test.ops[0], ast.In)
                and test.comparators
                and isinstance(test.comparators[0], (ast.Tuple, ast.List))
            ):
                return test.left.id
        if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And):
            for value in test.values:
                name = membership_name(value)
                if name is not None:
                    return name
        return None

    def membership_compare(test: ast.expr, name: str) -> ast.Compare | None:
        if (
            isinstance(test, ast.Compare)
            and isinstance(test.left, ast.Name)
            and test.left.id == name
            and test.ops
            and isinstance(test.ops[0], ast.In)
            and test.comparators
            and isinstance(test.comparators[0], (ast.Tuple, ast.List))
        ):
            return test
        if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And):
            for value in test.values:
                found = membership_compare(value, name)
                if found is not None:
                    return found
        return None

    for loop in ast.walk(tree):
        if not isinstance(loop, ast.While):
            continue
        name = membership_name(loop.test)
        if name is None:
            continue
        membership = membership_compare(loop.test, name)
        if membership is None:
            continue
        states = membership.comparators[0].elts
        if not states or not all(_is_integer(item) for item in states):
            continue
        # Identifier mangling may run between module flow and this stage.
        # Recognize the generated structure instead of requiring its spelling.
        if not any(
            (isinstance(node, ast.If) and _is_state_branch(node.test, name))
            or (
                isinstance(node, ast.Match)
                and isinstance(node.subject, ast.Subscript)
                and isinstance(node.subject.value, ast.Dict)
                and isinstance(node.subject.slice, ast.Name)
                and node.subject.slice.id == name
            )
            for node in loop.body
        ):
            continue
        return loop, name
    return None


def _is_state_branch(test: ast.expr, state_name: str) -> bool:
    if (
        isinstance(test, ast.Compare)
        and isinstance(test.left, ast.Name)
        and test.left.id == state_name
        and len(test.ops) == 1
        and isinstance(test.ops[0], ast.Eq)
        and len(test.comparators) == 1
        and _is_integer(test.comparators[0])
    ):
        return True
    return (
        isinstance(test, ast.BoolOp)
        and isinstance(test.op, ast.And)
        and any(_is_state_branch(value, state_name) for value in test.values)
    )


def _predicate(name: str, rng: random.Random) -> str:
    """Return a predicate guaranteed true when name holds an integer."""
    even = rng.randrange(2, 100_000, 2)
    options = (
        f"((({name} + {even}) * ({name} + {even} + 1)) % 2 == 0)",
        f"((({name} ^ {even}) & 1) == (({name} + {even}) & 1))",
        f"((({name} * {name} + {name}) % 2) == 0)",
    )
    return rng.choice(options)


def add_opaque_predicates(source: str, seed: int | None = None) -> tuple[str, int]:
    tree = ast.parse(source)
    found = _dispatcher_state(tree)
    if found is None:
        raise ValueError(
            "Could not find the dispatcher generated by the flow obfuscator."
        )
    loop, state_name = found
    targets: list[ast.expr] = [loop.test]
    branches = [node for node in ast.walk(ast.Module(body=loop.body, type_ignores=[]))
                if isinstance(node, ast.If) and _is_state_branch(node.test, state_name)]
    table_cases = [
        case
        for node in loop.body
        if isinstance(node, ast.Match)
        and isinstance(node.subject, ast.Subscript)
        and isinstance(node.subject.value, ast.Dict)
        and isinstance(node.subject.slice, ast.Name)
        and node.subject.slice.id == state_name
        for case in node.cases
    ]
    if not branches and not table_cases:
        raise ValueError("The flow loop contains no recognized state branches.")
    targets.extend(node.test for node in branches)

    rng = random.Random(seed)
    edits: list[tuple[int, int, str]] = []
    for test in targets:
        start, end = _span(source, test)
        original_test = source[start:end]
        edits.append((start, end, f"({original_test}) and ({_predicate(state_name, rng)})"))
    for case in table_cases:
        if case.guard is None:
            _, end = _span(source, case.pattern)
            edits.append((end, end, f" if {_predicate(state_name, rng)}"))
        else:
            start, end = _span(source, case.guard)
            edits.append((start, end, f"({source[start:end]}) and ({_predicate(state_name, rng)})"))

    rewritten = source
    for start, end, replacement in sorted(edits, reverse=True):
        rewritten = rewritten[:start] + replacement + rewritten[end:]
    tree_after = ast.parse(rewritten)
    compile(tree_after, "<opaque-predicates>", "exec")
    return rewritten, len(edits)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Add opaque predicates to a file produced by the flow obfuscator."
    )
    parser.add_argument("input", type=Path, help=".py file produced by flow obfuscation")
    parser.add_argument("output", nargs="?", type=Path,
                        help="output file (default: <name>_opaque.py)")
    parser.add_argument("--seed", type=int, help="reproducible predicates with the same seed")
    parser.add_argument("--force", action="store_true", help="overwrite an existing output file")
    args = parser.parse_args(argv)

    source_path = args.input.expanduser().resolve()
    if not source_path.is_file() or source_path.suffix.lower() != ".py":
        parser.error("Specify an existing .py file.")
    output_path = (args.output.expanduser().resolve() if args.output else
                   source_path.with_name(source_path.stem + "_opaque.py"))
    if output_path == source_path:
        parser.error("The output file must be different from the input file.")
    if output_path.exists() and not args.force:
        parser.error(f"Output file already exists: {output_path} (use --force to overwrite it).")

    try:
        with tokenize.open(str(source_path)) as handle:
            source = handle.read()
        transformed, count = add_opaque_predicates(source, args.seed)
        output_path.write_text(transformed, encoding="utf-8", newline="")
    except (OSError, SyntaxError, UnicodeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Saved: {output_path}")
    print(f"Added predicates: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
