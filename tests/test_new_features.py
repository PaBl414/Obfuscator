"""Differential regression checks for opt-in transformations and build tools."""
from __future__ import annotations

import contextlib
import importlib.machinery
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from code_editors import PipelineConfig, process_file, process_folder, transform_layers
from code_editors.core.artifacts import compile_native_module, verify_programs
from code_editors.core.configuration import ALL_STAGE_ORDER
from code_editors.stages.function_flow import flatten_simple_functions
from code_editors.stages.xor_encryption import obfuscate_chained_literals


def output(source):
    stream = io.StringIO()
    with contextlib.redirect_stdout(stream):
        exec(compile(source, "<differential>", "exec"), {})
    return stream.getvalue()


class PolymorphicTests(unittest.TestCase):
    def test_repeated_byte_reads_use_immutable_cache(self):
        for polymorphic in (False, True):
            encoded, _ = obfuscate_chained_literals("def value(): return b'payload'\n",
                bytes(range(16)), polymorphic=polymorphic)
            namespace = {}
            exec(encoded, namespace)
            self.assertIs(namespace['value'](), namespace['value']())

    def test_single_file_reflective_member_contract(self):
        source = "class Model:\n minimum = 42\n def read(self): return self.minimum\nmodel = Model()\nprint(getattr(model, 'minimum'), model.read())\n"
        encoded, _ = transform_layers(source, PipelineConfig(seed=14, junk_count=1, layers=2,
            literal_mode="polymorphic", import_mode="runtime"))
        self.assertEqual(output(source), output(encoded))

    def test_roundtrip_all_bytes_unicode_surrogates_and_random_access(self):
        source = (
            "from concurrent.futures import ThreadPoolExecutor\n"
            "def values(index):\n"
            "    items = ['', b'', 'Zażółć 🐍', '\\ud800', " + repr(bytes(range(256))) + "]\n"
            "    return items[index]\n"
            "with ThreadPoolExecutor(4) as pool:\n"
            "    print(repr(list(pool.map(values, [4, 0, 3, 2, 1] * 4))))\n"
        )
        expected = output(source)
        for seed in range(12):
            key = bytes((seed + n) % 256 for n in range(16))
            encoded, count = obfuscate_chained_literals(source, key, polymorphic=True)
            self.assertGreater(count, 0)
            self.assertEqual(output(encoded), expected)
            self.assertEqual(encoded, obfuscate_chained_literals(source, key, polymorphic=True)[0])

    def test_every_literal_and_dispatch_mode_through_repeated_pipeline(self):
        source = "def calculate(n):\n if n: return 'yes', b'\\x00\\xff'\n return 'no', b''\nprint(calculate(1), calculate(0))\n"
        for literal in ("xor", "layered", "chained", "polymorphic"):
            for dispatch in ("branches", "table"):
                with self.subTest(literal=literal, dispatch=dispatch):
                    encoded, _ = transform_layers(source, PipelineConfig(
                        seed=53, layers=2, junk_count=1, literal_mode=literal,
                        dispatch_mode=dispatch, import_mode="runtime", extended_flow=True))
                    self.assertEqual(output(encoded), output(source))


GENERATOR = '''
def generator(log):
    try:
        incoming = yield 'ready'
        if incoming:
            yield from range(incoming)
        try:
            yield 'throw'
        except ValueError:
            log.append('caught')
        return 17
    finally:
        log.append('closed')
log = []
iterator = generator(log)
print(next(iterator), iterator.send(2), next(iterator), next(iterator))
try:
    iterator.throw(ValueError())
except StopIteration as error:
    print(error.value, log)
iterator = generator(log)
next(iterator)
iterator.close()
print(log)
'''

ASYNC = '''
import asyncio
class Context:
    async def __aenter__(self):
        return self
    async def __aexit__(self, *exc):
        print('exit', exc[0].__name__ if exc[0] else None)
async def items():
    for item in range(3):
        await asyncio.sleep(0)
        yield item
async def worker():
    result = []
    async with Context():
        async for item in items():
            if item == 1:
                continue
            result.append(item)
    try:
        await asyncio.sleep(0)
        raise ValueError('expected')
    except ValueError as error:
        result.append(str(error))
    finally:
        result.append('finally')
    return result
async def cancel():
    try:
        async with Context():
            await asyncio.sleep(100)
    finally:
        print('cancelled')
async def run():
    print(await worker())
    task = asyncio.create_task(cancel())
    await asyncio.sleep(0)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        print('done')
asyncio.run(run())
'''


