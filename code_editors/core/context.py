"""Internal state passed between pipeline stages."""

from __future__ import annotations

import secrets
from collections.abc import Callable
from dataclasses import dataclass

ProgressCallback = Callable[[str], None]


@dataclass(slots=True)
class StageContext:
    """Mutable state for one pass through the configured stage sequence."""

    stage_seed: int
    junk_count: int
    name_length: int
    protected_names: set[str] | None
    attribute_mapping: dict[str, str] | None = None
    shared_name_mapping: dict[str, str] | None = None
    project_modules: set[str] | None = None
    project_keyword_names: set[str] | None = None
    project_callable_names: set[str] | None = None
    import_mode: str = "aliases"
    literal_mode: str = "xor"
    dispatch_mode: str = "branches"
    allow_introspection: bool = False
    extended_flow: bool = False
    module_flow_succeeded: bool = False
    current_stage_occurrence: int = 1


class SeedAllocator:
    """Allocate unique deterministic or cryptographically random stage seeds."""

    def __init__(self, seed: int | None) -> None:
        self._next_seed = seed
        self._used: set[int] = set()

    def next_seed(self) -> int:
        if self._next_seed is not None:
            result = self._next_seed
            self._next_seed += 1
            return result

        while True:
            result = secrets.randbits(64)
            if result not in self._used:
                self._used.add(result)
                return result
