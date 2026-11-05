from __future__ import annotations

import ast
import unittest

from code_editors.stages import xor_encryption


class XorEncryptionTests(unittest.TestCase):
    def test_distinct_keys_round_trip_text_and_bytes(self) -> None:
        source = "first = 'same'; second = 'same'; payload = b'\\x00\\xff'\n"
        master_key = bytes(range(16))

        transformed, tree = xor_encryption.transform_source(source, master_key)
        calls = [
            node for node in ast.walk(ast.parse(transformed))
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"_d", "_db"}
        ]
        embedded_keys = [call.args[1].value for call in calls]

        self.assertEqual(len(calls), 3)
        self.assertEqual(len(set(embedded_keys)), 3)
        self.assertNotIn(master_key.hex(), transformed)

        lines = transformed.split("\n")
        insert_at = xor_encryption.find_insert_line(tree, source)
        executable = "\n".join(
            lines[:insert_at]
            + xor_encryption.build_decryptor(master_key)
            + lines[insert_at:]
        )
        namespace: dict[str, object] = {}
        exec(compile(executable, "<xor-test>", "exec"), namespace)
        self.assertEqual(namespace["first"], "same")
        self.assertEqual(namespace["second"], "same")
        self.assertEqual(namespace["payload"], b"\x00\xff")

    def test_seed_key_produces_reproducible_per_literal_keys(self) -> None:
        source = "value = 'repeat'\n"
        master_key = b"reproducible-key"
        first, _ = xor_encryption.transform_source(source, master_key)
        second, _ = xor_encryption.transform_source(source, master_key)
        self.assertEqual(first, second)

    def test_rejects_empty_master_key(self) -> None:
        with self.assertRaises(ValueError):
            xor_encryption.transform_source("value = 'text'\n", b"")


if __name__ == "__main__":
    unittest.main()
