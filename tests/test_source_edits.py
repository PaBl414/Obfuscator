"""Source indexing must follow parser byte columns and physical newlines."""
import ast
import unittest

from code_editors.core.registry import preserve_integer_spellings
from code_editors.stages.numeric_obfuscator import obfuscate_numeric_constants
from code_editors.stages.source_edits import SourceIndex, apply_edits


class SourceEditTests(unittest.TestCase):
    def test_unicode_columns_and_line_separators(self):
        for newline in ('\n', '\r\n', '\r'):
            source = "zażółć = 'a\u2028b'; wynik = 123" + newline + "\fnext_value = 17" + newline
            tree = ast.parse(source)
            index = SourceIndex(source)
            edits = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, int):
                    self.assertEqual(index.segment(node), str(node.value))
                    edits.append((index.offset(node.lineno, node.col_offset),
                                  index.offset(node.end_lineno, node.end_col_offset), hex(node.value)))
            rewritten = apply_edits(source, edits)
            self.assertEqual(ast.dump(ast.parse(rewritten)), ast.dump(tree))
            rewritten, _ = obfuscate_numeric_constants(source, seed=4)
            self.assertEqual(ast.dump(ast.parse(rewritten)), ast.dump(tree))
            restored = preserve_integer_spellings(rewritten, ast.unparse(tree))
            self.assertEqual(ast.dump(ast.parse(restored)), ast.dump(tree))

    def test_rejects_overlapping_replacements(self):
        with self.assertRaisesRegex(ValueError, 'overlap'):
            apply_edits('abcdef', [(1, 4, 'x'), (2, 3, 'y')])


if __name__ == '__main__':
    unittest.main()
