from __future__ import annotations

import unittest

from code_editors.stages.function_flow import flatten_simple_functions


def _execute(source: str, function_name: str, *args: object) -> object:
    namespace: dict[str, object] = {}
    exec(compile(source, "<function-flow-test>", "exec"), namespace)
    function = namespace[function_name]
    return function(*args)  # type: ignore[operator]


class FunctionFlowTests(unittest.TestCase):
    def test_flattens_loop_try_and_loop_control(self) -> None:
        source = '''\
def calculate(limit):
    total = 0
    try:
        for value in range(limit):
            if value == 2:
                continue
            if value == 7:
                break
            total += value
    except ValueError:
        return -1
    return total
'''
        transformed, count = flatten_simple_functions(source, seed=21)
        self.assertEqual(count, 1)
        for limit in range(12):
            self.assertEqual(
                _execute(source, "calculate", limit),
                _execute(transformed, "calculate", limit),
            )

    def test_flattens_match_statement(self) -> None:
        source = '''\
def classify(value):
    result = 0
    match value:
        case 1:
            result = 10
        case _:
            result = 20
    return result
'''
        transformed, count = flatten_simple_functions(source, seed=4)
        self.assertEqual(count, 1)
        for value in (1, 2, "other"):
            self.assertEqual(
                _execute(source, "classify", value),
                _execute(transformed, "classify", value),
            )

    def test_flattens_nested_if_branches_with_same_side_effect_order(self) -> None:
        source = '''\
def classify(value, events):
    result = 0
    if value < 0:
        events.append("negative")
        if value < -5:
            result = 1
        else:
            result = 2
    elif value == 0:
        events.append("zero")
        result = 3
    else:
        events.append("positive")
        result = 4
    return result
'''
        transformed, count = flatten_simple_functions(source, seed=31)
        self.assertEqual(count, 1)
        for value in (-10, -3, 0, 9):
            original_events: list[str] = []
            transformed_events: list[str] = []
            original_result = _execute(source, "classify", value, original_events)
            transformed_result = _execute(
                transformed, "classify", value, transformed_events
            )
            self.assertEqual(original_result, transformed_result)
            self.assertEqual(original_events, transformed_events)

    def test_flattens_with_statement_and_nested_function(self) -> None:
        source = '''\
from contextlib import nullcontext

def collect(value):
    total = 0
    with nullcontext(value) as received:
        total += received
    def doubled(item):
        temporary = item * 2
        return temporary
    total += doubled(5)
    return total
'''
        transformed, count = flatten_simple_functions(source, seed=9)
        self.assertEqual(count, 2)
        self.assertEqual(
            _execute(source, "collect", 7),
            _execute(transformed, "collect", 7),
        )

    def test_skips_decorated_generators_and_introspection(self) -> None:
        source = '''\
@staticmethod
def decorated(value):
    first = value
    return first

def generated():
    first = 1
    yield first

def reflective():
    first = 1
    return locals()
'''
        transformed, count = flatten_simple_functions(source, seed=1)
        self.assertEqual(count, 0)
        self.assertEqual(transformed, source)


if __name__ == "__main__":
    unittest.main()
