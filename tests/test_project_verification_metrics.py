"""Regression coverage for project references, execution plans and metrics."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from code_editors import PipelineConfig, process_file, process_folder
from code_editors.core.configuration import VerificationCase
from code_editors.core.verification import load_verification_plan, verify_programs
from code_editors.cli import main


class ProjectVerificationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def write(self, name, source):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding='utf-8')
        return path

    def test_module_alias_reexports_inheritance_and_callable_alias(self):
        self.write('input/lib/__init__.py', 'from .provider import Base as Exported\n')
        self.write('input/lib/provider.py', '''CONST = 40
class Base:
    minimum = 2
    def total(self, amount=0):
        return CONST + self.minimum + amount
''')
        self.write('input/child.py', '''from lib import Exported as Parent
class Child(Parent):
    def calculate(self, amount):
        return self.total(amount=amount)
''')
        self.write('input/main.py', '''import lib.provider as provider
from child import Child as Model
Alias = Model
obj = Alias()
callback = obj.calculate
print(provider.CONST, callback(amount=3), obj.minimum)
''')
        for mode in ('runtime', 'aliases'):
            with self.subTest(mode=mode):
                report = self.root / (mode + '.json')
                process_folder(self.root / 'input', self.root / mode, PipelineConfig(seed=17,
                    junk_count=1, import_mode=mode, literal_mode='polymorphic',
                    dispatch_mode='table', verify_entry='main.py', report_path=report))
                data = json.loads(report.read_text())
                self.assertEqual(data['verification']['status'], 'matched')
                self.assertTrue(data['symbol_scopes']['main.py'])

    def test_shadowed_module_alias_and_unknown_factory_keep_keywords(self):
        self.write('input/provider.py', 'def build(default_factory): return default_factory\n')
        self.write('input/main.py', '''from dataclasses import field
import provider as lib
def invoke(lib):
    return lib(default_factory=list)
def factory():
    return lib.build
callback = factory()
print(invoke(field).default_factory is list, callback(default_factory=42))
''')
        process_folder(self.root / 'input', self.root / 'output', PipelineConfig(seed=88,
            junk_count=1, import_mode='runtime', verify_entry='main.py'))

    def test_module_factory_and_branch_dependent_alias(self):
        self.write('input/provider.py', 'def answer(): return 42\n')
        self.write('input/main.py', '''import sys
import provider as lib
from types import SimpleNamespace
def factory(): return lib
print(factory().answer())
selected = lib
if len(sys.argv) > 1:
    selected = SimpleNamespace(answer=lambda: 17)
print(selected.answer())
''')
        config = PipelineConfig(seed=22, junk_count=1, import_mode='runtime', verify_entry='main.py',
            verify_cases=(VerificationCase(args=()), VerificationCase(args=('external',))))
        process_folder(self.root / 'input', self.root / 'out', config)

    def test_plan_cases_files_and_repeated_runtime_measurements(self):
        source = self.write('input/main.py', '''import json, sys
from pathlib import Path
value = input()
Path('result.json').write_text(json.dumps([value, sys.argv[1:]]), encoding='utf-8')
print(value.upper())
if value == 'exit':
    sys.exit(2)
''')
        plan = self.write('cases.json', json.dumps({'version': 1,
            'cases': [{'name': 'unicode', 'stdin': 'zażółć\n', 'args': ['--one']},
                      {'name': 'expected-error', 'stdin': 'exit\n', 'expected_exit': 2}],
            'files': ['result.json']}))
        settings = load_verification_plan(plan)
        report = self.root / 'report.json'
        process_folder(source.parent, self.root / 'out', PipelineConfig(seed=71,
            junk_count=1, verify_entry='main.py', report_path=report, metrics=True,
            benchmark_runs=2, **settings))
        data = json.loads(report.read_text())
        self.assertEqual(len(data['verification']['cases']), 2)
        self.assertEqual(data['verification']['cases'][0]['runs'], 2)
        self.assertIn('result.json', data['verification']['cases'][0]['files'])
        self.assertGreater(data['verification']['cases'][0]['transformed_seconds'], 0)
        self.assertFalse((source.parent / 'result.json').exists())
        self.assertFalse((self.root / 'out/result.json').exists())
        self.assertGreater(data['metrics']['growth_ratio'], 0)
        self.assertTrue(all(stage['seconds'] >= 0 for stage in data['stages']))

    def test_file_mismatch_is_detected_and_failed_build_can_be_replayed(self):
        first = self.write('a/main.py', "from pathlib import Path\nPath('x.bin').write_bytes(b'one')\n")
        second = self.write('b/main.py', "from pathlib import Path\nPath('x.bin').write_bytes(b'two')\n")
        config = PipelineConfig(verify_entry='main.py', verify_files=('x.bin',))
        with self.assertRaisesRegex(RuntimeError, 'files differ'):
            verify_programs(first, second, config)
        self.assertFalse((first.parent / 'x.bin').exists())
        broken = self.write('input/main.py', 'print(__file__)\n')
        report = self.root / 'failure.json'
        with self.assertRaisesRegex(RuntimeError, 'differs'):
            process_folder(broken.parent, self.root / 'out', PipelineConfig(junk_count=1,
                verify_entry='main.py', report_path=report))
        data = json.loads(report.read_text())
        self.assertEqual(data['status'], 'failed')
        self.assertIsInstance(data['configuration']['seed'], int)
        self.assertFalse((self.root / 'out').exists())

    def test_external_test_harness_checks_public_api_of_generated_modules(self):
        self.write('input/api.py', "__all__ = ['answer']\ndef answer(): return 42\n")
        harness = self.write('contract.py', '''import unittest
from api import answer
class Contract(unittest.TestCase):
    def test_answer(self):
        self.assertEqual(answer(), 42)
unittest.main()
''')
        report = self.root / 'suite.json'
        process_folder(self.root / 'input', self.root / 'out', PipelineConfig(seed=21,
            junk_count=1, verify_commands=((str(harness),),), report_path=report))
        data = json.loads(report.read_text())
        self.assertEqual(data['verification']['test_commands'][0]['status'], 'passed')
        self.assertEqual(data['verification']['cases'], [])

    def test_public_class_api_can_be_kept_for_external_suites(self):
        self.write('input/api.py', 'class Model:\n def answer(self): return 42\n')
        harness = self.write('contract.py', 'from api import Model\nassert Model().answer() == 42\n')
        process_folder(self.root / 'input', self.root / 'out', PipelineConfig(seed=32,
            junk_count=1, keep_names=('Model', 'answer'), verify_commands=((str(harness),),)))

    def test_test_suite_cannot_silently_discover_zero_tests(self):
        self.write('input/main.py', 'print(42)\n')
        harness = self.write('empty_suite.py', 'import unittest\nunittest.main()\n')
        with self.assertRaisesRegex(RuntimeError, 'ran no tests'):
            process_folder(self.root / 'input', self.root / 'out', PipelineConfig(seed=2,
                junk_count=1, verify_commands=((str(harness),),)))

    def test_metrics_replay_effective_seed_produces_identical_source(self):
        source = self.write('main.py', "print('hello')\n")
        report = self.root / 'metrics.json'
        process_file(source, self.root / 'out.py', PipelineConfig(junk_count=1,
            report_path=report, metrics=True))
        data = json.loads(report.read_text())
        process_file(source, self.root / 'replay.py', PipelineConfig(junk_count=1,
            seed=data['configuration']['seed']))
        self.assertEqual((self.root / 'out.py').read_bytes(), (self.root / 'replay.py').read_bytes())
        self.assertGreater(data['metrics']['transform_seconds'], 0)
        self.assertEqual(len(data['stages']), 9)

    def test_plan_validation_rejects_invalid_paths_and_schema(self):
        for value in ({'cases': [{'args': 'bad'}]}, {'commands': ['echo bad']},
                      {'version': 99}, {'unknown': 1}):
            path = self.write('plan.json', json.dumps(value))
            with self.assertRaises(ValueError):
                load_verification_plan(path)
        for path in ('../secret', str(self.root / 'outside')):
            with self.assertRaises(ValueError):
                PipelineConfig(verify_entry='main.py', verify_files=(path,))

    def test_cli_plan_metrics_and_contract_options_reach_configuration(self):
        plan = self.write('plan.json', json.dumps({'cases': [{'stdin': '12\n'}], 'files': ['result.json']}))
        with patch('code_editors.cli.process_folder', return_value=['Saved']) as process:
            self.assertEqual(main(['project', '--folder', '--verify-entry', 'main.py',
                '--verify-cases', str(plan), '--benchmark-runs', '3', '--metrics',
                '--keep-name', 'PublicAPI']), 0)
        config = process.call_args.args[2]
        self.assertEqual(config.benchmark_runs, 3)
        self.assertTrue(config.metrics)
        self.assertEqual(config.keep_names, ('PublicAPI',))
        self.assertEqual(config.verify_cases[0].stdin, '12\n')

    def test_stage_failure_report_includes_failing_stage(self):
        source = self.write('main.py', 'print(42)\n')
        report = self.root / 'failure.json'
        from code_editors.core.registry import STAGE_REGISTRY
        registry = dict(STAGE_REGISTRY)
        def fail(source, context):
            raise RuntimeError('reproducible failure')
        registry['imports'] = fail
        with patch('code_editors.core.engine.STAGE_REGISTRY', registry):
            with self.assertRaisesRegex(RuntimeError, 'reproducible'):
                process_file(source, self.root / 'out.py', PipelineConfig(report_path=report))
        data = json.loads(report.read_text())
        self.assertEqual(data['stages'][-1]['stage'], 'imports')
        self.assertEqual(data['stages'][-1]['status'], 'failed')
        self.assertIsInstance(data['configuration']['seed'], int)


if __name__ == '__main__':
    unittest.main()
