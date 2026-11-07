#!/usr/bin/env python3
"""Conservative identifier obfuscator for Python source trees.

Uses only the Python standard library. Python syntax is parsed with ``ast``;
token positions are edited in-place so comments, whitespace and string literals
are preserved. This is a source obfuscator, not a security boundary.
"""

from __future__ import annotations

import argparse
import ast
import builtins
import io
import keyword
import os
import random
import re
import string
import sys
import tokenize
from pathlib import Path


SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules"}
TEXT_EXTENSIONS = {
    ".cfg", ".conf", ".csv", ".html", ".ini", ".js", ".json", ".md",
    ".sh", ".toml", ".txt", ".xml", ".yaml", ".yml",
}
BUILTIN_NAMES = set(dir(builtins)) | set(keyword.kwlist) | set(keyword.softkwlist)


def python_files(root: Path) -> list[Path]:
    return sorted(
        p for p in root.rglob("*.py")
        if not any(part in SKIP_DIRS for part in p.relative_to(root).parts)
    )


def all_files(root: Path) -> list[Path]:
    return sorted(
        p for p in root.rglob("*") if p.is_file()
        and not any(part in SKIP_DIRS for part in p.relative_to(root).parts)
    )


class ModuleDefinitions(ast.NodeVisitor):
    """Collect names bound in a module, excluding class/function contents."""

    def __init__(self) -> None:
        self.names: set[str] = set()
        self.attribute_names: set[str] = set()
        self.protected: set[str] = set()
        self._module_level = True
        self._in_class_body = False

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if self._module_level:
            self.names.add(node.name)
        for dec in node.decorator_list:
            self.visit(dec)
        self.visit(node.args)
        was = self._module_level
        was_class = self._in_class_body
        self._module_level = False
        self._in_class_body = False
        for child in node.body:
            self.visit(child)
        self._module_level = was
        self._in_class_body = was_class

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        if self._module_level:
            self.names.add(node.name)
        was = self._module_level
        was_class = self._in_class_body
        self._module_level = False
        self._in_class_body = True
        for child in node.body:
            self.visit(child)
        self._module_level = was
        self._in_class_body = was_class

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.protected.add(alias.asname or alias.name.split(".")[0])

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name != "*":
                self.protected.add(alias.asname or alias.name)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            if self._in_class_body:
                self.attribute_names.add(node.id)
            else:
                self.names.add(node.id)

    def visit_arg(self, node: ast.arg) -> None:
        self.names.add(node.arg)

    def visit_keyword(self, node: ast.keyword) -> None:
        # Renaming a parameter used with keyword-call syntax would break callers.
        if node.arg is not None:
            self.protected.add(node.arg)
        self.generic_visit(node)

    def visit_Global(self, node: ast.Global) -> None:
        self.protected.update(node.names)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self.protected.update(node.names)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        # Attribute spellings are deliberately never modified.
        self.protected.add(node.attr)
        self.generic_visit(node)


def collect_candidates(files: list[Path]) -> set[str]:
    definitions: set[str] = set()
    protected: set[str] = set()
    for path in files:
        try:
            tree = ast.parse(path.read_bytes(), filename=str(path))
        except (SyntaxError, UnicodeDecodeError) as exc:
            raise ValueError(f"Could not parse {path}: {exc}") from exc
        visitor = ModuleDefinitions()
        visitor.visit(tree)
        definitions.update(visitor.names)
        protected.update(visitor.protected)
    return {
        name for name in definitions
        if name not in protected
        and name not in BUILTIN_NAMES
        and not (name.startswith("__") and name.endswith("__"))
        and name.isidentifier()
    }


def make_mapping(
    names: set[str], length: int, rng: random.Random,
    reserved: set[str] | None = None,
) -> dict[str, str]:
    alphabet = string.ascii_letters + string.digits
    start = string.ascii_letters
    used = set(BUILTIN_NAMES) | names | (reserved or set())
    mapping: dict[str, str] = {}
    for old in sorted(names):
        for _ in range(10_000):
            new = rng.choice(start) + "".join(rng.choice(alphabet) for _ in range(length - 1))
            if new not in used and new not in mapping.values():
                mapping[old] = new
                break
        else:
            raise RuntimeError("Could not generate a unique identifier.")
    return mapping


