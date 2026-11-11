"""Selected source stages and once-only optional operations."""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from code_editors import PipelineConfig, process_file, process_folder, transform_layers
from code_editors.cli import main
from code_editors.core.configuration import REGISTERED_STAGE_IDS
from code_editors.core.hex_bundle import bundle_file, bundle_project
from code_editors.core.filesystem import SKIPPED_DIRECTORIES


class StageSelectionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.project = self.root / 'input'
        self.project.mkdir()
        (self.project / 'worker.py').write_text(
            'VALUE = 40\ndef calculate(extra=2):\n    return VALUE + extra\n', encoding='utf-8')
        (self.project / 'main.py').write_text(
            'from worker import calculate\nprint(calculate(extra=2))\n', encoding='utf-8')

    def run_program(self, path):
        result = subprocess.run([sys.executable, str(path)], cwd=path.parent,
            env=dict(os.environ, PYTHONPATH=''), capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, '')
        self.assertEqual(result.stdout.strip(), '42')

    def test_each_source_stage_alone_in_text_and_folder(self):
        for stage in REGISTERED_STAGE_IDS:
            with self.subTest(stage=stage):
                records = []
                source, _ = transform_layers('answer = 40 + 2\nprint(answer)\n',
                    PipelineConfig(seed=9, junk_count=1, stage_order=(stage,)), stage_records=records)
                self.assertEqual([record['stage'] for record in records], [stage])
                output = self.root / f'{stage}.py'
                output.write_text(source, encoding='utf-8')
                self.run_program(output)
                destination = self.root / stage
                process_folder(self.project, destination,
                    PipelineConfig(seed=9, junk_count=1, stage_order=(stage,)))
                self.run_program(destination / 'main.py')
                if stage not in ('name_mangler', 'imports'):
                    self.assertIn('def calculate(', (destination / 'worker.py').read_text(encoding='utf-8'))
                    self.assertIn('from worker import calculate',
                                  (destination / 'main.py').read_text(encoding='utf-8'))

    def test_empty_sequence_preserves_sources(self):
        source = '# keep this comment\nanswer = 42\n'
        self.assertEqual(transform_layers(source, PipelineConfig(stage_order=()))[0], source)
        destination = self.root / 'unchanged'
        process_folder(self.project, destination, PipelineConfig(stage_order=()))
        for path in self.project.glob('*.py'):
            self.assertEqual(path.read_text(encoding='utf-8'),
                             (destination / path.name).read_text(encoding='utf-8'))

    def test_repeated_subset_runs_in_selected_order_across_layers(self):
        records = []
        generated, _ = transform_layers('print(42)\n', PipelineConfig(
            seed=2, stage_order=('numeric', 'junk', 'numeric'), layers=2, junk_count=1),
            stage_records=records)
        self.assertEqual([record['stage'] for record in records], ['numeric', 'junk', 'numeric'] * 2)
        output = self.root / 'repeated.py'
        output.write_text(generated, encoding='utf-8')
        self.run_program(output)

    def test_hex_only_cli_file_and_folder_skip_every_source_stage(self):
        script = self.root / 'script.py'
        script.write_text('print(42)\n', encoding='utf-8')
        for folder in (False, True):
            with self.subTest(folder=folder):
                report = self.root / f'report-{folder}.json'
                output = self.root / f'packed-{folder}.py'
                args = [str(self.project if folder else script), '--hex-only',
                        '--layers', '3', '-o', str(output), '--report', str(report)]
                if folder:
                    args += ['--folder', '--verify-entry', 'main.py']
                else:
                    args += ['--verify']
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(main(args), 0)
                data = json.loads(report.read_text(encoding='utf-8'))
                self.assertEqual([record['stage'] for record in data['stages']], ['hexadecimal'])
                expected = (bundle_project(self.project, None, SKIPPED_DIRECTORIES)
                            if folder else bundle_file(script))
                self.assertEqual(output.read_text(encoding='utf-8'), expected.source)
                self.run_program(output)

    def test_optional_selectors_and_all_strength_with_custom_sequence(self):
        with patch('code_editors.cli.process_file', return_value=['Saved']) as process:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(['app.py', '--all', '--stage-order', 'hexadecimal,xor']), 0)
        config = process.call_args.args[2]
        self.assertTrue(config.hex_pack)
        self.assertEqual(config.stage_order, ('xor',))
        self.assertEqual(config.literal_mode, 'polymorphic')
        self.assertEqual(config.layers, 2)
        self.assertTrue(PipelineConfig(stage_order=('rename_modules',)).rename_modules)
        config = PipelineConfig(stage_order=('native',), native_modules=('worker.py',))
        self.assertEqual(config.stage_order, ())
        self.assertEqual(config.native_modules, ('worker.py',))
        with patch('code_editors.cli.process_file', return_value=['Saved']) as process:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(['app.py', '--hex-pack', '--stage-order', 'none']), 0)
        self.assertEqual(process.call_args.args[2].stage_order, ())
        with patch('code_editors.cli.process_file', return_value=['Saved']) as process:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(['app.py', '--all', '--stage-order', 'xor,name_mangler']), 0)
        config = process.call_args.args[2]
        self.assertFalse(config.hex_pack)
        self.assertEqual(config.stage_order, ('xor', 'name_mangler'))
        self.assertEqual(config.literal_mode, 'polymorphic')

    def test_only_relocation_and_hex_selected_by_id_execute(self):
        destination = self.root / 'relocated'
        process_folder(self.project, destination,
                       PipelineConfig(seed=5, stage_order=('rename_modules',)))
        self.run_program(destination / 'main.py')
        output = self.root / 'hex-selected.py'
        process_file(self.project / 'worker.py', output,
                     PipelineConfig(stage_order=('hexadecimal',)))
        self.assertEqual(output.read_text(encoding='utf-8'), bundle_file(self.project / 'worker.py').source)

    def test_hex_plus_selected_source_stage_repeats_only_source_stage(self):
        records = []
        source, _ = transform_layers('print(42)\n', PipelineConfig(
            seed=7, stage_order=('hexadecimal', 'xor'), layers=2), stage_records=records)
        self.assertEqual([record['stage'] for record in records], ['hexadecimal', 'xor', 'xor'])
        output = self.root / 'hex-xor.py'
        output.write_text(source, encoding='utf-8')
        self.run_program(output)

    def test_invalid_selections_fail_clearly(self):
        with self.assertRaisesRegex(ValueError, 'require process_folder'):
            transform_layers('print(42)', PipelineConfig(stage_order=('rename_modules',)))
        for order in [('bad',), ('xor', 'hexadecimal'), ('hexadecimal', 'hexadecimal'),
                      ('rename_modules', 'xor'), ('rename_modules', 'rename_modules'), ('native',),
                      ('hexadecimal', 'rename_modules')]:
            with self.subTest(order=order), self.assertRaises(ValueError):
                PipelineConfig(stage_order=order)
        for options in [('--hex-only', '--all'), ('--hex-only', '--stage-order', 'xor'),
                        ('--hex-only', '--rename-modules'), ('--stage-order', 'rename_modules')]:
            with self.subTest(options=options), contextlib.redirect_stderr(io.StringIO()):
                with patch('code_editors.cli._choose_file') as picker:
                    self.assertEqual(main(list(options)), 1)
                    picker.assert_not_called()

    def test_all_incompatible_optional_operations_fail_before_picking_input(self):
        for options in [('--rename-modules',), ('--native-module', 'worker.py')]:
            with self.subTest(options=options), contextlib.redirect_stderr(io.StringIO()) as error:
                with patch('code_editors.cli._choose_folder') as picker:
                    self.assertEqual(main(['--folder', '--all', *options]), 1)
                    picker.assert_not_called()
                self.assertIn('--stage-order', error.getvalue())

    def test_all_folder_build_is_one_executable_file_with_verification(self):
        output = self.root / 'all-packed.py'
        report = self.root / 'all-report.json'
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main([str(self.project), '--folder', '--all', '-o', str(output),
                '--verify-entry', 'main.py', '--report', str(report), '--seed', '7']), 0)
        self.assertTrue(output.is_file())
        self.run_program(output)
        data = json.loads(report.read_text(encoding='utf-8'))
        self.assertTrue(data['configuration']['hex_pack'])
        self.assertFalse(data['configuration']['allow_introspection'])
        self.assertEqual([record['stage'] for record in data['stages']].count('hexadecimal'), 1)
        self.assertEqual(len(data['stages']), 25)
