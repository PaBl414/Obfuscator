"""Behavioral regression checks for dependent literals and table dispatch."""

import ast
import contextlib
import io
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from code_editors import PipelineConfig, process_folder, transform_layers
from code_editors.stages import flattener, flow_obfuscator, function_flow
from code_editors.stages.xor_encryption import obfuscate_chained_literals
from code_editors.stages.xor_encryption import build_decryptor, transform_source


class AdvancedObfuscationTests(unittest.TestCase):
    def test_decoders_survive_builtin_names_shadowed_by_source(self):
        source = (
            "def value():\n    return 'hello', b'bytes'\n"
            "bytes = len = enumerate = bytearray = tuple = str = None\n"
            "result = value()\n"
        )
        key = b"capture-builtins"
        for mode in ("xor", "layered", "chained"):
            with self.subTest(mode=mode):
                if mode == "chained":
                    encoded, _ = obfuscate_chained_literals(source, key)
                else:
                    encoded, _ = transform_source(source, key, mode=mode)
                    encoded = "\n".join(build_decryptor(key, mode=mode)) + "\n" + encoded
                namespace = {}
                exec(encoded, namespace)
                self.assertEqual(namespace["result"], ("hello", b"bytes"))

    def test_docstrings_annotations_patterns_and_fstrings_keep_their_semantics(self):
        source = '''"module documentation"
from __future__ import annotations
def classify(value: "unknown") -> "unknown":
    "function documentation"
    match value:
        case "one":
            return f"label: {value}"
        case _:
            return "other"
result = classify("one"), classify("two")
'''
        encoded, _ = obfuscate_chained_literals(source, b"header-protection")
        before, after = {}, {}
        exec(source, before)
        exec(encoded, after)
        self.assertEqual(before["result"], after["result"])
        self.assertEqual(before["__doc__"], after["__doc__"])
        self.assertEqual(before["classify"].__doc__, after["classify"].__doc__)
        self.assertEqual(before["classify"].__annotations__, after["classify"].__annotations__)

    def test_chained_literals_allow_random_order_recursion_and_threads(self):
        source = r'''
def first():
    return "Zażółć 🐍"
def last():
    return b"\x00\xff", "\ud800", "", b""
def recursive(n):
    if n:
        return "A" + recursive(n - 1)
    return "Z"
'''
        for seed in range(5):
            key = bytes([seed + 1]) * 16
            encoded, count = obfuscate_chained_literals(source, key)
            self.assertEqual(count, 7)
            namespace = {}
            exec(encoded, namespace)
            def evaluate(_):
                return namespace["last"](), namespace["recursive"](3), namespace["first"]()
            expected = ((b"\x00\xff", "\ud800", "", b""), "AAAZ", "Zażółć 🐍")
            with ThreadPoolExecutor(max_workers=4) as executor:
                self.assertEqual(list(executor.map(evaluate, range(12))), [expected] * 12)

    def test_later_literal_depends_on_previous_record(self):
        encoded, _ = obfuscate_chained_literals("a=b'first'\nb=b'second'\n", b"k" * 16)
        namespace = {}
        exec(encoded, namespace)
        self.assertEqual(namespace["_db"](1), b"second")
        tree = ast.parse(encoded)
        factory = tree.body[0]
        records = ast.literal_eval(factory.args.defaults[0])
        damaged = ((records[0][0], "00" * 16), records[1])
        factory.args.defaults[0] = ast.parse(repr(damaged), mode="eval").body
        ast.fix_missing_locations(tree)
        damaged_namespace = {}
        exec(compile(tree, "<damaged-key-chain>", "exec"), damaged_namespace)
        self.assertNotEqual(damaged_namespace["b"], b"second")

    def test_table_dispatch_preserves_locals_exceptions_loops_and_return(self):
        source = '''
def calculate(limit):
    total = 0
    for index in range(limit):
        if index == 2:
            continue
        if index == 7:
            break
        total += index
    try:
        if limit < 0:
            raise ValueError("negative")
        if total > 5:
            return total, "large"
        return total, "small"
    finally:
        total += 1
'''
        for seed in range(8):
            encoded, count = function_flow.flatten_simple_functions(
                source, seed=seed, dispatch_mode="table"
            )
            self.assertEqual(count, 1)
            tree = ast.parse(encoded)
            self.assertTrue(any(isinstance(node, ast.Match) for node in ast.walk(tree)))
            original, transformed = {}, {}
            exec(source, original)
            exec(encoded, transformed)
            for limit in (0, 1, 4, 12):
                self.assertEqual(original["calculate"](limit), transformed["calculate"](limit))
            with self.assertRaisesRegex(ValueError, "negative"):
                transformed["calculate"](-1)

    def test_module_table_keeps_opaque_predicates(self):
        source = "total=0\nfor index in range(5):\n    total+=index\n"
        encoded = flow_obfuscator.obfuscate(source, seed=10, dispatch_mode="table")
        encoded, count = flattener.add_opaque_predicates(encoded, seed=12)
        self.assertGreater(count, 1)
        namespace = {}
        exec(encoded, namespace)
        self.assertEqual(namespace["total"], 10)

    def test_folder_preserves_flow_context_across_stages(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            (source / "app.py").write_text("print('folder works')\n", encoding="utf-8")
            events = []
            config = PipelineConfig(
                seed=32, layers=2, junk_count=1, dispatch_mode="table", literal_mode="chained"
            )
            output = root / "output"
            process_folder(source, output, config, progress=events.append)
            self.assertGreaterEqual(
                sum("Flattener: added" in event for event in events), 2
            )
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                exec((output / "app.py").read_text(encoding="utf-8"), {})
            self.assertEqual(stream.getvalue(), "folder works\n")

    def test_flattener_survives_mangling_of_dispatcher_name(self):
        source = flow_obfuscator.obfuscate("value=42\n", seed=3, dispatch_mode="table")
        tree = ast.parse(source)
        loop = next(node for node in tree.body if isinstance(node, ast.While))
        original_name = loop.test.left.id
        renamed = source.replace(original_name, "randomized_identifier")
        encoded, count = flattener.add_opaque_predicates(renamed, seed=7)
        namespace = {}
        exec(encoded, namespace)
        self.assertGreater(count, 1)
        self.assertEqual(namespace["value"], 42)

    def test_complete_pipeline_repeated_encoders_and_layers(self):
        source = '''
def calculate(value):
    if value % 2:
        return "odd", b"data"
    return "even", b""
for index in range(4):
    print(calculate(index))
'''
        order = tuple(
            "imports,junk,xor,name_mangler,function_flow,module_flow,"
            "flattener,junk,xor,name_mangler,numeric,cleanup".split(",")
        )
        expected = io.StringIO()
        with contextlib.redirect_stdout(expected):
            exec(source, {})
        for seed in (11, 22):
            encoded, _ = transform_layers(source, PipelineConfig(
                seed=seed, layers=2, junk_count=1, literal_mode="chained",
                dispatch_mode="table", stage_order=order,
            ))
            actual = io.StringIO()
            with contextlib.redirect_stdout(actual):
                exec(encoded, {})
            self.assertEqual(actual.getvalue(), expected.getvalue())


if __name__ == "__main__":
    unittest.main()
