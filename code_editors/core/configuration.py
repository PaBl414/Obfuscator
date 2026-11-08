"""Validated configuration for source transformations."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
import math
from typing import Literal

StageId = Literal[
    "junk",
    "imports",
    "xor",
    "function_flow",
    "module_flow",
    "flattener",
    "name_mangler",
    "cleanup",
    "numeric",
]
ImportMode = Literal["aliases", "runtime"]

REGISTERED_STAGE_IDS: tuple[StageId, ...] = (
    "junk",
    "imports",
    "xor",
    "function_flow",
    "module_flow",
    "flattener",
    "name_mangler",
    "cleanup",
    "numeric",
)

DEFAULT_STAGE_ORDER: tuple[StageId, ...] = REGISTERED_STAGE_IDS

ALL_STAGE_ORDER: tuple[str, ...] = tuple(
    "imports,junk,xor,name_mangler,function_flow,module_flow,"
    "flattener,junk,xor,name_mangler,numeric,cleanup".split(",")
)


def validate_stage_order(stage_order: Sequence[str]) -> None:
    """Validate that an order contains every stage and no unknown IDs."""
    known = set(REGISTERED_STAGE_IDS)
    provided = set(stage_order)
    unknown = sorted(provided - known)
    missing = sorted(known - provided)
    if unknown or missing:
        raise ValueError(
            "Stage order must contain every registered stage at least once; "
            f"missing={missing}, unknown={unknown}."
        )


@dataclass(frozen=True, slots=True)
class PipelineConfig:
    """User-facing settings shared by file and folder transformations."""

    seed: int | None = None
    layers: int = 1
    junk_count: int = 8
    name_length: int = 5
    import_mode: ImportMode = "aliases"
    literal_mode: str = "xor"
    dispatch_mode: str = "branches"
    allow_introspection: bool = False
    stage_order: tuple[str, ...] = DEFAULT_STAGE_ORDER
    extended_flow: bool = False
    rename_modules: bool = False
    report_path: Path | None = None
    verify_entry: str | None = None
    verify_stdin: str = ""
    verify_timeout: float = 30.0
    native_modules: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.layers < 1:
            raise ValueError("layers must be at least 1")
        if self.junk_count < 1:
            raise ValueError("junk_count must be at least 1")
        if self.name_length not in range(4, 7):
            raise ValueError("name_length must be 4, 5, or 6")
        if self.import_mode not in ("aliases", "runtime"):
            raise ValueError("import_mode must be 'aliases' or 'runtime'")
        if self.literal_mode not in ("xor", "layered", "chained", "polymorphic"):
            raise ValueError("Unknown literal_mode")
        if not math.isfinite(self.verify_timeout) or self.verify_timeout <= 0:
            raise ValueError("verify_timeout must be finite and positive")
        if self.rename_modules and self.native_modules:
            raise ValueError("Native modules and module relocation must be built separately")
        if self.dispatch_mode not in ("branches", "table"):
            raise ValueError("dispatch_mode must be 'branches' or 'table'")
        validate_stage_order(self.stage_order)