def ast_byte_column_to_char(line: str, byte_column: int) -> int:
    return len(line.encode("utf-8")[:byte_column].decode("utf-8", errors="ignore"))


def python_edits(
    source: str,
    mapping: dict[str, str],
    attribute_mapping: dict[str, str] | None = None,
) -> list[tuple[int, int, str]]:
    """Return character-offset edits for mapped identifiers in Python syntax."""
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    line_starts: list[int] = []
    cursor = 0
    for line in lines:
        line_starts.append(cursor)
        cursor += len(line)

    wanted: set[tuple[int, int, str]] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in mapping:
            char_col = ast_byte_column_to_char(lines[node.lineno - 1], node.col_offset)
            wanted.add((node.lineno - 1, char_col, node.id))
        elif isinstance(node, ast.arg) and node.arg in mapping:
            char_col = ast_byte_column_to_char(lines[node.lineno - 1], node.col_offset)
            wanted.add((node.lineno - 1, char_col, node.arg))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name in mapping:
            # Find the definition token after the def/class keyword.
            for tok in tokenize.generate_tokens(io.StringIO(source).readline):
                if tok.start[0] < node.lineno or tok.start[0] > node.lineno + 1:
                    continue
                if tok.type == tokenize.NAME and tok.string == node.name:
                    wanted.add((tok.start[0] - 1, tok.start[1], node.name))
                    break
        elif isinstance(node, ast.Attribute) and node.attr in (attribute_mapping or {}):
            line = lines[node.end_lineno - 1]
            end_byte = node.end_col_offset
            start_byte = end_byte - len(node.attr.encode("utf-8"))
            start_col = ast_byte_column_to_char(line, start_byte)
            wanted.add((node.end_lineno - 1, start_col, node.attr))

    # Class fields are represented by Name nodes, whereas uses are Attribute
    # nodes. Rename only bindings in a class body, not unrelated local names.
    if attribute_mapping:
        for class_node in (node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)):
            for statement in class_node.body:
                targets: list[ast.expr] = []
                if isinstance(statement, ast.AnnAssign):
                    targets.append(statement.target)
                elif isinstance(statement, (ast.Assign, ast.AugAssign)):
                    targets.extend(
                        statement.targets if isinstance(statement, ast.Assign)
                        else [statement.target]
                    )
                for target in targets:
                    for field in ast.walk(target):
                        if isinstance(field, ast.Name) and field.id in attribute_mapping:
                            char_col = ast_byte_column_to_char(
                                lines[field.lineno - 1], field.col_offset
                            )
                            wanted.add((field.lineno - 1, char_col, field.id))

    edits: list[tuple[int, int, str]] = []
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type != tokenize.NAME:
            continue
        key = (tok.start[0] - 1, tok.start[1], tok.string)
        if key not in wanted:
            continue
        start = line_starts[key[0]] + key[1]
        replacement = mapping.get(tok.string) or (attribute_mapping or {}).get(tok.string)
        if replacement is not None:
            edits.append((start, start + len(tok.string), replacement))
    return edits


def apply_edits(source: str, edits: list[tuple[int, int, str]]) -> str:
    for start, end, replacement in sorted(edits, reverse=True):
        source = source[:start] + replacement + source[end:]
    return source


def rewrite_python(
    path: Path,
    mapping: dict[str, str],
    write: bool,
    attribute_mapping: dict[str, str] | None = None,
) -> bool:
    source = path.read_text(encoding="utf-8")
    rewritten = apply_edits(
        source, python_edits(source, mapping, attribute_mapping)
    )
    if rewritten == source:
        return False
    ast.parse(rewritten, filename=str(path))
    if write:
        path.write_text(rewritten, encoding="utf-8", newline="")
    return True


