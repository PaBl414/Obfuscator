"""Explicit opt-in to namespace-reader transformations."""

import unittest
from unittest.mock import patch

from code_editors.cli import main

from code_editors.stages.flow_obfuscator import UnsafeToTransform, obfuscate
from code_editors.stages.function_flow import flatten_simple_functions
from code_editors.stages.import_obfuscator import obfuscate_imports_runtime


class IntrospectionModeTests(unittest.TestCase):
    def test_cli_passes_the_explicit_opt_in(self):
        with patch("code_editors.cli.process_file", return_value=["Saved"]) as process:
            self.assertEqual(main(["app.py", "--allow-introspection"]), 0)
        self.assertTrue(process.call_args.args[2].allow_introspection)

    def test_flow_can_opt_in_to_each_namespace_reader(self):
        for name in ("locals", "globals", "vars", "dir"):
            source = f"def inspect_namespace(value):\n    return len({name}()) > 0\n"
            with self.subTest(name=name):
                _, count = flatten_simple_functions(source, seed=5)
                self.assertEqual(count, 0)
                encoded, count = flatten_simple_functions(
                    source, seed=5, dispatch_mode="table", allow_introspection=True
                )
                self.assertEqual(count, 1)
                namespace = {}
                exec(encoded, namespace)
                self.assertTrue(namespace["inspect_namespace"](42))

    def test_module_mode_keeps_high_risk_checks(self):
        source = "names = locals()\nanswer = 42\n"
        with self.assertRaises(UnsafeToTransform):
            obfuscate(source, seed=3)
        encoded = obfuscate(source, seed=3, allow_introspection=True)
        namespace = {}
        exec(encoded, namespace)
        self.assertEqual(namespace["answer"], 42)
        for sensitive in ("eval", "exec", "compile", "__import__"):
            with self.subTest(sensitive=sensitive), self.assertRaises(UnsafeToTransform):
                obfuscate(f"function = {sensitive}\n", allow_introspection=True)

    def test_runtime_imports_can_opt_in_without_allowing_import_hooks(self):
        source = "from math import floor\nsnapshot = locals()\nresult = floor(1.5)\n"
        _, count, _ = obfuscate_imports_runtime(source, seed=3)
        self.assertEqual(count, 0)
        encoded, count, _ = obfuscate_imports_runtime(
            source, seed=3, allow_introspection=True
        )
        self.assertEqual(count, 1)
        namespace = {}
        exec(encoded, namespace)
        self.assertEqual(namespace["result"], 1)
        unsafe = "import builtins\nsnapshot = locals()\n"
        encoded, count, _ = obfuscate_imports_runtime(unsafe, allow_introspection=True)
        self.assertEqual(encoded, unsafe)
        self.assertEqual(count, 0)