class ExtendedFlowTests(unittest.TestCase):
    def test_strong_repeated_stage_order(self):
        for source in (GENERATOR, ASYNC):
            encoded, _ = transform_layers(source, PipelineConfig(seed=227, layers=2,
                junk_count=1, stage_order=ALL_STAGE_ORDER, extended_flow=True,
                literal_mode="polymorphic", dispatch_mode="table", import_mode="runtime"))
            self.assertEqual(output(encoded), output(source))

    def test_generator_and_coroutine_lifecycles(self):
        for source in (GENERATOR, ASYNC):
            expected = output(source)
            for seed in range(5):
                for mode in ("branches", "table"):
                    with self.subTest(seed=seed, mode=mode, async_source=source is ASYNC):
                        encoded, count = flatten_simple_functions(source, seed, mode, extended=True)
                        self.assertGreater(count, 0)
                        self.assertEqual(output(encoded), expected)

    def test_full_pipeline_extended_flow(self):
        for source in (GENERATOR, ASYNC):
            encoded, _ = transform_layers(source, PipelineConfig(seed=67,
                literal_mode="polymorphic", dispatch_mode="table", import_mode="runtime",
                junk_count=1, extended_flow=True))
            self.assertEqual(output(encoded), output(source))


class BuildToolsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def write(self, relative, text):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_layout_imports_resources_scripts_report_and_verification(self):
        self.write("input/pkg/__init__.py", "")
        self.write("input/pkg/helper.py", "def value(): return 42\n")
        self.write("input/pkg/consumer.py", "from .helper import value\ndef result(): return value()\n")
        self.write("input/pkg/data.txt", "resource")
        self.write("input/scripts/helper.py", "VALUE = 9\n")
        self.write("input/scripts/run.py", "from helper import VALUE\nprint(VALUE)\n")
        self.write("input/namespace/other.py", "VALUE = 7\n")
        self.write("input/main.py", "import importlib\nfrom pkg.consumer import result\nfrom namespace.other import VALUE\nfrom importlib.resources import files\nprint(result(), VALUE, importlib.import_module('pkg.helper') is not None, files('pkg').joinpath('data.txt').read_text())\n")
        report = self.root / "report.json"
        config = PipelineConfig(seed=18, junk_count=1, import_mode="runtime",
            literal_mode="polymorphic", dispatch_mode="table", extended_flow=True,
            rename_modules=True, report_path=report, verify_entry="main.py")
        process_folder(self.root / "input", self.root / "out", config)
        data = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual(data["verification"]["status"], "matched")
        self.assertNotEqual(data["module_paths"][str(Path("pkg/helper.py"))], str(Path("pkg/helper.py")))
        self.assertEqual(len(data["stages"]), 9 * 7)
        for path in (self.root / "out").rglob("*.py"):
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
        result = subprocess.run([sys.executable, "scripts/run.py"], cwd=self.root / "out", capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "9")
        result = subprocess.run([sys.executable, "-c", "import obfuscator_bootstrap; import pkg.helper; print('loaded')"],
            cwd=self.root / "out", capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "loaded")

    def test_file_verification_and_report(self):
        source = self.write("script.py", "print(input().upper())\n")
        config = PipelineConfig(seed=3, junk_count=1, verify_entry=".",
            verify_stdin="hello\n", report_path=self.root / "report.json")
        process_file(source, self.root / "out.py", config)
        self.assertEqual(json.loads(config.report_path.read_text())["verification"]["status"], "matched")

    def test_package_directory_really_moves_with_relative_resources(self):
        self.write("input/library/__init__.py", "")
        self.write("input/library/data.txt", "data")
        self.write("input/library/model.py", "from pathlib import Path\ndef read(): return Path(__file__).with_name('data.txt').read_text()\n")
        self.write("input/main.py", "from library.model import read\nprint(read())\n")
        report = self.root / "layout.json"
        process_folder(self.root / "input", self.root / "out", PipelineConfig(seed=27,
            junk_count=1, rename_modules=True, verify_entry="main.py", report_path=report))
        mapping = json.loads(report.read_text())["module_paths"]
        self.assertNotEqual(Path(mapping[str(Path('library/model.py'))]).parent, Path('library'))
        self.assertFalse((self.root / "out/library").exists())

    def test_comprehension_and_parameter_shadowing(self):
        self.write("input/main.py", "from dataclasses import field\ndef helper(default_factory): return default_factory\ndef invoke(helper): return helper(default_factory=list)\nprint(helper(default_factory=42), invoke(field).default_factory is list, [helper(default_factory=list).default_factory is list for helper in [field]])\n")
        process_folder(self.root / "input", self.root / "out", PipelineConfig(seed=41,
            junk_count=1, import_mode="runtime", verify_entry="main.py"))

    def test_verification_failure_and_timeout(self):
        first = self.write("a.py", "print('one')\n")
        second = self.write("b.py", "print('two')\n")
        with self.assertRaisesRegex(RuntimeError, "differs"):
            verify_programs(first, second, PipelineConfig())
        second.write_text("raise RuntimeError('bad')\n")
        with self.assertRaisesRegex(RuntimeError, "unsuccessfully"):
            verify_programs(first, second, PipelineConfig())
        second.write_text("import time; time.sleep(3)\n")
        with self.assertRaisesRegex(RuntimeError, "timed out"):
            verify_programs(first, second, PipelineConfig(verify_timeout=.1))

    def test_failed_file_verification_retains_previous_output(self):
        source = self.write("source.py", "print(__file__)\n")
        destination = self.write("output.py", "# previous output\n")
        with self.assertRaisesRegex(RuntimeError, "differs"):
            process_file(source, destination, PipelineConfig(seed=9, junk_count=1, verify_entry="."))
        self.assertEqual(destination.read_text(), "# previous output\n")
        self.assertFalse(list(self.root.glob('.output-*.py')))

    def test_path_preflight_protects_input(self):
        source = self.write("input/main.py", "print('safe')\n")
        for config in (
            PipelineConfig(report_path=source),
            PipelineConfig(verify_entry="../outside.py"),
            PipelineConfig(native_modules=("../outside.py",)),
        ):
            with self.assertRaises(ValueError):
                process_folder(source.parent, self.root / "out", config)
            self.assertFalse((self.root / "out").exists())
            self.assertEqual(source.read_text(), "print('safe')\n")

    def test_project_keyword_calls_and_shadowed_external_callable(self):
        self.write("input/provider.py", "def build(default_factory): return default_factory\n")
        self.write("input/main.py", "from dataclasses import dataclass, field\nfrom provider import build\ndef invoke(build):\n return build(default_factory=list)\n@dataclass\nclass Model:\n values: list = invoke(field)\nprint(Model().values, build(default_factory=42))\n")
        for mode in ("aliases", "runtime"):
            with self.subTest(mode=mode):
                process_folder(self.root / "input", self.root / mode, PipelineConfig(
                    seed=17, junk_count=1, import_mode=mode, verify_entry="main.py"))

    def test_native_adapter_retains_source_on_failure_and_removes_on_success(self):
        source = self.write("module.py", "answer = 42\n")
        failed = subprocess.CompletedProcess([], 1, "", "compiler missing")
        with patch("code_editors.core.artifacts.subprocess.run", return_value=failed):
            with self.assertRaisesRegex(RuntimeError, "compiler missing"):
                compile_native_module(source)
        self.assertTrue(source.exists())
        artifact = source.with_name(source.stem + importlib.machinery.EXTENSION_SUFFIXES[0])
        def compile_mock(*args, **kwargs):
            artifact.write_bytes(b"mock extension")
            return subprocess.CompletedProcess([], 0, "", "")
        with patch("code_editors.core.artifacts.subprocess.run", side_effect=compile_mock):
            self.assertEqual(compile_native_module(source), artifact)
        self.assertFalse(source.exists())

    def test_native_adapter_requires_artifact_and_handles_timeout(self):
        source = self.write("module.py", "answer = 42\n")
        with patch("code_editors.core.artifacts.subprocess.run",
                return_value=subprocess.CompletedProcess([], 0, "", "")):
            with self.assertRaisesRegex(RuntimeError, "did not produce"):
                compile_native_module(source)
        with patch("code_editors.core.artifacts.subprocess.run",
                side_effect=subprocess.TimeoutExpired([], 600)):
            with self.assertRaisesRegex(RuntimeError, "timed out"):
                compile_native_module(source)
        self.assertTrue(source.exists())

    @unittest.skipUnless(os.environ.get("OBFUSCATOR_TEST_NATIVE") == "1",
        "Set OBFUSCATOR_TEST_NATIVE=1 with Nuitka and a C compiler for a real native build")
    def test_real_native_build(self):
        self.write("input/mathops.py", "def answer(): return 42\n")
        self.write("input/main.py", "from mathops import answer\nprint(answer())\n")
        process_folder(self.root / "input", self.root / "out", PipelineConfig(seed=73,
            junk_count=1, literal_mode="polymorphic", import_mode="runtime",
            native_modules=("mathops.py",), verify_entry="main.py", verify_timeout=30))
        self.assertFalse((self.root / "out/mathops.py").exists())
        self.assertTrue(any((self.root / "out").glob("mathops*.pyd")) or
                        any((self.root / "out").glob("mathops*.so")))


if __name__ == "__main__":
    unittest.main()
