from __future__ import annotations

import contextlib
import importlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

from code_editors.pipeline import process_folder, transform


class PipelineTests(unittest.TestCase):
    def test_full_pipeline_preserves_output_with_loops(self) -> None:
        source = '''\
import math

def calculate(limit):
    total = 0
    for value in range(limit):
        if value == 2:
            continue
        if value == 7:
            break
        total += value
    return total

message = "pipeline works"
print(calculate(10), message, math.floor(1.5))
'''
        transformed, report = transform(source, seed=1234, junk_count=2)

        outputs = []
        for program in (source, transformed):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exec(compile(program, "<pipeline-integration>", "exec"), {})
            outputs.append(output.getvalue())

        self.assertEqual(outputs[0], outputs[1])
        function_flow_report = next(
            item for item in report if "Function flow: flattened " in item
        )
        self.assertNotIn("flattened 0", function_flow_report)
        self.assertTrue(any("per-literal keys" in item for item in report))

    def test_pipeline_skips_unsafe_flow_and_preserves_all_exports(self) -> None:
        source = '__all__ = ["answer"]\nanswer = 42\n'

        transformed, report = transform(source, seed=7, junk_count=1)

        namespace: dict[str, object] = {}
        exec(compile(transformed, "<pipeline-all>", "exec"), namespace)
        self.assertEqual(namespace["__all__"], ["answer"])
        self.assertEqual(namespace["answer"], 42)
        self.assertTrue(any("Flow obfuscator: skipped" in item for item in report))
        self.assertTrue(any("Name mangler: skipped" in item for item in report))

    def test_folder_pipeline_preserves_cross_module_imports(self) -> None:
        package_name = "transformed_sample_pkg"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            package = source / package_name
            package.mkdir(parents=True)
            (package / "__init__.py").write_text(
                '__all__ = ["value"]\nfrom .provider import value\n',
                encoding="utf-8",
            )
            (package / "provider.py").write_text(
                "value = 42\n", encoding="utf-8"
            )
            (package / "consumer.py").write_text(
                "from .provider import value\n", encoding="utf-8"
            )
            output = root / "output"
            process_folder(source, output, seed=13, junk_count=1)

            sys.path.insert(0, str(output))
            try:
                imported = importlib.import_module(package_name)
                consumer = importlib.import_module(f"{package_name}.consumer")
                self.assertEqual(imported.value, 42)
                self.assertEqual(consumer.value, 42)
            finally:
                sys.path.remove(str(output))
                for name in tuple(sys.modules):
                    if name == package_name or name.startswith(f"{package_name}."):
                        del sys.modules[name]


if __name__ == "__main__":
    unittest.main()
