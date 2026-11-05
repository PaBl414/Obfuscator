"""Orchestrate the project's source transformation stages."""

from __future__ import annotations

import argparse
import ast
import random
import secrets
import shutil
import sys
import tokenize
from pathlib import Path

from .stages import cleanup, flattener, flow_obfuscator, function_flow
from .stages import import_obfuscator, junk_editor, name_mangler, numeric_obfuscator
from .stages import xor_encryption

SKIPPED_DIRECTORIES = {
    ".git", ".mypy_cache", ".pytest_cache", ".venv", "__pycache__",
    "build", "dist", "node_modules", "venv",
}


def _mangle_source(
    source: str, length: int, rng: random.Random
) -> tuple[str, int]:
    tree = ast.parse(source)
    visitor = name_mangler.ModuleDefinitions()
    visitor.visit(tree)
    candidates = {
        name for name in visitor.names
        if name not in visitor.protected
        and name not in name_mangler.BUILTIN_NAMES
        and not (name.startswith("__") and name.endswith("__"))
        and name.isidentifier()
    }
    mapping = name_mangler.make_mapping(candidates, length, rng)
    rewritten = name_mangler.apply_edits(
        source, name_mangler.python_edits(source, mapping)
    )
    ast.parse(rewritten)
    return rewritten, len(mapping)


def transform(
    source: str,
    seed: int | None = None,
    junk_count: int = 8,
    name_length: int = 5,
) -> tuple[str, list[str]]:
    """Apply all transformation stages in their documented order."""
    rng = random.Random(seed)
    report: list[str] = []

    source, count = junk_editor.inject_junk(
        source, count=junk_count, seed=rng.randrange(2**32)
    )
    report.append(f"1. Junk editor: inserted {count} blocks")

    source, import_count = import_obfuscator.obfuscate_imports(source)
    report.append(
        f"2. Import obfuscation: added explicit aliases to {import_count} imports"
    )

    key_rng = random.Random(seed) if seed is not None else random.SystemRandom()
    key = bytes(key_rng.randrange(256) for _ in range(16))
    encrypted, tree = xor_encryption.transform_source(source, key)
    insert_at = xor_encryption.find_insert_line(tree, source)
    lines = encrypted.split("\n")
    source = "\n".join(
        lines[:insert_at] + xor_encryption.build_decryptor(key) + lines[insert_at:]
    )
    compile(source, "<pipeline-xor>", "exec")
    report.append("3. XOR encryption: encrypted literals with per-literal keys")

    source, function_count = function_flow.flatten_simple_functions(
        source, seed=rng.randrange(2**32)
    )
    report.append(
        f"4. Function flow: flattened {function_count} simple functions"
    )

    source = flow_obfuscator.obfuscate(source, seed=rng.randrange(2**32))
    report.append("5. Flow obfuscator: transformed module control flow")

    source, predicate_count = flattener.add_opaque_predicates(
        source, seed=rng.randrange(2**32)
    )
    report.append(f"6. Flattener: added {predicate_count} predicates")

    source, renamed_count = _mangle_source(
        source, name_length, random.Random(rng.randrange(2**32))
    )
    report.append(f"7. Name mangler: renamed {renamed_count} identifiers")

    source = cleanup.process(source)
    compile(source, "<pipeline-cleanup>", "exec")
    report.append("8. Cleanup: removed comments and compacted formatting")

    source, numeric_count = numeric_obfuscator.obfuscate_numeric_constants(
        source, seed=rng.randrange(2**32)
    )
    report.append(
        f"9. Numeric obfuscation: rewrote {numeric_count} integer literals"
    )
    return source, report


def _layer_seeds(seed: int | None, layers: int) -> list[int | None]:
    if layers < 1:
        raise ValueError("The number of layers must be at least 1.")
    if layers == 1:
        return [seed]
    if seed is not None:
        return [seed + offset for offset in range(layers)]

    seeds: list[int] = []
    used: set[int] = set()
    while len(seeds) < layers:
        candidate = secrets.randbits(64)
        if candidate not in used:
            seeds.append(candidate)
            used.add(candidate)
    return seeds


def transform_layers(
    source: str,
    seed: int | None = None,
    layers: int = 1,
    junk_count: int = 8,
    name_length: int = 5,
) -> tuple[str, list[str]]:
    """Run the complete pipeline the requested number of times in sequence."""
    seeds = _layer_seeds(seed, layers)
    report: list[str] = []
    for layer_number, layer_seed in enumerate(seeds, start=1):
        source, stage_report = transform(
            source, layer_seed, junk_count, name_length
        )
        seed_description = "random seed" if layer_seed is None else f"seed: {layer_seed}"
        report.append(f"Layer {layer_number}/{layers} ({seed_description})")
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
        _read_python_file(source_path), seed, layers, junk_count, name_length
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(transformed, encoding="utf-8", newline="")
    return [f"Saved: {output_path}", *report]


def _python_files(root: Path):
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root)
        if not any(part in SKIPPED_DIRECTORIES for part in relative.parts):
            yield path


def process_folder(
    source_folder: Path,
    output_folder: Path | None = None,
    seed: int | None = None,
    junk_count: int = 8,
    name_length: int = 5,
    layers: int = 1,
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

    output_folder.mkdir(parents=True)
    processed = 0
    try:
        for source_path in source_folder.rglob("*"):
            relative = source_path.relative_to(source_folder)
            if any(part in SKIPPED_DIRECTORIES for part in relative.parts):
                continue
            destination = output_folder / relative
            if source_path.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
            elif source_path.suffix.lower() == ".py":
                file_seed = (
                    None if seed is None
                    else seed + processed * layers
                )
                result, _ = transform_layers(
                    _read_python_file(source_path),
                    seed=file_seed,
                    layers=layers,
                    junk_count=junk_count,
                    name_length=name_length,
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
    parser.add_argument("--junk-count", type=int, default=8, help="number of junk blocks (default: 8)")
    parser.add_argument("--name-length", type=int, choices=range(4, 7), default=5,
                        help="generated identifier length (default: 5)")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.junk_count < 1:
        parser.error("--junk-count must be at least 1")
    if args.layers < 1:
        parser.error("--layers must be at least 1")
    try:
        target = args.target or (_choose_folder() if args.folder else _choose_file())
        lines = (
            process_folder(
                target, args.output, args.seed, args.junk_count,
                args.name_length, args.layers
            )
            if args.folder else
            process_file(
                target, args.output, args.seed, args.junk_count,
                args.name_length, args.layers
            )
        )
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
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
