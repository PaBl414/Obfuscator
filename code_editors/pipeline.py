"""Orchestrate the project's source transformation stages."""

from __future__ import annotations

import argparse
import ast
import io
import random
import secrets
import shutil
import sys
import tokenize
from collections import Counter, defaultdict, deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from .stages import cleanup, flattener, flow_obfuscator, function_flow
from .stages import import_obfuscator, junk_editor, name_mangler, numeric_obfuscator
from .stages import xor_encryption

SKIPPED_DIRECTORIES = {
    ".git", ".mypy_cache", ".pytest_cache", ".venv", "__pycache__",
    "build", "dist", "node_modules", "venv",
}

# Reorder these stage IDs to change the default pipeline sequence.
STAGE_ORDER = (
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

ProgressCallback = Callable[[str], None]


def _mangle_source(
    source: str, length: int, rng: random.Random,
    protected_names: set[str] | None = None,
    attribute_mapping: dict[str, str] | None = None,
) -> tuple[str, int]:
    tree = ast.parse(source)
    visitor = name_mangler.ModuleDefinitions()
    visitor.visit(tree)
    candidates = {
        name for name in visitor.names
        if name not in visitor.protected
        and name not in (protected_names or set())
        and name not in name_mangler.BUILTIN_NAMES
        and not (name.startswith("__") and name.endswith("__"))
        and name.isidentifier()
    }
    mapping = name_mangler.make_mapping(
        candidates, length, rng,
        reserved=set(attribute_mapping.values()) if attribute_mapping else None,
    )
    if attribute_mapping is None:
        attributes = {
            name for name in visitor.attribute_names
            if name not in name_mangler.BUILTIN_NAMES
            and not (name.startswith("__") and name.endswith("__"))
        }
        attribute_mapping = name_mangler.make_mapping(
            attributes, length, rng, reserved=set(mapping.values())
        )
    rewritten = name_mangler.apply_edits(
        source, name_mangler.python_edits(source, mapping, attribute_mapping)
    )
    ast.parse(rewritten)
    return rewritten, len(mapping) + len(attribute_mapping)


def _source_offset(source: str, line_number: int, byte_column: int) -> int:
    lines = source.splitlines(keepends=True)
    line_start = sum(len(line) for line in lines[: line_number - 1])
    line = lines[line_number - 1]
    character_column = len(
        line.encode("utf-8")[:byte_column].decode("utf-8", errors="ignore")
    )
    return line_start + character_column


def _preserve_integer_spellings(original: str, rewritten: str) -> str:
    """Carry equivalent integer token spellings through AST-unparse stages."""
    original_tree = ast.parse(original, type_comments=True)
    spellings: dict[int, deque[str]] = defaultdict(deque)
    for node in ast.walk(original_tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, int)
            and not isinstance(node.value, bool)
        ):
            spelling = ast.get_source_segment(original, node)
            if spelling is not None:
                spellings[node.value].append(spelling)

    if not spellings:
        return rewritten

    tree = ast.parse(rewritten, type_comments=True)
    edits: list[tuple[int, int, str]] = []
    last_spelling: dict[int, str] = {}
    for node in ast.walk(tree):
        if (
            not isinstance(node, ast.Constant)
            or not isinstance(node.value, int)
            or isinstance(node.value, bool)
        ):
            continue
        choices = spellings.get(node.value)
        if choices:
            spelling = choices.popleft()
            last_spelling[node.value] = spelling
        else:
            spelling = last_spelling.get(node.value)
        if spelling is None:
            continue
        start = _source_offset(rewritten, node.lineno, node.col_offset)
        end = _source_offset(rewritten, node.end_lineno, node.end_col_offset)
        edits.append((start, end, spelling))

    restored = rewritten
    for start, end, spelling in sorted(edits, reverse=True):
        restored = restored[:start] + spelling + restored[end:]
    if ast.dump(tree, include_attributes=False) != ast.dump(
        ast.parse(restored, type_comments=True), include_attributes=False
    ):
        raise ValueError("Preserving integer spellings changed the parsed syntax tree.")
    return restored


