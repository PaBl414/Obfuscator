#!/usr/bin/env python3
"""Encrypt string and bytes literals with distinct per-literal XOR keys."""
import ast
import keyword
import random
import secrets
import sys


def generate_key(length=16):
    return secrets.token_bytes(length)


def xor_bytes(data: bytes, key: bytes) -> bytes:
    return bytes(c ^ key[i % len(key)] for i, c in enumerate(data))


def layered_bytes(data: bytes, key: bytes, master: bytes) -> bytes:
    """Encode bytes with a module key, rotation, offset and ciphertext feedback."""
    previous = master[0]
    result = bytearray()
    for index, value in enumerate(data):
        mask = key[index % len(key)] ^ master[index % len(master)] ^ previous
        rotation = 1 + key[(index + 1) % len(key)] % 7
        value ^= mask
        value = ((value << rotation) | (value >> (8 - rotation))) & 255
        value = (value + key[(index + 2) % len(key)]) & 255
        result.append(value)
        previous = value
    return bytes(result)


def obfuscate_chained_literals(
    source: str,
    master: bytes,
    decoder_names: tuple[str, str] = ("_d", "_db"),
    *, polymorphic: bool = False,
) -> tuple[str, int]:
    """Encode literals using immutable, order-independent key dependencies.

    Record n's key is wrapped with record n-1's key and ciphertext. Runtime
    initialization reconstructs immutable keys once, so threads, recursion,
    skipped branches and repeated calls see the same result.
    """
    if not master:
        raise ValueError("The master key must not be empty")
    text_decoder, bytes_decoder = decoder_names
    # Validate names through the same code path as the layered decoder.
    layered = build_decryptor(master, decoder_names, mode="layered")
    tree = ast.parse(source)
    skip = collect_skip_ids(tree)
    literals = sorted(
        (node for node in ast.walk(tree)
         if isinstance(node, ast.Constant)
         and isinstance(node.value, (str, bytes)) and id(node) not in skip),
        key=lambda node: (node.lineno, node.col_offset),
    )
    if not literals:
        return source, 0
    rng = random.Random(int.from_bytes(master, "big"))
    reader_name = f"_{rng.getrandbits(64):016x}"
    previous_key, previous_payload = master, b"\xa5" + master
    records: list[tuple] = []
    used_keys: set[bytes] = set()
    edits: list[tuple[int, int, bytes]] = []
    source_bytes = source.encode("utf-8")
    starts = [0]
    for line in source_bytes.splitlines(keepends=True):
        starts.append(starts[-1] + len(line))
    for index, node in enumerate(literals):
        key = bytes(rng.randrange(256) for _ in range(16))
        while key in used_keys:
            key = bytes(rng.randrange(256) for _ in range(16))
        used_keys.add(key)
        data = (node.value.encode("utf-8", "surrogatepass")
                if isinstance(node.value, str) else node.value)
        payload = layered_bytes(data, key, master)
        variant = rng.randrange(3) if polymorphic else 0
        if polymorphic:
            if variant == 0:
                payload = payload[::-1]
            elif variant == 1:
                payload = bytes((value + j + key[0]) & 255 for j, value in enumerate(payload))
            else:
                payload = bytes(value ^ key[(j + 5) % len(key)] for j, value in enumerate(payload))
        wrapped = bytes(
            value ^ previous_key[j % len(previous_key)]
            ^ previous_payload[j % len(previous_payload)]
            for j, value in enumerate(key)
        )
        records.append((payload.hex(), wrapped.hex(), variant) if polymorphic
                       else (payload.hex(), wrapped.hex()))
        previous_key, previous_payload = key, payload or master
        decoder = text_decoder if isinstance(node.value, str) else bytes_decoder
        replacement = f" {decoder}({index}) ".encode("utf-8")
        start = starts[node.lineno - 1] + node.col_offset
        end = starts[node.end_lineno - 1] + node.end_col_offset
        edits.append((start, end, replacement))
    result = source_bytes
    for start, end, replacement in sorted(edits, reverse=True):
        result = result[:start] + replacement + result[end:]

    decoder_lines = [
        f"def {bytes_decoder}(_records={tuple(records)!r}, _master={master!r}, "
        "_bytes=bytes, _len=len, _enumerate=enumerate, _bytearray=bytearray, _tuple=tuple):",
        "    previous_key, previous_payload = _master, b'\\xa5' + _master",
        "    keys = []",
        ("    for ciphertext, wrapped, variant in _records:" if polymorphic
         else "    for ciphertext, wrapped in _records:"),
        "        payload = _bytes.fromhex(ciphertext)",
        "        wrapped = _bytes.fromhex(wrapped)",
        "        key = _bytes(value ^ previous_key[j % _len(previous_key)] ^ "
        "previous_payload[j % _len(previous_payload)] for j, value in _enumerate(wrapped))",
        "        previous_key, previous_payload = key, payload or _master",
        "        keys.append(key)",
        "    cache = {}",
        f"    def {reader_name}(_index, _records=_records, _keys=_tuple(keys), _master=_master, _cache=cache):",
        "        if _index in _cache:",
        "            return _cache[_index]",
        "        payload = _bytes.fromhex(_records[_index][0])",
        "        key = _keys[_index]",
        *([
            "        variant = _records[_index][2]",
            "        if variant == 0:",
            "            payload = payload[::-1]",
            "        elif variant == 1:",
            "            payload = _bytes((value - j - key[0]) & 255 for j, value in _enumerate(payload))",
            "        else:",
            "            payload = _bytes(value ^ key[(j + 5) % _len(key)] for j, value in _enumerate(payload))",
        ] if polymorphic else []),
        *("    " + line for line in layered[3:-3]),
        "        value = _bytes(result)",
        "        _cache[_index] = value",
        "        return value",
        f"    return {reader_name}",
        f"{bytes_decoder} = {bytes_decoder}()",
        f"def {text_decoder}(_index, _str=str):",
        f"    return _str({bytes_decoder}(_index), 'utf-8', 'surrogatepass')",
    ]
    lines = result.decode("utf-8").split("\n")
    insert = find_insert_line(tree, source)
    result_source = "\n".join(lines[:insert] + decoder_lines + lines[insert:])
    compile(result_source, "<chained-literals>", "exec")
    return result_source, len(records)


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
    mode: str = "xor",
):
    if not key:
        raise ValueError("The master key must not be empty.")
    if mode not in {"xor", "layered"}:
        raise ValueError("Unknown literal encoding mode")
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
                data = node.value.encode('utf-8', 'surrogatepass')
                enc = (layered_bytes(data, literal_key, key) if mode == "layered"
                       else xor_bytes(data, literal_key)).hex()
                repl = (
                    f"{text_decoder}('{enc}', '{literal_key.hex()}')"
                    .encode('utf-8')
                )
            elif isinstance(node.value, bytes):
                literal_key = new_literal_key()
                enc = (layered_bytes(node.value, literal_key, key) if mode == "layered"
                       else xor_bytes(node.value, literal_key)).hex()
                repl = (
                    f"{bytes_decoder}('{enc}', '{literal_key.hex()}')"
                    .encode('utf-8')
                )
            else:
                for c in ast.iter_child_nodes(node): visit(c)
                return
            s = pos(node.lineno, node.col_offset)
            e = pos(node.end_lineno, node.end_col_offset)
            # Minified source may spell ``elif'word':`` without whitespace.
            # A replacement call needs a token boundary after the keyword.
            if s and (chr(source_bytes[s - 1]).isalnum() or source_bytes[s - 1] == 95):
                repl = b" " + repl
            if e < len(source_bytes) and (
                chr(source_bytes[e]).isalnum() or source_bytes[e] == 95
            ):
                repl += b" "
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
    mode: str = "xor",
):
    """Build runtime decoders; per-literal keys are stored beside each payload."""
    text_decoder, bytes_decoder = decoder_names
    if mode not in {"xor", "layered"}:
        raise ValueError("Unknown literal encoding mode")
    if (
        len(set(decoder_names)) != 2
        or any(not name.isidentifier() or keyword.iskeyword(name)
               for name in decoder_names)
    ):
        raise ValueError("Decoder names must be valid Python identifiers.")
    if mode == "layered":
        if not key:
            raise ValueError("Layered decoders require a module key")
        # Choose equivalent arithmetic forms deterministically from the seed.
        subtract = "(value - offset) & 255" if key[0] & 1 else "(value + 256 - offset) % 256"
        rotate = (
            "((value >> rotation) | (value << (8 - rotation))) & 255"
            if key[-1] & 1
            else "value // (1 << rotation) + (value % (1 << rotation)) * (1 << (8 - rotation))"
        )
        return [
            f"def {bytes_decoder}(s, k, _master={key!r}, _bytes=bytes, "
            "_len=len, _enumerate=enumerate, _bytearray=bytearray):",
            "    payload = _bytes.fromhex(s)",
            "    key = _bytes.fromhex(k)",
            "    previous = _master[0]",
            "    result = _bytearray()",
            "    for index, cipher in _enumerate(payload):",
            "        mask = key[index % _len(key)] ^ _master[index % _len(_master)] ^ previous",
            "        rotation = 1 + key[(index + 1) % _len(key)] % 7",
            "        offset = key[(index + 2) % _len(key)]",
            "        value = cipher",
            f"        value = {subtract}",
            f"        value = {rotate}",
            "        result.append(value ^ mask)",
            "        previous = cipher",
            "    return _bytes(result)",
            f"def {text_decoder}(s, k, _str=str):",
            f"    return _str({bytes_decoder}(s, k), 'utf-8', 'surrogatepass')",
        ]
    if (
        len(set(decoder_names)) != 2
        or any(not name.isidentifier() for name in decoder_names)
    ):
        raise ValueError("Decoder names must be valid Python identifiers.")
    return [
        f"def {text_decoder}(s, k, _bytes=bytes, _len=len, _enumerate=enumerate, _str=str):",
        "    _k = _bytes.fromhex(k)",
        "    _b = _bytes.fromhex(s)",
        "    return _str(_bytes(c ^ _k[i % _len(_k)] for i, c in _enumerate(_b)), 'utf-8', 'surrogatepass')",
        f"def {bytes_decoder}(s, k, _bytes=bytes, _len=len, _enumerate=enumerate):",
        "    _k = _bytes.fromhex(k)",
        "    _b = _bytes.fromhex(s)",
        "    return _bytes(c ^ _k[i % _len(_k)] for i, c in _enumerate(_b))",
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
