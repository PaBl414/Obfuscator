"""Execution engine for ordered, repeatable source transformations."""

from __future__ import annotations

import ast
import time
from collections import Counter
from dataclasses import replace

from .configuration import PipelineConfig, resolve_seed
from .context import ProgressCallback, SeedAllocator, StageContext
from .registry import STAGE_REGISTRY
from ..stages.name_mangler import ModuleDefinitions
from .hex_bundle import bundle_text


def _hex_prepass(source, config, progress, records):
    if not config.hex_pack:
        return source, config, []
    if config.hex_entry is not None or config.hex_entry_mode != 'auto':
        raise ValueError("Hex entrypoint selection is only available for folder input")
    if progress:
        progress("Preprocess: hexadecimal — started")
    started = time.perf_counter()
    bundle = bundle_text(source, cwd=config.hex_cwd)
    description = "Hexadecimal packing: created one launcher before pipeline stages"
    if records is not None:
        records.append({"stage": "hexadecimal", "phase": "preprocess", "seed": None,
            "seconds": time.perf_counter() - started, "input_bytes": len(source.encode('utf-8')),
            "output_bytes": len(bundle.source.encode('utf-8')), "description": description})
    if progress:
        progress(f"Preprocess: hexadecimal — completed: {description}")
    return bundle.source, replace(config, hex_pack=False, hex_entry=None, hex_cwd="caller", hex_entry_mode='auto'), [description]


def reflective_members(source: str) -> set[str]:
    tree = ast.parse(source)
    definitions = ModuleDefinitions()
    definitions.visit(tree)
    return definitions.attribute_names & {
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }


def transform(
    source: str,
    config: PipelineConfig,
    *,
    protected_names: set[str] | None = None,
    attribute_mapping: dict[str, str] | None = None,
    shared_name_mapping: dict[str, str] | None = None,
    progress: ProgressCallback | None = None,
    progress_label: str | None = None,
    layer_number: int = 1,
    layer_count: int = 1,
    seed_allocator: SeedAllocator | None = None,
    stage_records: list[dict] | None = None,
) -> tuple[str, list[str]]:
    """Apply one configured stage sequence to Python source."""
    if config.rename_modules or config.native_modules:
        raise ValueError("Module relocation and native compilation require process_folder")
    config = resolve_seed(config)
    source, config, packing_report = _hex_prepass(source, config, progress, stage_records)
    allocator = seed_allocator or SeedAllocator(config.seed)
    context = StageContext(
        stage_seed=0,
        junk_count=config.junk_count,
        name_length=config.name_length,
        protected_names=(protected_names or set()) | reflective_members(source) | set(config.keep_names),
        attribute_mapping=attribute_mapping,
        shared_name_mapping=shared_name_mapping,
        import_mode=config.import_mode,
        literal_mode=config.literal_mode,
        dispatch_mode=config.dispatch_mode,
        allow_introspection=config.allow_introspection,
        extended_flow=config.extended_flow,
    )
    report: list[str] = list(packing_report)
    totals = Counter(config.stage_order)
    seen: Counter[str] = Counter()

    for index, stage_id in enumerate(config.stage_order, start=1):
        seen[stage_id] += 1
        stage_seed = allocator.next_seed()
        context.stage_seed = stage_seed
        context.current_stage_occurrence = seen[stage_id]
        prefix = f"{progress_label} | " if progress_label else ""
        prefix += (
            f"Layer {layer_number}/{layer_count} | "
            f"Stage {index}/{len(config.stage_order)}: {stage_id} "
            f"({seen[stage_id]}/{totals[stage_id]}) seed={stage_seed}"
        )
        if progress is not None:
            progress(f"{prefix} — started")
        started = time.perf_counter()
        input_bytes = len(source.encode('utf-8'))
        try:
            source, description = STAGE_REGISTRY[stage_id](source, context)
        except Exception as exc:
            if stage_records is not None:
                stage_records.append({"file": progress_label, "layer": layer_number,
                    "stage": stage_id, "seed": stage_seed, "status": "failed",
                    "seconds": time.perf_counter() - started,
                    "error": f"{type(exc).__name__}: {exc}"})
            if progress is not None:
                progress(f"{prefix} — failed: {type(exc).__name__}: {exc}")
            raise
        stage_seconds = time.perf_counter() - started
        report.append(f"{index}. {description} [seed={stage_seed}]")
        if stage_records is not None:
            stage_records.append({"file": progress_label, "layer": layer_number,
                "stage": stage_id, "seed": stage_seed, "description": description,
                "seconds": stage_seconds, "input_bytes": input_bytes,
                "output_bytes": len(source.encode('utf-8'))})
        if progress is not None:
            suffix = (f" [{stage_seconds:.3f}s; {input_bytes} -> "
                      f"{len(source.encode('utf-8'))} bytes]") if config.metrics else ""
            progress(f"{prefix} — completed: {description}{suffix}")
    return source, report


def transform_layers(
    source: str,
    config: PipelineConfig,
    *,
    protected_names: set[str] | None = None,
    attribute_mapping: dict[str, str] | None = None,
    shared_name_mapping: dict[str, str] | None = None,
    progress: ProgressCallback | None = None,
    progress_label: str | None = None,
    seed_allocator: SeedAllocator | None = None,
    stage_records: list[dict] | None = None,
) -> tuple[str, list[str]]:
    """Apply the complete sequence repeatedly, feeding each result forward."""
    if config.rename_modules or config.native_modules:
        raise ValueError("Module relocation and native compilation require process_folder")
    config = resolve_seed(config)
    source, config, packing_report = _hex_prepass(source, config, progress, stage_records)
    allocator = seed_allocator or SeedAllocator(config.seed)
    report: list[str] = list(packing_report)
    protected_names = (protected_names or set()) | reflective_members(source)
    for layer_number in range(1, config.layers + 1):
        source, stage_report = transform(
            source,
            config,
            protected_names=protected_names,
            attribute_mapping=attribute_mapping,
            shared_name_mapping=shared_name_mapping,
            progress=progress,
            progress_label=progress_label,
            layer_number=layer_number,
            layer_count=config.layers,
            seed_allocator=allocator,
            stage_records=stage_records,
        )
        report.append(f"Layer {layer_number}/{config.layers}")
        report.extend(f"  {line}" for line in stage_report)
    return source, report