@dataclass
class _StageContext:
    stage_seed: int
    junk_count: int
    name_length: int
    protected_names: set[str] | None
    attribute_mapping: dict[str, str] | None = None
    import_mode: str = "aliases"
    module_flow_succeeded: bool = False
    current_stage_occurrence: int = 1


StageHandler = Callable[[str, _StageContext], tuple[str, str]]


class _SeedAllocator:
    """Allocate a unique deterministic or random seed for each stage run."""

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


def _stage_junk(source: str, context: _StageContext) -> tuple[str, str]:
    source, count = junk_editor.inject_junk(
        source, count=context.junk_count, seed=context.stage_seed
    )
    return source, f"Junk editor: inserted {count} blocks"


def _stage_imports(source: str, context: _StageContext) -> tuple[str, str]:
    if context.import_mode == "runtime":
        source, count, skipped = import_obfuscator.obfuscate_imports_runtime(
            source, seed=context.stage_seed
        )
        return (
            source,
            f"Import obfuscation (runtime): hid {count} imports; left {skipped} unchanged",
        )
    source, count = import_obfuscator.obfuscate_imports(source)
    return source, f"Import obfuscation: added explicit aliases to {count} imports"


def _stage_xor(source: str, context: _StageContext) -> tuple[str, str]:
    key_rng = random.Random(context.stage_seed)
    key = bytes(key_rng.randrange(256) for _ in range(16))
    decoder_names = _xor_decoder_names(source, context.current_stage_occurrence)
    encrypted, tree = xor_encryption.transform_source(
        source, key, decoder_names=decoder_names
    )
    insert_at = xor_encryption.find_insert_line(tree, source)
    lines = encrypted.split("\n")
    source = "\n".join(
        lines[:insert_at]
        + xor_encryption.build_decryptor(key, decoder_names=decoder_names)
        + lines[insert_at:]
    )
    compile(source, "<pipeline-xor>", "exec")
    return source, "XOR encryption: encrypted literals with per-literal keys"


def _stage_function_flow(source: str, context: _StageContext) -> tuple[str, str]:
    original = source
    source, count = function_flow.flatten_simple_functions(
        source, seed=context.stage_seed
    )
    source = _preserve_integer_spellings(original, source)
    return source, f"Function flow: flattened {count} simple functions"


def _stage_module_flow(source: str, context: _StageContext) -> tuple[str, str]:
    original = source
    try:
        source = flow_obfuscator.obfuscate(
            source, seed=context.stage_seed
        )
    except flow_obfuscator.UnsafeToTransform as exc:
        context.module_flow_succeeded = False
        details = "; ".join(
            line.removeprefix("  - ")
            for line in str(exc).splitlines()[1:]
        )
        return source, f"Flow obfuscator: skipped ({details})"
    source = _preserve_integer_spellings(original, source)
    context.module_flow_succeeded = True
    return source, "Flow obfuscator: transformed module control flow"


def _stage_flattener(source: str, context: _StageContext) -> tuple[str, str]:
    if not context.module_flow_succeeded:
        return source, "Flattener: skipped (requires flow obfuscation)"
    source, count = flattener.add_opaque_predicates(
        source, seed=context.stage_seed
    )
    return source, f"Flattener: added {count} predicates"


def _stage_name_mangler(source: str, context: _StageContext) -> tuple[str, str]:
    tree = ast.parse(source)
    if any(
        isinstance(node, ast.Name) and node.id == "__all__"
        for node in ast.walk(tree)
    ):
        return source, "Name mangler: skipped (module uses __all__)"
    source, count = _mangle_source(
        source,
        context.name_length,
        random.Random(context.stage_seed),
        context.protected_names,
        context.attribute_mapping,
    )
    return source, f"Name mangler: renamed {count} identifiers"


