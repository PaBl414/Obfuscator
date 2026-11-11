"""Single-artifact packing order, import/resource behavior and CLI coverage."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from code_editors import PipelineConfig, VerificationCase, process_file, process_folder, transform_layers
from code_editors.core.hex_bundle import make_bundle
from code_editors.core.registry import STAGE_REGISTRY


class HexBundleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return path

    def run_program(self, program, *args):
        result = subprocess.run([sys.executable, str(program), *args], cwd=program.parent,
            capture_output=True, text=True, encoding='utf-8', timeout=40,
            env=dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONPATH=''))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, '')
        return result.stdout

    def test_preprocessing_is_first_and_happens_once_across_layers(self):
        records, seen = [], []
        registry = dict(STAGE_REGISTRY)
        original = registry['junk']
        def inspect(source, context):
            seen.append(source)
            return original(source, context)
        registry['junk'] = inspect
        config = PipelineConfig(seed=45, layers=2, junk_count=1, hex_pack=True,
            literal_mode='polymorphic', dispatch_mode='table')
        with patch('code_editors.core.engine.STAGE_REGISTRY', registry):
            generated, _ = transform_layers('print(42)\n', config, stage_records=records)
        self.assertIn('bytes.fromhex', seen[0])
        self.assertNotIn('print(42)', seen[0])
        self.assertEqual(records[0]['stage'], 'hexadecimal')
        self.assertEqual([item['stage'] for item in records].count('hexadecimal'), 1)
        self.assertEqual(len(records), 19)
        artifact = self.write('output.py', generated)
        self.assertEqual(self.run_program(artifact).strip(), '42')
        self.assertEqual(generated, transform_layers('print(42)\n', config)[0])

    def test_file_encoding_argv_and_exit_status_are_preserved(self):
        source = self.root / 'script.py'
        source.write_bytes("# coding: cp1250\nimport sys\nprint('zażółć', sys.argv[1:])\nsys.exit(3)\n".encode('cp1250'))
        report = self.root / 'result.json'
        process_file(source, self.root / 'out.py', PipelineConfig(seed=1, hex_pack=True,
            junk_count=1, verify_entry='.', report_path=report,
            verify_cases=(VerificationCase(args=('one', 'two'), expected_exit=3),)))
        data = json.loads(report.read_text())
        self.assertEqual(data['verification']['status'], 'matched')
        self.assertEqual(data['packing']['files'], ['script.py'])
        self.assertEqual(data['stages'][0]['stage'], 'hexadecimal')

    def test_text_api_normalizes_an_already_decoded_encoding_cookie(self):
        generated, _ = transform_layers("# coding: cp1250\nprint('zażółć')\n",
            PipelineConfig(seed=3, junk_count=1, hex_pack=True))
        self.assertEqual(self.run_program(self.write('out.py', generated)).strip(), 'zażółć')

    def test_one_movable_file_contains_imports_and_binary_resources(self):
        self.write('input/pkg/__init__.py', '')
        self.write('input/pkg/helper.py', 'from pathlib import Path\ndef answer(): return Path(__file__).with_name("data.txt").read_text(encoding="utf-8")\n')
        self.write('input/pkg/data.txt', 'zasób 🐍')
        self.write('input/main.py', 'from pkg.helper import answer\nfrom pathlib import Path\nprint(answer(), (Path(__file__).parent/"blob.bin").read_bytes().hex())\n')
        (self.root / 'input/blob.bin').write_bytes(bytes(range(256)))
        self.write('input/__pycache__/ignored.pyc', 'not source')
        output = self.root / 'release/app.py'
        process_folder(self.root / 'input', output, PipelineConfig(seed=5,
            hex_pack=True, junk_count=1, import_mode='runtime', literal_mode='chained'))
        # The only distributed artifact lives in a different directory; the
        # original input is no longer available at its original location.
        (self.root / 'input').rename(self.root / 'input-unavailable')
        self.assertEqual(list(output.parent.iterdir()), [output])
        self.assertEqual(self.run_program(output).strip(), 'zasób 🐍 ' + bytes(range(256)).hex())
        self.assertEqual(list(output.parent.iterdir()), [output])

    def test_project_module_names_do_not_shadow_loader_dependencies(self):
        self.write('input/zipfile.py', 'def answer(): return 42\n')
        self.write('input/main.py', 'import zipfile\nprint(zipfile.answer())\n')
        process_folder(self.root / 'input', self.root / 'bundle.py', PipelineConfig(seed=15,
            hex_pack=True, verify_entry='main.py', junk_count=1))

    def test_regular_package_entry_uses_module_semantics(self):
        self.write('input/pkg/__init__.py', 'VALUE = 40\n')
        self.write('input/pkg/helper.py', 'def add(x): return x + 2\n')
        self.write('input/pkg/json.py', 'raise RuntimeError("must not shadow the standard library")\n')
        self.write('input/pkg/main.py', 'from . import VALUE\nfrom .helper import add\nimport json\nprint(add(VALUE), json.dumps(3))\n')
        process_folder(self.root / 'input', self.root / 'bundle.py', PipelineConfig(seed=7,
            hex_pack=True, hex_entry='pkg/main.py', verify_entry='pkg/main.py', junk_count=1))

    def test_namespace_module_and_nested_script_entries(self):
        self.write('input/ns/helper.py', 'def answer(): return 42\n')
        self.write('input/ns/main.py', 'from .helper import answer\nprint(answer())\n')
        process_folder(self.root / 'input', self.root / 'module.py', PipelineConfig(seed=8,
            hex_pack=True, hex_entry='ns/main.py', hex_entry_mode='module',
            verify_entry='ns/main.py', junk_count=1))
        self.write('scripts/helper.py', 'VALUE = 42\n')
        self.write('scripts/run.py', 'from helper import VALUE\nprint(VALUE)\n')
        process_folder(self.root / 'scripts', self.root / 'script.py', PipelineConfig(seed=9,
            hex_pack=True, hex_entry='run.py', verify_entry='run.py', junk_count=1))
        self.write('legacy/old-scripts/__init__.py', '')
        self.write('legacy/old-scripts/helper.py', 'VALUE = 42\n')
        self.write('legacy/old-scripts/run.py', 'from helper import VALUE\nprint(VALUE)\n')
        process_folder(self.root / 'legacy', self.root / 'legacy.py', PipelineConfig(seed=10,
            hex_pack=True, hex_entry='old-scripts/run.py', verify_entry='old-scripts/run.py', junk_count=1))

    def test_entrypoint_is_discovered_automatically(self):
        self.write('nested/app/__init__.py', '')
        self.write('nested/app/main.py', "from .helper import answer\nprint(answer())\n")
        self.write('nested/app/helper.py', 'def answer(): return 42\n')
        process_folder(self.root / 'nested', self.root / 'auto.py', PipelineConfig(seed=13,
            hex_pack=True, verify_entry='app/main.py', junk_count=1))
        self.assertIn("42", self.run_program(self.root / 'auto.py'))

        self.write('only/single.py', 'print(7)\n')
        process_folder(self.root / 'only', self.root / 'single-bundle.py', PipelineConfig(seed=14,
            hex_pack=True, hex_entry='single.py', junk_count=1))

    def test_multiple_cases_can_compare_caller_relative_output_files(self):
        self.write('input/main.py', 'from pathlib import Path\nvalue=input()\nPath("result.txt").write_text(value, encoding="utf-8")\nprint(value.upper())\n')
        process_folder(self.root / 'input', self.root / 'bundle.py', PipelineConfig(seed=10,
            hex_pack=True, verify_entry='main.py', junk_count=1, verify_files=('result.txt',),
            verify_cases=(VerificationCase(stdin='one\n'), VerificationCase(stdin='two\n'))))
        self.assertFalse((self.root / 'input/result.txt').exists())
        self.assertFalse((self.root / 'result.txt').exists())

    def test_bundle_working_directory_supports_relative_resource_reads(self):
        self.write('input/data.txt', 'hello')
        self.write('input/main.py', 'from pathlib import Path\nprint(Path("data.txt").read_text())\n')
        process_folder(self.root / 'input', self.root / 'bundle.py', PipelineConfig(seed=11,
            hex_pack=True, hex_cwd='bundle', verify_entry='main.py', junk_count=1))

    def test_resources_survive_application_exit_handlers_and_are_then_removed(self):
        self.write('input/data.txt', 'cleanup resource')
        self.write('input/main.py', '''import atexit
from pathlib import Path
root = Path(__file__).parent
def final_message():
    print((root / 'data.txt').read_text())
atexit.register(final_message)
print(root)
''')
        output = self.root / 'bundle.py'
        process_folder(self.root / 'input', output, PipelineConfig(seed=12,
            hex_pack=True, hex_cwd='bundle', junk_count=1))
        lines = self.run_program(output).splitlines()
        self.assertEqual(lines[-1], 'cleanup resource')
        extracted_root = Path(lines[0])
        self.assertTrue(extracted_root.name.startswith('obfuscator-edu-'))
        self.assertFalse(extracted_root.exists())

    def test_cli_all_includes_hex_without_a_separate_flag(self):
        source = self.write('source.py', 'print(42)\n')
        report = self.root / 'report.json'
        command = [sys.executable, '-m', 'code_editors', str(source), '-o', str(self.root/'out.py'),
            '--all', '--verify', '--seed', '17', '--report', str(report)]
        result = subprocess.run(command, capture_output=True, text=True, timeout=90)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLess(result.stdout.index('Preprocess: hexadecimal'), result.stdout.index('Stage 1/'))
        data = json.loads(report.read_text())
        self.assertEqual(data['stages'][0]['stage'], 'hexadecimal')
        self.assertEqual(len(data['stages']), 25)
        self.assertTrue(data['configuration']['hex_pack'])

    def test_invalid_combinations_and_paths_fail_before_writing_output(self):
        for options in ({'hex_entry': 'main.py'}, {'hex_cwd': 'bundle'},
                        {'hex_pack': True, 'rename_modules': True},
                        {'hex_pack': True, 'native_modules': ('module.py',)},
                        {'hex_pack': True, 'verify_commands': (('-m', 'unittest'),)},
                        {'hex_pack': True, 'hex_cwd': 'bundle', 'verify_entry': 'main.py', 'verify_files': ('x',)}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                PipelineConfig(**options)
        self.write('input/main.py', 'print(42)\n')
        for output in (self.root / 'input/bundle.py', self.root / 'not-a-file'):
            with self.assertRaises(ValueError):
                process_folder(self.root / 'input', output, PipelineConfig(hex_pack=True))
            self.assertFalse(output.exists())
        with self.assertRaises(ValueError):
            process_folder(self.root / 'input', self.root / 'out.py', PipelineConfig(
                hex_pack=True, hex_entry='../outside.py'))
        self.write('ambiguous/a/main.py', 'print(1)\n')
        self.write('ambiguous/b/main.py', 'print(2)\n')
        with self.assertRaisesRegex(ValueError, '--hex-entry'):
            process_folder(self.root / 'ambiguous', self.root / 'ambiguous-bundle.py',
                           PipelineConfig(hex_pack=True))
        with self.assertRaises(ValueError):
            make_bundle({'../bad': b'bad', 'main.py': b''}, 'main.py')


if __name__ == '__main__':
    unittest.main()