def class_and_function_names(files: list[Path]) -> set[str]:
    names: set[str] = set()
    for path in files:
        tree = ast.parse(path.read_bytes(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
    return names


def collect_class_attribute_names(files: list[Path]) -> set[str]:
    """Collect attributes assigned directly in class bodies across a project."""
    names: set[str] = set()
    for path in files:
        tree = ast.parse(path.read_bytes(), filename=str(path))
        visitor = ModuleDefinitions()
        visitor.visit(tree)
        names.update(visitor.attribute_names)
    return {
        name for name in names
        if name.isidentifier()
        and name not in BUILTIN_NAMES
        and not (name.startswith("__") and name.endswith("__"))
    }


def rewrite_text_files(root: Path, mapping: dict[str, str], non_python_names: set[str],
                       python_paths: set[Path], write: bool) -> list[Path]:
    """Replace standalone function/class names in common text formats."""
    changed: list[Path] = []
    # Names of functions/classes only, as requested for references outside Python.
    replacements = {name: value for name, value in mapping.items() if name in non_python_names}
    if not replacements:
        return changed
    pattern = re.compile(r"\b(" + "|".join(map(re.escape, sorted(replacements, key=len, reverse=True))) + r")\b")
    for path in all_files(root):
        if path in python_paths or path.suffix.lower() not in TEXT_EXTENSIONS:
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        rewritten = pattern.sub(lambda m: replacements[m.group(0)], source)
        if rewritten != source:
            changed.append(path)
            if write:
                path.write_text(rewritten, encoding="utf-8", newline="")
    return changed


def run_file(path: Path, length: int, rng: random.Random, write: bool) -> dict[str, str]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    visitor = ModuleDefinitions()
    visitor.visit(tree)
    candidates = {
        name for name in visitor.names
        if name not in visitor.protected and name not in BUILTIN_NAMES
        and not (name.startswith("__") and name.endswith("__"))
    }
    mapping = make_mapping(candidates, length, rng)
    attributes = {
        name for name in visitor.attribute_names
        if name not in BUILTIN_NAMES
        and not (name.startswith("__") and name.endswith("__"))
    }
    attribute_mapping = make_mapping(
        attributes, length, rng, reserved=set(mapping.values())
    )
    rewrite_python(path, mapping, write, attribute_mapping)
    return mapping | attribute_mapping


def run_directory(root: Path, length: int, rng: random.Random, write: bool) -> dict[str, str]:
    files = python_files(root)
    non_python_names = class_and_function_names(files)
    mapping = make_mapping(collect_candidates(files), length, rng)
    attribute_mapping = make_mapping(
        collect_class_attribute_names(files), length, rng,
        reserved=set(mapping.values()),
    )
    for path in files:
        rewrite_python(path, mapping, write, attribute_mapping)
    if write:
        changed_text = rewrite_text_files(root, mapping, non_python_names, set(files), write=True)
    else:
        changed_text = rewrite_text_files(root, mapping, non_python_names, set(files), write=False)
    print(f"Non-Python text files with changed tokens: {len(changed_text)}")
    return mapping | attribute_mapping


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Obfuscate identifiers in Python source code.")
    parser.add_argument("mode", choices=("file", "dir"), help="operation mode")
    parser.add_argument("path", type=Path, help=".py file or directory")
    parser.add_argument("--length", type=int, choices=range(4, 7), default=5,
                        help="generated identifier length: 4–6 (default: 5)")
    parser.add_argument("--seed", type=int, help="seed for reproducible mappings")
    parser.add_argument("--dry-run", action="store_true", help="show the mapping without writing changes")
    args = parser.parse_args(argv)
    target = args.path.resolve()
    if args.mode == "file" and (not target.is_file() or target.suffix.lower() != ".py"):
        parser.error("In file mode, specify an existing .py file.")
    if args.mode == "dir" and not target.is_dir():
        parser.error("In directory mode, specify an existing directory.")
    rng = random.Random(args.seed)
    try:
        mapping = (run_file(target, args.length, rng, not args.dry_run)
                   if args.mode == "file" else
                   run_directory(target, args.length, rng, not args.dry_run))
    except (OSError, SyntaxError, ValueError, tokenize.TokenError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    print(f"Mode: {args.mode}; mappings: {len(mapping)}" + (" (no changes written)" if args.dry_run else ""))
    for old, new in sorted(mapping.items()):
        print(f"{old} -> {new}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