def _stage_cleanup(source: str, context: _StageContext) -> tuple[str, str]:
    original = source
    source = _preserve_integer_spellings(original, cleanup.process(source))
    compile(source, "<pipeline-cleanup>", "exec")
    return source, "Cleanup: removed comments and compacted formatting"


def _stage_numeric(source: str, context: _StageContext) -> tuple[str, str]:
    source, count = numeric_obfuscator.obfuscate_numeric_constants(
        source, seed=context.stage_seed
    )
    return source, f"Numeric obfuscation: rewrote {count} integer literals"


STAGE_HANDLERS: dict[str, StageHandler] = {
    "junk": _stage_junk,
    "imports": _stage_imports,
    "xor": _stage_xor,
    "function_flow": _stage_function_flow,
    "module_flow": _stage_module_flow,
    "flattener": _stage_flattener,
    "name_mangler": _stage_name_mangler,
    "cleanup": _stage_cleanup,
    "numeric": _stage_numeric,
}


def _xor_decoder_names(source: str, occurrence: int) -> tuple[str, str]:
    used = {
        token.string
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type == tokenize.NAME
    }
    if occurrence == 1 and not ({"_d", "_db"} & used):
        return "_d", "_db"

    serial = max(2, occurrence)
    while True:
        names = (f"_xor_text_{serial}", f"_xor_bytes_{serial}")
        if not (set(names) & used):
            return names
        serial += 1


def validate_stage_order(stage_order: Sequence[str]) -> None:
    if not set(STAGE_HANDLERS).issubset(stage_order):
        missing = sorted(set(STAGE_HANDLERS) - set(stage_order))
        unknown = sorted(set(stage_order) - set(STAGE_HANDLERS))
        raise ValueError(
            f"Stage order must contain every registered stage at least once; "
            f"missing={missing}, unknown={unknown}."
        )

    if any(name not in STAGE_HANDLERS for name in stage_order):
        unknown = sorted(set(stage_order) - set(STAGE_HANDLERS))
        raise ValueError(f"Unknown pipeline stage IDs: {unknown}.")

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
    _seed_allocator: _SeedAllocator | None = None,
    import_mode: str = "aliases",
    attribute_mapping: dict[str, str] | None = None,
) -> tuple[str, list[str]]:
    """Apply the configured transformation stages in sequence."""
    validate_stage_order(stage_order)
    seed_allocator = _seed_allocator or _SeedAllocator(seed)
    context = _StageContext(
        stage_seed=0,
        junk_count=junk_count,
        name_length=name_length,
        protected_names=protected_names,
        attribute_mapping=attribute_mapping,
        import_mode=import_mode,
    )
    report: list[str] = []
    stage_totals = Counter(stage_order)
    stage_seen: Counter[str] = Counter()
    for index, stage_name in enumerate(stage_order, start=1):
        stage_seen[stage_name] += 1
        stage_seed = seed_allocator.next_seed()
        context.stage_seed = stage_seed
        context.current_stage_occurrence = stage_seen[stage_name]
        prefix = f"{progress_label} | " if progress_label else ""
        prefix += (
            f"Layer {layer_number}/{layer_count} | "
            f"Stage {index}/{len(stage_order)}: {stage_name} "
            f"({stage_seen[stage_name]}/{stage_totals[stage_name]}) "
            f"seed={stage_seed}"
        )
        if progress is not None:
            progress(f"{prefix} — started")
        try:
            source, description = STAGE_HANDLERS[stage_name](source, context)
        except Exception as exc:
            if progress is not None:
                progress(f"{prefix} — failed: {type(exc).__name__}: {exc}")
            raise
        report.append(f"{index}. {description} [seed={stage_seed}]")
        if progress is not None:
            progress(f"{prefix} — completed: {description}")
    return source, report


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
    _seed_allocator: _SeedAllocator | None = None,
    import_mode: str = "aliases",
    attribute_mapping: dict[str, str] | None = None,
) -> tuple[str, list[str]]:
    """Run the complete pipeline the requested number of times in sequence."""
    if layers < 1:
        raise ValueError("The number of layers must be at least 1.")
    validate_stage_order(stage_order)
    seed_allocator = _seed_allocator or _SeedAllocator(seed)
    report: list[str] = []
    for layer_number in range(1, layers + 1):
        source, stage_report = transform(
            source,
            seed=seed,
            junk_count=junk_count,
            name_length=name_length,
            protected_names=protected_names,
            stage_order=stage_order,
            progress=progress,
            layer_number=layer_number,
            layer_count=layers,
            progress_label=progress_label,
            _seed_allocator=seed_allocator,
            import_mode=import_mode,
            attribute_mapping=attribute_mapping,
        )
        report.append(f"Layer {layer_number}/{layers}")
        report.extend(f"  {line}" for line in stage_report)
    return source, report


