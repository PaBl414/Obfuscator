from __future__ import annotations

import unittest

from code_editors import PipelineConfig, transform
from code_editors.core.configuration import REGISTERED_STAGE_IDS
from code_editors.core.registry import STAGE_REGISTRY


class PipelineConfigTests(unittest.TestCase):
    def test_registry_and_configuration_have_the_same_stage_ids(self) -> None:
        self.assertEqual(set(REGISTERED_STAGE_IDS), set(STAGE_REGISTRY))

    def test_rejects_invalid_settings_before_transformation(self) -> None:
        invalid_values = (
            {"layers": 0},
            {"junk_count": 0},
            {"name_length": 3},
            {"import_mode": "unknown"},
            {"stage_order": ("unknown",)},
        )
        for values in invalid_values:
            with self.subTest(values=values), self.assertRaises(ValueError):
                PipelineConfig(**values)

    def test_public_configuration_api_is_deterministic(self) -> None:
        source = 'message = "hello"\nanswer = 40 + 2\n'
        config = PipelineConfig(seed=17, junk_count=1)

        first, first_report = transform(source, config)
        second, second_report = transform(source, config)

        self.assertEqual(first, second)
        self.assertEqual(first_report, second_report)
        namespace: dict[str, object] = {}
        exec(compile(first, "<core-api>", "exec"), namespace)
        self.assertIn(42, namespace.values())


if __name__ == "__main__":
    unittest.main()
