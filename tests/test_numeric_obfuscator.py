from __future__ import annotations

import ast
import unittest

from code_editors.stages.numeric_obfuscator import obfuscate_numeric_constants


class NumericObfuscatorTests(unittest.TestCase):
    def test_preserves_ast_and_values(self) -> None:
        source = '''\
from __future__ import annotations

def calculate(value: int) -> int:
    match value:
        case 10:
            return 1000
        case _:
            return 2000

answer = calculate(10)
'''
        transformed, count = obfuscate_numeric_constants(source, seed=11)
        self.assertGreater(count, 0)
        self.assertEqual(
            ast.dump(ast.parse(source), include_attributes=False),
            ast.dump(ast.parse(transformed), include_attributes=False),
        )

        original_namespace: dict[str, object] = {}
        transformed_namespace: dict[str, object] = {}
        exec(compile(source, "<numeric-original>", "exec"), original_namespace)
        exec(compile(transformed, "<numeric-transformed>", "exec"), transformed_namespace)
        self.assertEqual(original_namespace["answer"], transformed_namespace["answer"])
        self.assertEqual(
            original_namespace["calculate"].__annotations__,  # type: ignore[attr-defined]
            transformed_namespace["calculate"].__annotations__,  # type: ignore[attr-defined]
        )


if __name__ == "__main__":
    unittest.main()