def _read_python_file(path: Path) -> str:
    with tokenize.open(str(path)) as source_file:
        return source_file.read()


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
    """Transform one Python file and write to a separate output file."""
    source_path = source_path.expanduser().resolve()
    if not source_path.is_file() or source_path.suffix.lower() != ".py":
        raise ValueError("Input must be an existing .py file.")
    if output_path is None:
        output_path = source_path.with_name(f"{source_path.stem}_edited.py")
    else:
        output_path = output_path.expanduser().resolve()
    if output_path == source_path:
        raise ValueError("The output file must be different from the input file.")

    transformed, report = transform_layers(
        _read_python_file(source_path), seed, layers, junk_count,
        name_length, stage_order=stage_order, progress=progress,
        progress_label=source_path.name,
        import_mode=import_mode,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(transformed, encoding="utf-8", newline="")
    return [f"Saved: {output_path}", *report]


def _python_files(root: Path):
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root)
        if not any(part in SKIPPED_DIRECTORIES for part in relative.parts):
            yield path


def _imported_names(paths: list[Path]) -> set[str]:
    names: set[str] = set()
    for path in paths:
        tree = ast.parse(_read_python_file(path), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name != "*":
                        names.add(alias.name)
                        if alias.asname:
                            names.add(alias.asname)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    names.add(alias.asname or alias.name.split(".")[0])
    return names


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
    """Transform every Python file and copy other files unchanged."""
    source_folder = source_folder.expanduser().resolve()
    if not source_folder.is_dir():
        raise ValueError("Input must be an existing directory.")
    if output_folder is None:
        output_folder = source_folder.with_name(source_folder.name + "_processed")
    else:
        output_folder = output_folder.expanduser().resolve()
    if output_folder == source_folder or source_folder in output_folder.parents:
        raise ValueError("The output directory must be outside the input directory.")
    if output_folder.exists():
        raise FileExistsError(f"Output directory already exists: {output_folder}")

    python_files = list(_python_files(source_folder))
    if not python_files:
        raise ValueError("No Python files were found in the input directory.")
    protected_names = _imported_names(python_files)
    attribute_names = name_mangler.collect_class_attribute_names(python_files)
    attribute_mapping = name_mangler.make_mapping(
        attribute_names, name_length, random.Random(seed)
    )

    output_folder.mkdir(parents=True)
    processed = 0
    seed_allocator = _SeedAllocator(seed)
    try:
        for source_path in source_folder.rglob("*"):
            relative = source_path.relative_to(source_folder)
            if any(part in SKIPPED_DIRECTORIES for part in relative.parts):
                continue
            destination = output_folder / relative
            if source_path.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
            elif source_path.suffix.lower() == ".py":
                result, _ = transform_layers(
                    _read_python_file(source_path),
                    seed=seed,
                    layers=layers,
                    junk_count=junk_count,
                    name_length=name_length,
                    protected_names=protected_names,
                    stage_order=stage_order,
                    progress=progress,
                    progress_label=relative.as_posix(),
                    _seed_allocator=seed_allocator,
                    import_mode=import_mode,
                    attribute_mapping=attribute_mapping,
                )
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(result, encoding="utf-8", newline="")
                processed += 1
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_path, destination)
    except Exception:
        shutil.rmtree(output_folder, ignore_errors=True)
        raise

    return [
        f"Saved folder: {output_folder}",
        f"Transformed Python files: {processed}",
        f"Layers per file: {layers}",
        "Copied other files unchanged.",
    ]


