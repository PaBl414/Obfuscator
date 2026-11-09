"""Build reports, explicit differential execution and optional native modules."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

from .configuration import PipelineConfig
from .verification import verify_programs


def check_native_dependencies(modules: tuple[str, ...]) -> None:
    if modules and importlib.util.find_spec("nuitka") is None:
        raise RuntimeError("--native-module requires Nuitka in this Python interpreter")


def compile_native_module(path: Path) -> Path:
    """Compile an output module, retaining its source unless compilation succeeds."""
    if path.suffix != ".py" or path.name in {"__init__.py", "__main__.py", "main.py"}:
        raise ValueError("Select an ordinary .py module for native compilation")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "nuitka", "--module", "--remove-output",
             "--no-pyi-file", f"--output-dir={path.parent}", str(path)],
            cwd=path.parent, capture_output=True, text=True, timeout=600,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Nuitka compilation timed out: {path}") from exc
    if result.returncode:
        raise RuntimeError(f"Nuitka compilation failed: {result.stderr[-4000:]}")
    candidates = [path.with_name(path.stem + suffix)
                  for suffix in importlib.machinery.EXTENSION_SUFFIXES]
    artifact = next((candidate for candidate in candidates if candidate.is_file()), None)
    if artifact is None:
        raise RuntimeError("Nuitka did not produce an extension module")
    path.unlink()
    return artifact


def validate_report_path(config: PipelineConfig, source: Path, destination: Path) -> None:
    if config.report_path is None:
        return
    report = config.report_path.expanduser().resolve()
    for target in (source, destination):
        if report == target or target in report.parents:
            raise ValueError("Report must be outside the input and output paths")


def project_entry(root: Path, relative: str) -> Path:
    if Path(relative).is_absolute():
        raise ValueError(f"Expected a relative project path: {relative}")
    path = (root / relative).resolve()
    if root not in path.parents or not path.is_file() or path.suffix != ".py":
        raise ValueError(f"Expected a relative .py file inside the project: {relative}")
    return path


def write_report(config: PipelineConfig, destination: Path, stages: list,
                 **details) -> None:
    if config.report_path is None:
        return
    report = {
        "schema_version": 1, "python": sys.version,
        "status": "completed",
        "configuration": asdict(config), "destination": str(destination),
        "stages": stages, **details,
    }
    report_path = config.report_path.expanduser().resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
