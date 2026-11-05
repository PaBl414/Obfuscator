#!/usr/bin/env python3
"""Inject unreachable, randomly named junk blocks into a Python module.

The generated blocks are guarded by the literal ``if False`` and are inserted
only between module-level statements. They never execute and do not bind names
in the running module. The input file is never overwritten by default.
"""

from __future__ import annotations

import argparse
import ast
import random
import re
import string
import sys
import tokenize
from pathlib import Path


IDENTIFIER_RE = re.compile(r"\b[A-Za-z_]\w*\b")


def _module_header_end(body: list[ast.stmt]) -> int:
    """Return index after module docstring and consecutive future imports."""
    index = 0
    if (body and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        index = 1
    while index < len(body):
        node = body[index]
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            index += 1
        else:
            break
    return index


def _fresh_identifier(rng: random.Random, used: set[str]) -> str:
    while True:
        name = f"_j{rng.getrandbits(40):010x}"
        if name not in used:
            used.add(name)
            return name


def _false_guard(rng: random.Random) -> str:
    """A side-effect-free, always-false guard not written as a False literal."""
    return rng.choice((
        "(1).__bool__() is False",
        "(0).__bool__() is True",
        "().__len__() != 0",
        "'x'.__len__() < 0",
        "b''.__len__() > 0",
    ))


def _junk_block(rng: random.Random, used: set[str], newline: str) -> str:
    count = rng.randint(2, 4)
    lines = [f"if {_false_guard(rng)}:{newline}"]
    for _ in range(count):
        name = _fresh_identifier(rng, used)
        a, b, c = (rng.randint(2, 999) for _ in range(3))
        kind = rng.choice(("integer", "string", "bytes", "tuple", "list",
                           "dict", "float", "boolean", "complex", "set"))
        if kind == "integer":
            expression = f"(({a} * {b}) + {c}) ^ {rng.randint(2, 999)}"
        elif kind == "string":
            value = "".join(rng.choice(string.ascii_letters) for _ in range(rng.randint(5, 18)))
            expression = repr(value)
        elif kind == "bytes":
            value = bytes(rng.randrange(256) for _ in range(rng.randint(4, 12)))
            expression = repr(value)
        elif kind == "tuple":
            expression = repr((a, b, c))
        elif kind == "list":
            expression = repr([a, b, c])
        elif kind == "dict":
            expression = repr({f"k{a}": b, f"k{c}": rng.randint(0, 999)})
        elif kind == "float":
            expression = f"{a}.{b}e-{rng.randint(1, 8)}"
        elif kind == "boolean":
            expression = rng.choice(("True", "False"))
        elif kind == "complex":
            expression = f"complex({a}, {b})"
        else:
            expression = "{" + f"{a}, {b}, {c}" + "}"
        lines.append(f"    {name} = {expression}{newline}")
    lines.append(f"    pass{newline}")
    return "".join(lines)


def inject_junk(source: str, count: int = 8, seed: int | None = None) -> tuple[str, int]:
    if count < 1:
        raise ValueError("The block count must be at least 1.")
    tree = ast.parse(source)
    body = list(tree.body)
    header_end = _module_header_end(body)
    if header_end >= len(body):
        raise ValueError("No module statements are available between which blocks can be inserted.")

    # Insert at safe module-suite boundaries, never inside a function, class,
    # conditional, loop, try block, or with block.
    offsets: list[int] = []
    lines = source.splitlines(keepends=True)
    line_starts: list[int] = []
    position = 0
    for line in lines:
        line_starts.append(position)
        position += len(line)
    for node in body[header_end:]:
        end_line = node.end_lineno
        if end_line is None:
            continue
        after = line_starts[end_line] if end_line < len(line_starts) else len(source)
        offsets.append(after)
    if not offsets:
        raise ValueError("Nie znaleziono bezpiecznego miejsca wstawienia")

    newline = "\r\n" if "\r\n" in source else "\n"
    rng = random.Random(seed)
    used = set(IDENTIFIER_RE.findall(source))
    insertions: dict[int, list[str]] = {}
    selected = rng.sample(offsets, min(count, len(offsets)))
    while len(selected) < count:
        selected.append(rng.choice(offsets))
    for where in selected:
        insertions.setdefault(where, []).append(_junk_block(rng, used, newline))

    # Descending offsets ensure earlier positions remain valid after edits.
    for offset in sorted(insertions, reverse=True):
        prefix = "" if offset == 0 or source[:offset].endswith(("\n", "\r")) else newline
        addition = prefix + "".join(insertions[offset])
        source = source[:offset] + addition + source[offset:]

    ast.parse(source)
    compile(source, "<junk-injected>", "exec")
    return source, count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Insert unreachable blocks with random names into a Python file."
    )
    parser.add_argument("input", type=Path, help="input .py file")
    parser.add_argument("output", nargs="?", type=Path,
                        help="output file (default: <name>_junk.py)")
    parser.add_argument("--count", type=int, default=8,
                        help="number of blocks to insert (default: 8)")
    parser.add_argument("--seed", type=int, help="reproduce the layout with the same seed")
    parser.add_argument("--force", action="store_true", help="overwrite an existing output file")
    args = parser.parse_args(argv)

    source_path = args.input.expanduser().resolve()
    if not source_path.is_file() or source_path.suffix.lower() != ".py":
        parser.error("Specify an existing .py file.")
    if args.count < 1:
        parser.error("--count must be at least 1.")
    output_path = (args.output.expanduser().resolve() if args.output else
                   source_path.with_name(source_path.stem + "_junk.py"))
    if output_path == source_path:
        parser.error("The output file must be different from the input file.")
    if output_path.exists() and not args.force:
        parser.error(f"Output file already exists: {output_path} (use --force to overwrite it).")

    try:
        with tokenize.open(str(source_path)) as handle:
            source = handle.read()
        transformed, inserted = inject_junk(source, args.count, args.seed)
        output_path.write_text(transformed, encoding="utf-8", newline="")
    except (OSError, SyntaxError, UnicodeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Saved: {output_path}")
    print(f"Inserted blocks: {inserted}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
