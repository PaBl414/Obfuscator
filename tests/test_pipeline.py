from __future__ import annotations

import contextlib
import io
import unittest

from code_editors.pipeline import transform


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


if __name__ == "__main__":
    unittest.main()
