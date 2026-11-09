"""Validated configuration for source transformations."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
import math
import secrets
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


@dataclass(frozen=True, slots=True)
class VerificationCase:
    name: str = "default"
    stdin: str = ""
    args: tuple[str, ...] = ()
    expected_exit: int = 0

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
    verify_cases: tuple[VerificationCase, ...] = ()
    verify_files: tuple[str, ...] = ()
    verify_commands: tuple[tuple[str, ...], ...] = ()
    benchmark_runs: int = 1
    metrics: bool = False
    keep_names: tuple[str, ...] = ()

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
        if isinstance(self.benchmark_runs, bool) or not isinstance(self.benchmark_runs, int) or self.benchmark_runs < 1:
            raise ValueError("benchmark_runs must be a positive integer")
        if not all(isinstance(name, str) and name.isidentifier() for name in self.keep_names):
            raise ValueError("keep_names must contain Python identifiers")
        for case in self.verify_cases:
            if (not isinstance(case, VerificationCase) or not isinstance(case.name, str)
                    or not isinstance(case.stdin, str) or not all(isinstance(arg, str) for arg in case.args)
                    or type(case.expected_exit) is not int):
                raise ValueError("Invalid verification case")
        for value in self.verify_files:
            path = Path(value)
            if not value or path.is_absolute() or '..' in path.parts:
                raise ValueError("Verification files must be relative paths without '..'")
        if any(not command or not all(isinstance(arg, str) for arg in command)
               for command in self.verify_commands):
            raise ValueError("Verification commands must be nonempty Python argument lists")
        if (self.verify_cases or self.verify_files or self.benchmark_runs > 1) and not self.verify_entry:
            raise ValueError("Cases, output file checks and benchmark runs require a verification entrypoint")
        if self.rename_modules and self.native_modules:
            raise ValueError("Native modules and module relocation must be built separately")
        if self.dispatch_mode not in ("branches", "table"):
            raise ValueError("dispatch_mode must be 'branches' or 'table'")
        validate_stage_order(self.stage_order)


def resolve_seed(config: PipelineConfig) -> PipelineConfig:
    """Persist one effective seed for reproducible analysis and transformation."""
    return replace(config, seed=secrets.randbits(64)) if config.seed is None else config
