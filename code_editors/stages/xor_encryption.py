#!/usr/bin/env python3
"""Encrypt string and bytes literals with distinct per-literal XOR keys."""
import ast
import random
import secrets
import sys


def generate_key(length=16):
    return secrets.token_bytes(length)


def xor_bytes(data: bytes, key: bytes) -> bytes:
    return bytes(c ^ key[i % len(key)] for i, c in enumerate(data))


def collect_skip_ids(tree):
    skip = set()

    # Replacing docstrings would change __doc__.
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef,
                             ast.AsyncFunctionDef, ast.ClassDef)):
            if node.body and isinstance(node.body[0], ast.Expr):
                v = node.body[0].value
                if isinstance(v, ast.Constant) and isinstance(v.value, str):
                    skip.add(id(v))

    # Calling a function in a string annotation changes __annotations__ when
    # ``from __future__ import annotations`` is enabled.
    def mark_annotations(ann):
        for n in ast.walk(ann):
            if isinstance(n, ast.Constant) and isinstance(n.value, str):
                skip.add(id(n))

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            a = node.args
            all_args = list(a.posonlyargs) + list(a.args) + list(a.kwonlyargs)
            if a.vararg: all_args.append(a.vararg)
            if a.kwarg:  all_args.append(a.kwarg)
            for arg in all_args:
                if arg.annotation: mark_annotations(arg.annotation)
            if node.returns: mark_annotations(node.returns)
        if isinstance(node, ast.AnnAssign) and node.annotation:
            mark_annotations(node.annotation)

    # Match-case patterns cannot be represented as function calls.
    MatchValue = getattr(ast, 'MatchValue', None)
    MatchMapping = getattr(ast, 'MatchMapping', None)
    for node in ast.walk(tree):
        if MatchValue and isinstance(node, MatchValue):
            if isinstance(node.value, ast.Constant):
                skip.add(id(node.value))
        if MatchMapping and isinstance(node, MatchMapping):
            for k in node.keys:
                if isinstance(k, ast.Constant):
                    skip.add(id(k))
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            for sub in ast.walk(node):
                if isinstance(sub, ast.Constant):
                    skip.add(id(sub))
    return skip


def find_insert_line(tree, source):
    """Miejsce wstawienia decryptora: po shebang, docstringu i __future__."""
    insert = 0
    if source.startswith('#!'):
        insert = 1
    if tree.body and isinstance(tree.body[0], ast.Expr):
        v = tree.body[0].value
        if isinstance(v, ast.Constant) and isinstance(v.value, str):
            insert = max(insert, v.end_lineno)
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == '__future__':
            insert = max(insert, node.end_lineno)
    return insert


def transform_source(
    source: str,
    key: bytes,
    decoder_names: tuple[str, str] = ("_d", "_db"),
):
    if not key:
        raise ValueError("The master key must not be empty.")
    text_decoder, bytes_decoder = decoder_names
    if (
        len(set(decoder_names)) != 2
        or any(not name.isidentifier() for name in decoder_names)
    ):
        raise ValueError("Decoder names must be valid Python identifiers.")
    tree = ast.parse(source)
    skip = collect_skip_ids(tree)
    key_rng = random.Random(int.from_bytes(key, "big"))
    used_keys: set[bytes] = set()

    def new_literal_key() -> bytes:
        literal_key = bytes(key_rng.randrange(256) for _ in range(16))
        while literal_key in used_keys:
            literal_key = bytes(key_rng.randrange(256) for _ in range(16))
        used_keys.add(literal_key)
        return literal_key

    source_bytes = source.encode('utf-8')
    lines = source.split('\n')
    starts, off = [], 0
    for ln in lines:
        starts.append(off)
        off += len(ln.encode('utf-8')) + 1

    def pos(lineno, col):
        return starts[lineno - 1] + col

    replacements = []

    def visit(node):
        if isinstance(node, ast.Constant) and id(node) not in skip:
            if isinstance(node.value, str):
                literal_key = new_literal_key()
                enc = xor_bytes(node.value.encode('utf-8'), literal_key).hex()
                repl = (
                    f"{text_decoder}('{enc}', '{literal_key.hex()}')"
                    .encode('utf-8')
                )
            elif isinstance(node.value, bytes):
                literal_key = new_literal_key()
                enc = xor_bytes(node.value, literal_key).hex()
                repl = (
                    f"{bytes_decoder}('{enc}', '{literal_key.hex()}')"
                    .encode('utf-8')
                )
            else:
                for c in ast.iter_child_nodes(node): visit(c)
                return
            s = pos(node.lineno, node.col_offset)
            e = pos(node.end_lineno, node.end_col_offset)
            replacements.append((s, e, repl))
        for c in ast.iter_child_nodes(node):
            visit(c)

    visit(tree)
    replacements.sort(key=lambda x: x[0], reverse=True)

    out = bytearray(source_bytes)
    for s, e, r in replacements:
        out[s:e] = r
    return out.decode('utf-8'), tree


def build_decryptor(
    key: bytes | None = None,
    decoder_names: tuple[str, str] = ("_d", "_db"),
):
    """Build runtime decoders; per-literal keys are stored beside each payload."""
    text_decoder, bytes_decoder = decoder_names
    if (
        len(set(decoder_names)) != 2
        or any(not name.isidentifier() for name in decoder_names)
    ):
        raise ValueError("Decoder names must be valid Python identifiers.")
    return [
        f"def {text_decoder}(s, k):",
        "    _k = bytes.fromhex(k)",
        "    _b = bytes.fromhex(s)",
        "    return bytes(c ^ _k[i % len(_k)] for i, c in enumerate(_b)).decode('utf-8')",
        f"def {bytes_decoder}(s, k):",
        "    _k = bytes.fromhex(k)",
        "    _b = bytes.fromhex(s)",
        "    return bytes(c ^ _k[i % len(_k)] for i, c in enumerate(_b))",
    ]


def encrypt_file(path, out_path=None, key=None):
    if key is None:
        key = generate_key(16)

    with open(path, 'r', encoding='utf-8') as f:
        source = f.read()

    transformed, tree = transform_source(source, key)
    insert = find_insert_line(tree, source)

    t_lines = transformed.split('\n')
    dec_lines = build_decryptor(key)
    final = '\n'.join(t_lines[:insert] + dec_lines + t_lines[insert:])

    try:
        compile(final, out_path or path, 'exec')
    except SyntaxError as e:
        print(f"Error: transformed source does not compile ({e}); output was not saved.")
        return

    if out_path is None:
        out_path = path
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(final)

    print(f"Saved: {out_path}")
    print("Encrypted literals use distinct embedded keys.")


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python xor_encryption.py <input.py> [output.py] [key_hex]")
        sys.exit(1)
    out = sys.argv[2] if len(sys.argv) > 2 else None
    k = bytes.fromhex(sys.argv[3]) if len(sys.argv) > 3 else None
    encrypt_file(sys.argv[1], out, k)
