"""Regression tests for defects found in production-like projects."""

import unittest
import ast
import random
import importlib
import sys
import tempfile
from pathlib import Path

from code_editors import PipelineConfig
from code_editors.core.registry import mangle_source
from code_editors.stages.function_flow import flatten_simple_functions
from code_editors.core.filesystem import process_folder


class ObfuscationRegressionTests(unittest.TestCase):
    def test_shared_mapping_renames_scope_declarations_and_bindings(self) -> None:
        source = '''\
total = 0
def outer():
    fields = {}
    current_field = None
    def flush():
        nonlocal fields, \\
            current_field
        global total
        total += len(fields)
        fields = {}
        current_field = "done"
    fields["entry"] = 1
    flush()
    return fields, current_field, total
result = outer()
'''
        mapping = {name: "hidden_" + name for name in (
            "total", "fields", "current_field", "outer", "result"
        )}
        transformed, _ = mangle_source(
            source, 5, random.Random(1), shared_name_mapping=mapping
        )
        namespace = {}
        exec(compile(transformed, "<scope-declarations>", "exec"), namespace)
        self.assertEqual(namespace["hidden_result"], ({}, "done", 1))
        self.assertIn("nonlocal hidden_fields", transformed)
        self.assertIn("global hidden_total", transformed)

    def test_external_keyword_is_untouched_when_same_name_is_internal(self) -> None:
        source = '''\
from dataclasses import dataclass, field
def helper(default_factory):
    return default_factory
def make():
    return helper(default_factory=42)
@dataclass
class Config:
    value: int = field(default_factory=make)
'''
        mapping = {
            "default_factory": "hidden_factory",
            "helper": "hidden_helper",
            "make": "hidden_make",
            "Config": "hidden_config",
            "value": "hidden_value",
        }
        transformed, _ = mangle_source(
            source,
            5,
            random.Random(1),
            attribute_mapping={"value": "hidden_value"},
            shared_name_mapping=mapping,
            keyword_names={"default_factory"},
            internal_callable_names={"helper"},
        )
        self.assertIn("field(default_factory=hidden_make)", transformed)
        self.assertIn("hidden_helper(hidden_factory=42)", transformed)
        namespace = {}
        exec(compile(transformed, "<keyword-collision>", "exec"), namespace)
        self.assertEqual(namespace["hidden_config"]().hidden_value, 42)

    def test_inherited_external_method_does_not_follow_same_named_project_method(self) -> None:
        source = '''\
class LocalCounter:
    def close(self):
        return "counter closed"
    def finish(self):
        return self.close()
class QtWindow(QMainWindow):
    def finish(self):
        return self.close()
'''
        transformed, _ = mangle_source(
            source,
            5,
            random.Random(2),
            attribute_mapping={"close": "hidden_close", "finish": "hidden_finish"},
        )
        self.assertIn("def hidden_close(", transformed)
        self.assertIn(".close()", transformed)
        self.assertIn(".hidden_close()", transformed)

    def test_class_method_does_not_use_a_local_name_mapping(self) -> None:
        source = "class Model:\n    def rules(self):\n        return 42\n"
        transformed, _ = mangle_source(
            source,
            5,
            random.Random(3),
            shared_name_mapping={"rules": "renamed_rules"},
            attribute_mapping={},
        )
        self.assertIn("def rules(", transformed)
        namespace = {}
        exec(compile(transformed, "<method-scope>", "exec"), namespace)
        model_type = next(value for value in namespace.values() if isinstance(value, type))
        self.assertEqual(model_type().rules(), 42)

    def test_string_reflection_keeps_dataclass_member_names_consistent(self) -> None:
        source = '''\
from dataclasses import dataclass
@dataclass
class Rules:
    minimum: int = 0
def read(rules):
    keys = ("minimum",)
    return getattr(rules, keys[0])
result = read(Rules(minimum=42))
'''
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_folder = root / "input"
            input_folder.mkdir()
            (input_folder / "model.py").write_text(source, encoding="utf-8")
            output_folder = root / "output"
            process_folder(input_folder, output_folder, PipelineConfig(seed=9, junk_count=1))
            namespace = {}
            exec(
                compile(
                    (output_folder / "model.py").read_text(encoding="utf-8"),
                    "<reflective-member>",
                    "exec",
                ),
                namespace,
            )
            self.assertIn(42, namespace.values())

    def test_excluded_shared_name_stays_consistent_across_module_and_method(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            package_name = "excluded_shared_name_pkg"
            package = source / package_name
            package.mkdir(parents=True)
            (package / "__init__.py").write_text("", encoding="utf-8")
            (package / "provider.py").write_text(
                "def launch():\n"
                "    return 42\n"
                "class Window:\n"
                "    def launch(self):\n"
                "        return 7\n"
                "def use(window):\n"
                "    return window.launch()\n",
                encoding="utf-8",
            )
            (package / "consumer.py").write_text(
                "from .provider import launch\nresult = launch()\n",
                encoding="utf-8",
            )
            output = root / "output"
            process_folder(source, output, PipelineConfig(seed=12, junk_count=1))
            sys.path.insert(0, str(output))
            try:
                consumer = importlib.import_module(f"{package_name}.consumer")
                self.assertIn(42, vars(consumer).values())
            finally:
                sys.path.remove(str(output))
                for module_name in tuple(sys.modules):
                    if module_name == package_name or module_name.startswith(
                        f"{package_name}."
                    ):
                        del sys.modules[module_name]

    def test_locals_introspection_is_left_unchanged(self) -> None:
        source = """\
def inspect_locals(value):
    first = value + 1
    return locals()
"""
        transformed, count = flatten_simple_functions(source, seed=1)

        self.assertEqual(count, 0)
        self.assertEqual(transformed, source)

    def test_repeated_mangling_keeps_class_fields_and_accesses_aligned(self) -> None:
        # A field and a local can share the original spelling. The field
        # declaration must follow the shared attribute map, while the local
        # still follows the module map.
        source = """\
class FutureOutlook:
    target_grade: int

    def read(self):
        target_grade = 1
        return self.target_grade + target_grade
"""
        transformed, _ = mangle_source(
            source, 5, random.Random(4), attribute_mapping={"target_grade": "x3rtO"}
        )
        tree = ast.parse(transformed)
        cls = next(node for node in tree.body if isinstance(node, ast.ClassDef))
        field = next(node for node in cls.body if isinstance(node, ast.AnnAssign))
        read = next(node for node in cls.body if isinstance(node, ast.FunctionDef))
        access = next(
            node for node in ast.walk(read)
            if isinstance(node, ast.Attribute) and node.attr == "x3rtO"
        )
        self.assertEqual(field.target.id, "x3rtO")
        self.assertEqual(access.attr, "x3rtO")


if __name__ == "__main__":
    unittest.main()
