from __future__ import annotations

import contextlib
import importlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

from code_editors.pipeline import process_file, process_folder, transform


class PipelineTests(unittest.TestCase):
    def test_single_file_pipeline_writes_transformed_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.py"
            output = root / "output.py"
            source.write_text("answer = 40 + 2\n", encoding="utf-8")

            result = process_file(source, output, seed=19, junk_count=1)

            self.assertTrue(result[0].startswith("Saved:"))
            namespace: dict[str, object] = {}
            exec(compile(output.read_text(encoding="utf-8"), str(output), "exec"), namespace)
            self.assertIn(42, namespace.values())

    def test_runtime_import_mode_hides_all_symbols_in_multi_name_import(self) -> None:
        from code_editors.stages.import_obfuscator import obfuscate_imports_runtime

        source = "from math import ceil, floor as round_down\n"
        transformed, changed, skipped = obfuscate_imports_runtime(source, seed=9)

        self.assertEqual(changed, 2)
        self.assertEqual(skipped, 0)
        self.assertNotIn("from math import", transformed)
        namespace: dict[str, object] = {}
        exec(compile(transformed, "<runtime-imports>", "exec"), namespace)
        self.assertEqual(namespace["ceil"](1.2), 2)
        self.assertEqual(namespace["round_down"](1.8), 1)

    def test_full_pipeline_supports_empty_modules(self) -> None:
        transformed, report = transform("", seed=3, junk_count=2)

        compile(transformed, "<empty-module>", "exec")
        self.assertTrue(any("Junk editor: inserted 2 blocks" in item for item in report))

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

    def test_folder_pipeline_preserves_cross_module_keyword_contracts(self) -> None:
        package_name = "keyword_contract_pkg"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            package = source / package_name
            package.mkdir(parents=True)
            (package / "__init__.py").write_text("", encoding="utf-8")
            (package / "models.py").write_text(
                "from dataclasses import dataclass\n\n"
                "@dataclass\n"
                "class Plan:\n"
                "    upgrades: tuple[int, ...]\n",
                encoding="utf-8",
            )
            (package / "consumer.py").write_text(
                "from .models import Plan\n\n"
                "result = Plan(upgrades=(1, 2, 3)).upgrades\n",
                encoding="utf-8",
            )
            output = root / "output"
            events: list[str] = []
            strong_order = tuple(
                "imports,junk,xor,name_mangler,function_flow,module_flow,"
                "flattener,junk,xor,name_mangler,numeric,cleanup".split(",")
            )
            process_folder(
                source,
                output,
                seed=29,
                junk_count=1,
                stage_order=strong_order,
                import_mode="runtime",
                progress=events.append,
            )

            stage_one_starts = [
                event for event in events if "Stage 1/12" in event and "started" in event
            ]
            stage_two_starts = [
                event for event in events if "Stage 2/12" in event and "started" in event
            ]
            self.assertEqual(len(stage_one_starts), 3)
            self.assertEqual(len(stage_two_starts), 3)
            self.assertLess(
                max(events.index(event) for event in stage_one_starts),
                min(events.index(event) for event in stage_two_starts),
            )

            consumer_source = (output / package_name / "consumer.py").read_text(
                encoding="utf-8"
            )
            self.assertNotIn("Plan", consumer_source)
            self.assertNotIn("upgrades", consumer_source)

            sys.path.insert(0, str(output))
            try:
                consumer = importlib.import_module(f"{package_name}.consumer")
                self.assertIn((1, 2, 3), vars(consumer).values())
            finally:
                sys.path.remove(str(output))
                for name in tuple(sys.modules):
                    if name == package_name or name.startswith(f"{package_name}."):
                        del sys.modules[name]

if __name__ == "__main__":
    unittest.main()
