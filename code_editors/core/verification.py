"""Explicit differential execution, isolated output checks and test commands."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .configuration import PipelineConfig, VerificationCase


def load_verification_plan(path: Path) -> dict:
    """Read a versioned JSON plan without executing shell commands."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or set(data) - {"version", "cases", "files", "commands"}:
        raise ValueError("Verification plan must contain version, cases, files and/or commands")
    if type(data.get("version", 1)) is not int or data.get("version", 1) != 1:
        raise ValueError("Unsupported verification plan version")
    raw_cases = data.get("cases", [])
    files = data.get("files", [])
    commands = data.get("commands", [])
    if not isinstance(raw_cases, list) or not isinstance(files, list) or not isinstance(commands, list):
        raise ValueError("Verification cases, files and commands must be arrays")
    cases = []
    for index, case in enumerate(raw_cases):
        if not isinstance(case, dict) or set(case) - {"name", "stdin", "args", "expected_exit"}:
            raise ValueError(f"Invalid verification case {index + 1}")
        args = case.get("args", [])
        if not isinstance(args, list) or not all(isinstance(value, str) for value in args):
            raise ValueError("Case args must be an array of strings")
        cases.append(VerificationCase(case.get("name", f"case-{index + 1}"),
            case.get("stdin", ""), tuple(args), case.get("expected_exit", 0)))
    if not all(isinstance(value, str) for value in files):
        raise ValueError("Verification files must be strings")
    if not all(isinstance(command, list) and command and
               all(isinstance(value, str) for value in command) for command in commands):
        raise ValueError("Commands must be nonempty arrays of Python arguments")
    return dict(verify_cases=tuple(cases), verify_files=tuple(files),
                verify_commands=tuple(tuple(command) for command in commands))


def _execute(arguments: list[str], cwd: Path, stdin: str, timeout: float):
    environment = dict(os.environ, PYTHONIOENCODING="utf-8")
    # Put the selected project first, including for external test harnesses.
    environment["PYTHONPATH"] = str(cwd) + (os.pathsep + environment["PYTHONPATH"]
                                           if environment.get("PYTHONPATH") else "")
    started = time.perf_counter()
    try:
        result = subprocess.run([sys.executable, *arguments], cwd=cwd,
            input=stdin.encode("utf-8"), capture_output=True, timeout=timeout,
            env=environment)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Verification timed out: {arguments[0]}") from exc
    return result, time.perf_counter() - started


def _file_hash(root: Path, relative: str) -> str:
    path = (root / relative).resolve()
    if root not in path.parents or not path.is_file():
        raise RuntimeError(f"Verification file missing or outside sandbox: {relative}")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_project(source: Path, destination: Path) -> None:
    # Verification never follows project symlinks to files outside its sandbox.
    ignored = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox"}
    for directory, directories, files in os.walk(source, followlinks=False):
        directories[:] = [name for name in directories if name not in ignored]
        for name in (*directories, *files):
            path = Path(directory) / name
            if path.is_symlink() or getattr(path, 'is_junction', lambda: False)():
                raise ValueError(f"Isolated verification does not support symlinks/junctions: {path}")
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns(*ignored))


def verification_summary(result: dict | None) -> list[str]:
    if result is None:
        return []
    lines = []
    for case in result['cases']:
        ratio = f"{case['runtime_ratio']:.2f}x" if case['runtime_ratio'] is not None else 'n/a'
        lines.append(f"Verification: {case['name']}; original={case['original_seconds']:.3f}s; "
            f"obfuscated={case['transformed_seconds']:.3f}s; ratio={ratio}; runs={case['runs']}")
    for index, suite in enumerate(result['test_commands'], 1):
        lines.append(f"Contract tests: command {index} passed; original={suite['original_seconds']:.3f}s; "
                     f"obfuscated={suite['transformed_seconds']:.3f}s")
    return lines


