"""Rewrite integer literals using equivalent Python numeric bases."""

from __future__ import annotations

import ast
import random
from .source_edits import SourceIndex, apply_edits


def _annotation_nodes(tree: ast.Module) -> set[int]:
    annotations: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            arguments = node.args
            for argument in (
                *arguments.posonlyargs,
                *arguments.args,
                *arguments.kwonlyargs,
            ):
                if argument.annotation is not None:
                    annotations.update(id(item) for item in ast.walk(argument.annotation))
            if arguments.vararg and arguments.vararg.annotation:
                annotations.update(id(item) for item in ast.walk(arguments.vararg.annotation))
            if arguments.kwarg and arguments.kwarg.annotation:
                annotations.update(id(item) for item in ast.walk(arguments.kwarg.annotation))
            if node.returns is not None:
                annotations.update(id(item) for item in ast.walk(node.returns))
        elif isinstance(node, ast.AnnAssign):
            annotations.update(id(item) for item in ast.walk(node.annotation))
    return annotations


def obfuscate_numeric_constants(
    source: str, seed: int | None = None
) -> tuple[str, int]:
    """Change integer spellings while preserving the parsed syntax tree."""
    tree = ast.parse(source, type_comments=True)
    index = SourceIndex(source)
    annotation_nodes = _annotation_nodes(tree)

    rng = random.Random(seed)
    edits: list[tuple[int, int, str]] = []
    for node in ast.walk(tree):
        if (
            not isinstance(node, ast.Constant)
            or not isinstance(node.value, int)
            or isinstance(node.value, bool)
            or id(node) in annotation_nodes
        ):
            continue

        representations = (hex(node.value), oct(node.value), bin(node.value))
        replacement = rng.choice(representations)
        start = index.offset(node.lineno, node.col_offset)
        end = index.offset(node.end_lineno, node.end_col_offset)
        edits.append((start, end, replacement))

    rewritten = apply_edits(source, edits)

    transformed_tree = ast.parse(rewritten, type_comments=True)
    if ast.dump(tree, include_attributes=False) != ast.dump(
        transformed_tree, include_attributes=False
    ):
        raise ValueError("Numeric literal rewriting changed the Python syntax tree.")
    compile(rewritten, "<numeric-obfuscation>", "exec")
    return rewritten, len(edits)
