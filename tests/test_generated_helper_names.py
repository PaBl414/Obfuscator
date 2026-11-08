"""Generated helper code must not expose fixed decode/chr identifiers."""

import ast
import builtins
import unittest

from code_editors.stages.import_obfuscator import obfuscate_imports_runtime
from code_editors.stages.xor_encryption import (
    build_decryptor,
    obfuscate_chained_literals,
    transform_source,
)


class GeneratedHelperNamesTests(unittest.TestCase):
    def assert_hidden_names(self, source):
        names = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Name):
                names.add(node.id)
            elif isinstance(node, ast.Attribute):
                names.add(node.attr)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                names.add(node.name)
            elif isinstance(node, ast.arg):
                names.add(node.arg)
        self.assertFalse({"decode", "chr"} & names)

    def test_runtime_import_helper_hides_names_and_survives_shadowed_builtins(self):
        original = (
            "from math import floor\n"
            "str = bytes = chr = None\n"
            "from math import ceil\n"
            "answer = floor(1.8), ceil(1.8)\n"
        )
        # Inspect only the generated prefix: chr in the original assignment
        # must be preserved rather than rewritten as if it belonged to a helper.
        for seed in (1, 2, 3):
            encoded, changed, _ = obfuscate_imports_runtime(original, seed=seed)
            self.assertEqual(changed, 2)
            tree = ast.parse(encoded)
            self.assert_hidden_names(ast.unparse(tree.body[0]))
            for environment in (builtins, vars(builtins)):
                namespace = {"__builtins__": environment}
                exec(encoded, namespace)
                self.assertEqual(namespace["answer"], (1, 2))

    def test_literal_helpers_have_no_fixed_decode_names(self):
        source = "answer = 'Zażółć 🐍', b'\\x00\\xff'\n"
        master = bytes(range(16))
        for mode in ("xor", "layered", "chained"):
            with self.subTest(mode=mode):
                if mode == "chained":
                    encoded, _ = obfuscate_chained_literals(source, master)
                else:
                    encoded, _ = transform_source(source, master, mode=mode)
                    encoded = "\n".join(build_decryptor(master, mode=mode)) + "\n" + encoded
                self.assert_hidden_names(encoded)
                namespace = {}
                exec(encoded, namespace)
                self.assertEqual(namespace["answer"], ("Zażółć 🐍", b"\x00\xff"))

    def test_randomized_names_are_reproducible_by_seed(self):
        source = "import math\n"
        first, _, _ = obfuscate_imports_runtime(source, seed=7)
        repeated, _, _ = obfuscate_imports_runtime(source, seed=7)
        another, _, _ = obfuscate_imports_runtime(source, seed=8)
        self.assertEqual(first, repeated)
        self.assertNotEqual(first, another)