def verify_programs(original: Path, transformed: Path, config: PipelineConfig,
                    *, original_root: Path | None = None,
                    transformed_root: Path | None = None) -> dict:
    """Compare explicit cases and run assertions against independent project copies.

    Legacy single-entry verification keeps its original working-directory
    behavior. Plans, file checks and command suites use fresh trees for each run.
    """
    roots = ((original_root or original.parent).resolve(),
             (transformed_root or transformed.parent).resolve())
    programs = (original.resolve(), transformed.resolve())
    isolated = bool(config.verify_cases or config.verify_files or config.verify_commands or config.benchmark_runs > 1)
    cases = (config.verify_cases or (VerificationCase(stdin=config.verify_stdin),)
             if config.verify_entry or not config.verify_commands else ())
    records = []
    for case in cases:
        timings = [[], []]
        stdout_hash = None
        file_hashes = {}
        for repetition in range(config.benchmark_runs):
            results = []
            hashes = []
            with tempfile.TemporaryDirectory(prefix="obfuscator-verify-") as temporary:
                for index, (program, root) in enumerate(zip(programs, roots)):
                    if isolated:
                        workspace = Path(temporary) / str(index)
                        _copy_project(root, workspace)
                        entry = workspace / program.relative_to(root)
                        cwd = entry.parent
                    else:
                        workspace, entry, cwd = root, program, program.parent
                    result, seconds = _execute([str(entry), *case.args], cwd,
                                               case.stdin, config.verify_timeout)
                    if result.returncode != case.expected_exit:
                        detail = result.stderr.decode("utf-8", errors="replace")[-4000:]
                        raise RuntimeError(f"Differential verification: case {case.name!r} "
                            f"exited unsuccessfully (expected {case.expected_exit}, got {result.returncode})\n{detail}")
                    results.append(result)
                    timings[index].append(seconds)
                    hashes.append({name: _file_hash(workspace, name) for name in config.verify_files})
                if results[0].stdout != results[1].stdout or results[0].stderr != results[1].stderr:
                    raise RuntimeError(f"Differential verification: stdout or stderr differs in case {case.name!r}")
                if hashes[0] != hashes[1]:
                    changed = [name for name in config.verify_files if hashes[0][name] != hashes[1][name]]
                    raise RuntimeError(f"Differential verification: output files differ: {', '.join(changed)}")
                stdout_hash = hashlib.sha256(results[0].stdout).hexdigest()
                file_hashes = hashes[0]
        original_seconds, transformed_seconds = map(statistics.median, timings)
        records.append({"name": case.name, "status": "matched", "runs": config.benchmark_runs,
            "stdout_sha256": stdout_hash, "files": file_hashes,
            "original_seconds": original_seconds, "transformed_seconds": transformed_seconds,
            "runtime_ratio": transformed_seconds / original_seconds if original_seconds else None,
            "samples_seconds": {"original": timings[0], "transformed": timings[1]}})
    suites = []
    for command in config.verify_commands:
        durations = []
        with tempfile.TemporaryDirectory(prefix="obfuscator-suite-") as temporary:
            for index, root in enumerate(roots):
                workspace = Path(temporary) / str(index)
                _copy_project(root, workspace)
                # Relocate absolute harness paths within each original tree.
                args = [str(workspace / Path(value).relative_to(roots[0]))
                        if Path(value).is_absolute() and Path(value).is_relative_to(roots[0])
                        else value for value in command]
                result, seconds = _execute(args, workspace, "", config.verify_timeout)
                if result.returncode or re.search(rb"Ran 0 tests\b", result.stderr):
                    detail = (result.stdout + result.stderr).decode("utf-8", errors="replace")[-4000:]
                    raise RuntimeError(f"Verification test command failed or ran no tests: {command!r}\n{detail}")
                durations.append(seconds)
        suites.append({"status": "passed", "command": list(command),
                       "original_seconds": durations[0], "transformed_seconds": durations[1]})
    return {"status": "matched", "stdout_sha256": records[0]["stdout_sha256"] if records else None,
            "cases": records, "test_commands": suites, "isolated": isolated}
