"""Command-line interface for ObfuscatorEdu."""

from __future__ import annotations

import argparse
import sys
import tokenize
from contextlib import contextmanager
from pathlib import Path

from .core.configuration import ALL_STAGE_ORDER, DEFAULT_STAGE_ORDER, PipelineConfig, validate_stage_order
from .core.filesystem import process_file, process_folder
from .stages import flow_obfuscator


@contextmanager
def _picker_dialog():
    """Open optional Tk dialogs and report unavailable desktop sessions clearly."""
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError as exc:
        raise RuntimeError(
            "Tkinter is unavailable for this Python. Install your distribution's "
            "Tk package, or pass a path: obfuscator-edu app.py / "
            "obfuscator-edu --folder ./project"
        ) from exc
    root = None
    try:
        root = tk.Tk()
        root.withdraw()
        yield filedialog
    except tk.TclError as exc:
        raise RuntimeError(
            "The graphical picker is unavailable (Tk/display). On a server, "
            "SSH session or container, pass an input path: obfuscator-edu app.py "
            "or obfuscator-edu --folder ./project"
        ) from exc
    finally:
        if root is not None:
            try:
                root.destroy()
            except tk.TclError:
                pass


def _choose_file() -> Path:
    with _picker_dialog() as filedialog:
        selected = filedialog.askopenfilename(
            title="Select a Python script",
            filetypes=(("Python files", "*.py"), ("All files", "*.*")),
        )
    if not selected:
        raise RuntimeError("No input file was selected")
    return Path(selected)


def _choose_folder() -> Path:
    with _picker_dialog() as filedialog:
        selected = filedialog.askdirectory(title="Select a folder to process")
    if not selected:
        raise RuntimeError("No input folder was selected")
    return Path(selected)


def _parse_stage_order(value: str) -> tuple[str, ...]:
    stages = tuple(part.strip() for part in value.split(",") if part.strip())
    if not stages:
        raise argparse.ArgumentTypeError("Provide at least one pipeline stage ID")
    try:
        validate_stage_order(stages)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    return stages


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="obfuscator-edu",
        description="Apply configurable source obfuscation to Python files or projects.",
    )
    parser.add_argument(
        "target",
        nargs="?",
        type=Path,
        help="input .py file, or an input directory with --folder",
    )
    parser.add_argument("-o", "--output", type=Path, help="output file or directory")
    parser.add_argument("--folder", action="store_true", help="process a directory recursively")
    parser.add_argument("--seed", type=int, help="make generated transformations reproducible")
    parser.add_argument(
        "--allow-introspection", action="store_true",
        help="allow locals/globals/vars/dir in flow and runtime-import transformations; "
             "observed namespaces may change (not enabled by --all)",
    )
    parser.add_argument(
        "--all", action="store_true",
        help="strong preset: runtime imports, polymorphic literals, extended flow, table dispatch, repeated stages, "
             "at least 2 layers/16 junk blocks, and 6-character names",
    )
    parser.add_argument(
        "--literal-mode", choices=("xor", "layered", "chained", "polymorphic"), default="xor",
        help="literal encoding: XOR, layered feedback, chained keys, or per-literal polymorphic codecs",
    )
    parser.add_argument(
        "--dispatch-mode", choices=("branches", "table"), default="branches",
        help="control-flow dispatch via conditional branches or a lookup table and match",
    )
    parser.add_argument(
        "--layers", type=int, default=1, help="complete pipeline repetitions (default: 1)"
    )
    parser.add_argument(
        "--import-mode",
        choices=("aliases", "runtime"),
        default="aliases",
        help="import rewriting strategy (default: aliases)",
    )
    parser.add_argument(
        "--stage-order",
        type=_parse_stage_order,
        metavar="ID,...",
        help="comma-separated stage IDs; every registered ID is required",
    )
    parser.add_argument(
        "--junk-count", type=int, default=8, help="junk blocks per occurrence (default: 8)"
    )
    parser.add_argument(
        "--name-length",
        type=int,
        choices=range(4, 7),
        default=5,
        help="generated identifier length (default: 5)",
    )
    parser.add_argument("--extended-flow", action="store_true", help="also transform generators and async functions")
    parser.add_argument("--rename-modules", action="store_true", help="randomize physical module paths (folder mode)")
    parser.add_argument("--report", type=Path, help="write a JSON transformation and compatibility report outside the input/output trees")
    parser.add_argument("--verify", action="store_true", help="execute and compare the input and output script (file mode)")
    parser.add_argument("--verify-entry", help="relative entrypoint to execute and compare (folder mode)")
    parser.add_argument("--verify-stdin", type=Path, help="UTF-8 file supplied to both verified processes")
    parser.add_argument("--verify-timeout", type=float, default=30.0, help="timeout in seconds per verified process")
    parser.add_argument("--native-module", action="append", default=[], metavar="RELATIVE.py", help="compile an output module with installed Nuitka (folder mode, repeatable)")
    return parser


def _print_progress(message: str) -> None:
    print(message, flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if not args.folder and (args.rename_modules or args.native_module or args.verify_entry):
            raise ValueError("--rename-modules, --native-module and --verify-entry require --folder")
        if args.folder and args.verify:
            raise ValueError("Use --verify-entry in folder mode")
        config = PipelineConfig(
            seed=args.seed,
            layers=max(2, args.layers) if args.all else args.layers,
            junk_count=max(16, args.junk_count) if args.all else args.junk_count,
            name_length=6 if args.all else args.name_length,
            import_mode="runtime" if args.all else args.import_mode,
            literal_mode="polymorphic" if args.all else args.literal_mode,
            dispatch_mode="table" if args.all else args.dispatch_mode,
            allow_introspection=args.allow_introspection,
            extended_flow=args.extended_flow or args.all,
            rename_modules=args.rename_modules,
            report_path=args.report,
            verify_entry=args.verify_entry or ("." if args.verify else None),
            verify_stdin=args.verify_stdin.read_text(encoding="utf-8") if args.verify_stdin else "",
            verify_timeout=args.verify_timeout,
            native_modules=tuple(args.native_module),
            stage_order=ALL_STAGE_ORDER if args.all else args.stage_order or DEFAULT_STAGE_ORDER,
        )
        target = args.target or (_choose_folder() if args.folder else _choose_file())
        if args.folder:
            lines = process_folder(target, args.output, config, progress=_print_progress)
            print("\n".join(lines))
        else:
            lines = process_file(target, args.output, config, progress=_print_progress)
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