def _choose_file() -> Path:
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError as exc:
        raise RuntimeError("tkinter is unavailable; pass an input path on the command line.") from exc
    root = tk.Tk()
    root.withdraw()
    try:
        selected = filedialog.askopenfilename(
            title="Select a Python script",
            filetypes=(("Python files", "*.py"), ("All files", "*.*")),
        )
    finally:
        root.destroy()
    if not selected:
        raise RuntimeError("No input file was selected.")
    return Path(selected)


def _choose_folder() -> Path:
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError as exc:
        raise RuntimeError("tkinter is unavailable; pass an input path on the command line.") from exc
    root = tk.Tk()
    root.withdraw()
    try:
        selected = filedialog.askdirectory(title="Select a folder to process")
    finally:
        root.destroy()
    if not selected:
        raise RuntimeError("No input folder was selected.")
    return Path(selected)


def _parse_stage_order(value: str) -> tuple[str, ...]:
    stages = tuple(part.strip() for part in value.split(",") if part.strip())
    if not stages:
        raise argparse.ArgumentTypeError("Provide at least one pipeline stage ID.")
    return stages


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the source transformation pipeline on a file or folder."
    )
    parser.add_argument(
        "target", nargs="?", type=Path,
        help="input .py file, or an input directory when --folder is set",
    )
    parser.add_argument("-o", "--output", type=Path, help="output file or directory")
    parser.add_argument("--folder", action="store_true", help="process a directory recursively")
    parser.add_argument("--seed", type=int, help="seed the random transformations")
    parser.add_argument("--layers", type=int, default=1,
                        help="number of times to run the complete pipeline (default: 1)")
    parser.add_argument(
        "--import-mode",
        choices=("aliases", "runtime"),
        default="aliases",
        help="import rewriting mode (default: aliases)",
    )
    parser.add_argument(
        "--stage-order",
        type=_parse_stage_order,
        metavar="ID,...",
        help="comma-separated stage IDs; IDs may repeat",
    )
    parser.add_argument("--junk-count", type=int, default=8, help="number of junk blocks (default: 8)")
    parser.add_argument("--name-length", type=int, choices=range(4, 7), default=5,
                        help="generated identifier length (default: 5)")
    return parser


def _print_progress(message: str) -> None:
    print(message, flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.junk_count < 1:
        parser.error("--junk-count must be at least 1")
    if args.layers < 1:
        parser.error("--layers must be at least 1")
    try:
        stage_order = args.stage_order or STAGE_ORDER
        validate_stage_order(stage_order)
        target = args.target or (_choose_folder() if args.folder else _choose_file())
        if args.folder:
            lines = process_folder(
                target, args.output, args.seed, args.junk_count,
                args.name_length, args.layers, stage_order=stage_order,
                progress=_print_progress, import_mode=args.import_mode,
            )
            print("\n".join(lines))
        else:
            lines = process_file(
                target, args.output, args.seed, args.junk_count,
                args.name_length, args.layers, stage_order=stage_order,
                progress=_print_progress, import_mode=args.import_mode,
            )
            print(lines[0])
    except (
        OSError,
        SyntaxError,
        UnicodeError,
        ValueError,
        RuntimeError,
        tokenize.TokenError,
        flow_obfuscator.UnsafeToTransform,
    ) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
