"""Compare an example application's output and tests before/after obfuscation.

Run from a checkout: python scripts/verify_example.py Test
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from code_editors import PipelineConfig, process_folder

STRONG_ORDER = tuple(
    "imports,junk,xor,name_mangler,function_flow,module_flow,"
    "flattener,junk,xor,name_mangler,numeric,cleanup".split(",")
)


def run(root: Path, arguments: list[str], input_text: str | None = None) -> str:
    environment = dict(os.environ, PYTHONIOENCODING="utf-8")
    result = subprocess.run(
        [sys.executable, *arguments],
        cwd=root,
        input=input_text,
        capture_output=True,
        encoding="utf-8",
        env=environment,
        timeout=120,
    )
    if result.returncode:
        raise RuntimeError(f"Command failed in {root}:\n{result.stdout}\n{result.stderr}")
    return result.stdout + result.stderr if input_text is None else result.stdout


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--literal-mode", choices=("xor", "layered", "chained", "polymorphic"), default="xor")
    parser.add_argument("--dispatch-mode", choices=("branches", "table"), default="branches")
    parser.add_argument("--allow-introspection", action="store_true")
    parser.add_argument("--extended-flow", action="store_true")
    parser.add_argument("--rename-modules", action="store_true")
    parser.add_argument("--all", action="store_true", help="verify the complete strong preset")
    args = parser.parse_args()
    source = args.source.resolve()
    cases = (
        "12 | 0 | 1 | 3 | 8 | 6\n",   # Target not yet met.
        "12 | 0 | 0 | 0 | 12 | 6\n",  # Target already met.
        "6 | 0 | 0 | 0 | 0 | 2\n",    # No evaluated competencies.
        "8 | 1 | 2 | 1 | 0 | 4\n",    # Future competencies and upgrades.
    )
    originals = [run(source, ["main.py"], case) for case in cases]
    print("Original tests:\n" + run(source, ["-m", "unittest", "discover", "-s", "tests", "-q"]))
    with tempfile.TemporaryDirectory(prefix=".verification-", dir=ROOT) as temporary:
        output = Path(temporary) / "output"
        config = PipelineConfig(
            seed=20261107, import_mode="runtime", stage_order=STRONG_ORDER,
            literal_mode="polymorphic" if args.all else args.literal_mode,
            dispatch_mode="table" if args.all else args.dispatch_mode,
            allow_introspection=args.allow_introspection,
            extended_flow=args.extended_flow or args.all,
            rename_modules=args.rename_modules,
            layers=2 if args.all else 1,
            junk_count=16 if args.all else 8,
            name_length=6 if args.all else 5,
        )
        process_folder(source, output, config)
        for case, original in zip(cases, originals):
            transformed = run(output, ["main.py"], case)
            if original != transformed:
                raise AssertionError(f"Original and obfuscated outputs differ for {case!r}")
        print(f"Application outputs match ({len(cases)} inputs)")


if __name__ == "__main__":
    main()
