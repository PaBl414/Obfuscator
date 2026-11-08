"""Backward-compatible public API for the ObfuscatorEdu pipeline.

New integrations may construct :class:`PipelineConfig` and call functions from
``code_editors.core``. These wrappers preserve the original argument-based API.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from .cli import build_parser, main
from .core.configuration import DEFAULT_STAGE_ORDER, PipelineConfig, validate_stage_order
from .core.context import ProgressCallback, SeedAllocator
from .core.engine import transform as _transform
from .core.engine import transform_layers as _transform_layers
from .core.filesystem import (
    SKIPPED_DIRECTORIES,
    process_file as _process_file,
    process_folder as _process_folder,
)
from .core.registry import STAGE_REGISTRY, mangle_source

STAGE_ORDER = DEFAULT_STAGE_ORDER
STAGE_HANDLERS = STAGE_REGISTRY
_SeedAllocator = SeedAllocator
_mangle_source = mangle_source


def _config(
    seed: int | None,
    layers: int,
    junk_count: int,
    name_length: int,
    import_mode: str,
    stage_order: Sequence[str],
) -> PipelineConfig:
    return PipelineConfig(
        seed=seed,
        layers=layers,
        junk_count=junk_count,
        name_length=name_length,
        import_mode=import_mode,  # type: ignore[arg-type]
        stage_order=tuple(stage_order),
    )


def transform(
    source: str,
    seed: int | None = None,
    junk_count: int = 8,
    name_length: int = 5,
    protected_names: set[str] | None = None,
    stage_order: Sequence[str] = STAGE_ORDER,
    progress: ProgressCallback | None = None,
    layer_number: int = 1,
    layer_count: int = 1,
    progress_label: str | None = None,
    _seed_allocator: SeedAllocator | None = None,
    import_mode: str = "aliases",
    attribute_mapping: dict[str, str] | None = None,
) -> tuple[str, list[str]]:
    """Apply one stage sequence using the legacy argument-based API."""
    config = _config(seed, 1, junk_count, name_length, import_mode, stage_order)
    return _transform(
        source,
        config,
        protected_names=protected_names,
        attribute_mapping=attribute_mapping,
        progress=progress,
        progress_label=progress_label,
        layer_number=layer_number,
        layer_count=layer_count,
        seed_allocator=_seed_allocator,
    )


def transform_layers(
    source: str,
    seed: int | None = None,
    layers: int = 1,
    junk_count: int = 8,
    name_length: int = 5,
    protected_names: set[str] | None = None,
    stage_order: Sequence[str] = STAGE_ORDER,
    progress: ProgressCallback | None = None,
    progress_label: str | None = None,
    _seed_allocator: SeedAllocator | None = None,
    import_mode: str = "aliases",
    attribute_mapping: dict[str, str] | None = None,
) -> tuple[str, list[str]]:
    """Apply one or more layers using the legacy argument-based API."""
    config = _config(seed, layers, junk_count, name_length, import_mode, stage_order)
    return _transform_layers(
        source,
        config,
        protected_names=protected_names,
        attribute_mapping=attribute_mapping,
        progress=progress,
        progress_label=progress_label,
        seed_allocator=_seed_allocator,
    )


def process_file(
    source_path: Path,
    output_path: Path | None = None,
    seed: int | None = None,
    junk_count: int = 8,
    name_length: int = 5,
    layers: int = 1,
    stage_order: Sequence[str] = STAGE_ORDER,
    progress: ProgressCallback | None = None,
    import_mode: str = "aliases",
) -> list[str]:
    """Transform one file using the legacy argument-based API."""
    config = _config(seed, layers, junk_count, name_length, import_mode, stage_order)
    return _process_file(source_path, output_path, config, progress=progress)


def process_folder(
    source_folder: Path,
    output_folder: Path | None = None,
    seed: int | None = None,
    junk_count: int = 8,
    name_length: int = 5,
    layers: int = 1,
    stage_order: Sequence[str] = STAGE_ORDER,
    progress: ProgressCallback | None = None,
    import_mode: str = "aliases",
) -> list[str]:
    """Transform a project tree using the legacy argument-based API."""
    config = _config(seed, layers, junk_count, name_length, import_mode, stage_order)
    return _process_folder(source_folder, output_folder, config, progress=progress)


__all__ = [
    "PipelineConfig",
    "SKIPPED_DIRECTORIES",
    "STAGE_HANDLERS",
    "STAGE_ORDER",
    "build_parser",
    "main",
    "process_file",
    "process_folder",
    "transform",
    "transform_layers",
    "validate_stage_order",
]


if __name__ == "__main__":
    raise SystemExit(main())
