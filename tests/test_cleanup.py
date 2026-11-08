from __future__ import annotations

import unittest

from code_editors.stages.cleanup import process


class CleanupTests(unittest.TestCase):
    def test_minifier_preserves_escaped_fstring_braces(self) -> None:
        source = '''\
color = "#abc"
style = f"QWidget {{ color: {color}; width: 10px; }}"
'''
        cleaned = process(source)
        original_values: dict[str, object] = {}
        cleaned_values: dict[str, object] = {}
        exec(compile(source, "<original-style>", "exec"), original_values)
        exec(compile(cleaned, "<cleaned-style>", "exec"), cleaned_values)

        self.assertEqual(cleaned_values["style"], original_values["style"])
        self.assertIn("QWidget { color: #abc; width: 10px; }", cleaned_values["style"])


if __name__ == "__main__":
    unittest.main()
