"""Semantic checks for optional layered literal encoding."""

import contextlib
import io
import unittest

from code_editors import PipelineConfig, transform_layers
from code_editors.stages.xor_encryption import build_decryptor, transform_source


class LayeredLiteralTests(unittest.TestCase):
    def test_round_trip_binary_unicode_empty_and_repeated_calls(self):
        source = (
            "def values():\n"
            "    return '', b'', 'Zażółć 🐍', " + repr(bytes(range(256))) + ", b'\\x00\\xff'\n"
            "result = [values() for _ in range(3)]\n"
        )
        expected = {}
        exec(source, expected)
        for seed in range(10):
            key = bytes((seed + index) % 256 for index in range(16))
            encoded, _ = transform_source(source, key, mode="layered")
            namespace = {}
            exec("\n".join(build_decryptor(key, mode="layered")) + "\n" + encoded, namespace)
            self.assertEqual(namespace["result"], expected["result"])

    def test_full_pipeline_multiple_layers_preserves_output(self):
        source = "for x in range(3):\n    print('hello 🐍', b'\\x00\\xff', x)\n"
        encoded, _ = transform_layers(
            source, PipelineConfig(seed=21, layers=2, junk_count=1, literal_mode="layered")
        )
        outputs = []
        for program in (source, encoded):
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                exec(program, {})
            outputs.append(stream.getvalue())
        self.assertEqual(*outputs)
