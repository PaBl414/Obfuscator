"""Apply a conservative, bytecode-preserving rewrite to import statements."""

from __future__ import annotations

import ast
import dis
from types import CodeType


def _source_offset(source: str, line_number: int, byte_column: int) -> int:
    lines = source.splitlines(keepends=True)
    line_start = sum(len(line) for line in lines[: line_number - 1])
    line = lines[line_number - 1]
    character_column = len(
        line.encode("utf-8")[:byte_column].decode("utf-8", errors="ignore")
    )
    return line_start + character_column


def _bytecode_signature(code: CodeType) -> tuple[tuple[str, object], ...]:
    signature = []
    for instruction in dis.get_instructions(code):
        argument = instruction.argval
        if isinstance(argument, CodeType):
            argument = _bytecode_signature(argument)
        signature.append((instruction.opname, repr(argument)))
    return tuple(signature)


def obfuscate_imports(source: str) -> tuple[str, int]:
    """Add redundant explicit aliases without changing import bytecode.

    Existing aliases, dotted ``import`` statements, star imports, and future
    imports are left untouched. The resulting code is compiled and its
    instruction stream is compared with the original before it is returned.
    """
    tree = ast.parse(source)
    insertions: list[tuple[int, str]] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            aliases = node.names
            for alias in aliases:
                if alias.asname is None and "." not in alias.name:
                    offset = _source_offset(
                        source, alias.end_lineno, alias.end_col_offset
                    )
                    insertions.append((offset, f" as {alias.name}"))
        elif isinstance(node, ast.ImportFrom):
            if node.module == "__future__":
                continue
            for alias in node.names:
                if alias.asname is None and alias.name != "*":
                    offset = _source_offset(
                        source, alias.end_lineno, alias.end_col_offset
                    )
                    insertions.append((offset, f" as {alias.name}"))

    rewritten = source
    for offset, text in sorted(insertions, reverse=True):
        rewritten = rewritten[:offset] + text + rewritten[offset:]

    original_code = compile(source, "<import-obfuscation>", "exec")
    rewritten_code = compile(rewritten, "<import-obfuscation>", "exec")
    if _bytecode_signature(original_code) != _bytecode_signature(rewritten_code):
        raise ValueError("Import rewriting changed the compiled instruction stream.")

    return rewritten, len(insertions)
