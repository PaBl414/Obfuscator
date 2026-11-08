"""Apply a conservative, bytecode-preserving rewrite to import statements."""

from __future__ import annotations

import ast
import dis
import io
import random
import tokenize
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


def rewrite_project_imports(source: str, mapping: dict[str, str],
                            project_modules: set[str]) -> str:
    """Align retained from-imports with renamed project definitions/bindings."""
    tree = ast.parse(source)
    edits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.module == "__future__":
            continue
        if not node.level and (node.module or "") not in project_modules:
            continue
        for alias in node.names:
            if alias.name == "*":
                continue
            binding = alias.asname or alias.name
            alias.name = mapping.get(alias.name, alias.name)
            alias.asname = mapping.get(binding, binding)
        edits.append((_source_offset(source, node.lineno, node.col_offset),
                      _source_offset(source, node.end_lineno, node.end_col_offset),
                      ast.unparse(node)))
    for start, end, replacement in sorted(edits, reverse=True):
        source = source[:start] + replacement + source[end:]
    return source


def _unsafe_runtime_import_reason(
    tree: ast.Module, allow_introspection: bool = False
) -> str | None:
    sensitive_names = {"__builtins__", "__import__", "globals", "locals"}
    blocked_names = sensitive_names - {"globals", "locals"} if allow_introspection else sensitive_names
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in blocked_names:
            return f"source uses {node.id}"
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            if sensitive_names.intersection(node.names):
                return "source changes the scope of import-related builtins"
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            module_names = (
                [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
            )
            if any(name == "builtins" for name in module_names):
                return "source imports builtins"
        if isinstance(node, ast.Attribute) and node.attr in {
            "__import__", "meta_path", "path_hooks"
        }:
            return f"source accesses import hook attribute {node.attr}"
    return None


def _runtime_import_helper_name(source: str, rng: random.Random) -> str:
    used = {
        token.string
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type == tokenize.NAME
    }
    while True:
        candidate = f"_load_{rng.getrandbits(64):016x}"
        if candidate not in used:
            return candidate


def _module_header_insert_line(tree: ast.Module, source: str) -> int:
    insert = 1 if source.startswith("#!") else 0
    if tree.body and isinstance(tree.body[0], ast.Expr):
        value = tree.body[0].value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            insert = max(insert, value.end_lineno or 0)
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            insert = max(insert, node.end_lineno or 0)
    return insert


def obfuscate_imports_runtime(
    source: str,
    seed: int | None = None,
    name_mapping: dict[str, str] | None = None,
    project_modules: set[str] | None = None,
    allow_introspection: bool = False,
) -> tuple[str, int, int]:
    """Hide supported import paths behind runtime decoding and ``__import__``.

    Star and future imports are retained unchanged. Multi-name ``from`` imports
    are expanded into one independently encoded binding per imported symbol.
    Sources that explicitly manipulate import builtins or scope
    introspection are left unchanged to reduce semantic risk.
    """
    tree = ast.parse(source, type_comments=True)
    unsafe_reason = _unsafe_runtime_import_reason(tree, allow_introspection=allow_introspection)
    if unsafe_reason:
        import_count = sum(
            isinstance(node, (ast.Import, ast.ImportFrom))
            for node in ast.walk(tree)
        )
        return source, 0, import_count

    rng = random.Random(seed)
    helper_name = _runtime_import_helper_name(source, rng)
    edits: list[tuple[int, int, str]] = []
    changed = 0
    skipped = 0

    def encode(value: str) -> tuple[tuple[tuple[int, ...], int], ...]:
        """XOR-encode a value in independently masked, variable-sized chunks."""
        chunks: list[tuple[tuple[int, ...], int]] = []
        offset = 0
        while offset < len(value):
            chunk_size = rng.randint(2, 5)
            chunk = value[offset : offset + chunk_size]
            mask = rng.randrange(1, 256)
            chunks.append((tuple(ord(char) ^ mask for char in chunk), mask))
            offset += chunk_size
        return tuple(chunks)

    def call_expression(
        module_name: str,
        fromlist: tuple[str, ...],
        symbol_name: str,
        level: int,
        extract_symbol: bool,
    ) -> str:
        encoded_module = encode(module_name)
        encoded_fromlist = tuple(encode(name) for name in fromlist)
        encoded_symbol = encode(symbol_name)
        return (
            f"{helper_name}({encoded_module!r}, "
            f"{encoded_fromlist!r}, {encoded_symbol!r}, {level}, "
            f"{extract_symbol!r}, globals(), locals())"
        )

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if getattr(node, "type_comment", None):
                skipped += 1
                continue
            statements: list[str] = []
            for alias in node.names:
                if alias.asname is None and "." in alias.name:
                    binding = alias.name.split(".", 1)[0]
                    fromlist: tuple[str, ...] = ()
                elif alias.asname is not None and "." in alias.name:
                    binding = alias.asname
                    fromlist = (alias.name.rsplit(".", 1)[1],)
                else:
                    binding = alias.asname or alias.name
                    fromlist = ()
                statements.append(
                    f"{binding} = {call_expression(alias.name, fromlist, '', 0, False)}"
                )
            start = _source_offset(source, node.lineno, node.col_offset)
            end = _source_offset(source, node.end_lineno, node.end_col_offset)
            edits.append((start, end, "; ".join(statements)))
            changed += len(node.names)

        elif isinstance(node, ast.ImportFrom):
            if (
                node.module == "__future__"
                or node.names[0].name == "*"
                or getattr(node, "type_comment", None)
            ):
                skipped += 1
                continue
            statements = []
            for alias in node.names:
                if alias.name == "*":
                    skipped += 1
                    continue
                binding = alias.asname or alias.name
                imported_name = alias.name
                # Only relative imports are known to belong to the processed
                # project. External package APIs must keep their symbol names.
                internal_module = (
                    node.level > 0
                    or (node.module or "") in (project_modules or set())
                )
                if name_mapping and internal_module:
                    imported_name = name_mapping.get(imported_name, imported_name)
                    binding = name_mapping.get(binding, binding)
                call = call_expression(
                    node.module or "",
                    (imported_name,),
                    imported_name,
                    node.level,
                    True,
                )
                statements.append(f"{binding} = {call}")
                changed += 1
            if not statements:
                continue
            start = _source_offset(source, node.lineno, node.col_offset)
            end = _source_offset(source, node.end_lineno, node.end_col_offset)
            edits.append((start, end, "; ".join(statements)))

    if not edits:
        return source, 0, skipped

    reader_name = f"_{rng.getrandbits(64):016x}"
    character_mask = rng.randrange(1, 256)
    character_name = bytes(value ^ character_mask for value in b"chr")

    rewritten = source
    for start, end, replacement in sorted(edits, reverse=True):
        rewritten = rewritten[:start] + replacement + rewritten[end:]

    helper_lines = [
        f"def {helper_name}(n, f, v, level, extract, g, l, "
        "_b=__builtins__, _t=tuple, _y=type, _s=str, _z=bytes):",
        "    builtins = _b if _y(_b) is _y({}) else _b.__dict__",
        f"    _c = builtins[_s(_z(value ^ {character_mask} "
        f"for value in {character_name!r}), 'ascii')]",
        "    importer_name = ''.join(_c(value) for value in "
        "(95, 95, 105, 109, 112, 111, 114, 116, 95, 95))",
        "    attribute_name = ''.join(_c(value) for value in "
        "(103, 101, 116, 97, 116, 116, 114))",
        "    importer = builtins[importer_name]",
        "    attribute = builtins[attribute_name]",
        f"    {reader_name} = lambda parts: ''.join("
        "''.join(_c(value ^ key) for value in chunk) for chunk, key in parts)",
        f"    name = {reader_name}(n)",
        f"    fromlist = _t({reader_name}(item) for item in f)",
        "    module = importer(name, g, l, fromlist, level)",
        "    if extract:",
        f"        return attribute(module, {reader_name}(v))",
        "    return module",
    ]
    insertion_line = _module_header_insert_line(tree, source)
    lines = rewritten.split("\n")
    rewritten = "\n".join(
        lines[:insertion_line] + helper_lines + lines[insertion_line:]
    )
    compile(rewritten, "<runtime-import-obfuscation>", "exec")
    return rewritten, changed, skipped
