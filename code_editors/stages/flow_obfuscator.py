
#!/usr/bin/env python3
"""Conservative Python Flow Obfuscator (Python 3.9+)."""

from __future__ import annotations

import argparse
import ast
import io
import random
import subprocess
import sys
import tempfile
import tokenize
from pathlib import Path
from typing import List, Optional, Sequence, Set, Tuple


class UnsafeToTransform(Exception):
    """Raised when static checks find risky constructs."""


_DYNAMIC_NAMES = {
    "vars", "dir", "eval", "exec", "compile",
    "__import__", "getattr", "setattr", "delattr",
}

_FRAME_ATTRIBUTES = {
    "_getframe", "_current_frames", "currentframe", "settrace", "gettrace",
    "setprofile", "getprofile", "f_back", "f_code", "f_globals",
    "f_locals", "f_lineno", "f_lasti", "tb_frame", "tb_next",
}


class SafetyVisitor(ast.NodeVisitor):

    def __init__(self, tree: ast.Module) -> None:
        self.reasons: List[str] = []
        self.sys_aliases = {"sys"}

        for item in ast.walk(tree):
            if isinstance(item, ast.Import):
                for alias in item.names:
                    if alias.name == "sys":
                        self.sys_aliases.add(alias.asname or "sys")

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in _DYNAMIC_NAMES:
            self.reasons.append(
                "dynamic namespace access or dynamic code execution: "
                + node.id
            )
        elif node.id in {"__all__"}:
            self.reasons.append("direct access to " + node.id)
        elif node.id in {
            "__file__", "__cached__", "__spec__", "__loader__", "__package__"
        }:
            self.reasons.append("access to module metadata: " + node.id)

        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in _FRAME_ATTRIBUTES:
            self.reasons.append(
                "runtime introspection: " + node.attr
            )
        elif (
            node.attr in {"argv", "path"}
            and isinstance(node.value, ast.Name)
            and node.value.id in self.sys_aliases
        ):
            self.reasons.append("access to sys." + node.attr)

        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name == "inspect" or alias.name.startswith("inspect."):
                self.reasons.append("import of the inspect module")

        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module == "inspect":
            self.reasons.append("import of the inspect module")

        if any(alias.name == "*" for alias in node.names):
            self.reasons.append(
                "star import may overwrite the state-machine variable"
            )

        for alias in node.names:
            if (
                alias.name in _DYNAMIC_NAMES
                or alias.name in _FRAME_ATTRIBUTES
                or (
                    node.module == "sys"
                    and alias.name in {"argv", "path"}
                )
            ):
                self.reasons.append(
                    "import of an introspection function: " + alias.name
                )

        self.generic_visit(node)

    def visit_Break(self, node: ast.Break) -> None:
        # Compound statements are kept intact inside their dispatcher branch,
        # so break remains nested in the same source-level loop.
        self.generic_visit(node)

    def visit_Continue(self, node: ast.Continue) -> None:
        # Compound statements are kept intact inside their dispatcher branch,
        # so continue remains nested in the same source-level loop.
        self.generic_visit(node)


def _module_header(
    body: List[ast.stmt],
) -> Tuple[List[ast.stmt], List[ast.stmt]]:

    header: List[ast.stmt] = []
    remaining = list(body)

    if (
        remaining
        and isinstance(remaining[0], ast.Expr)
        and isinstance(remaining[0].value, ast.Constant)
        and isinstance(remaining[0].value.value, str)
    ):
        header.append(remaining.pop(0))

    while (
        remaining
        and isinstance(remaining[0], ast.ImportFrom)
        and remaining[0].module == "__future__"
    ):
        header.append(remaining.pop(0))

    return header, remaining


def _source_identifiers(source: str) -> Set[str]:
    names: Set[str] = set()
    reader = io.StringIO(source).readline

    for token in tokenize.generate_tokens(reader):
        if token.type == tokenize.NAME:
            names.add(token.string)

    return names


def _new_state_name(
    rng: random.Random,
    identifiers: Set[str],
) -> str:

    while True:
        candidate = "_flow_state_" + f"{rng.getrandbits(64):016x}"

        if candidate not in identifiers:
            return candidate


def _check_safety(tree: ast.Module) -> None:
    visitor = SafetyVisitor(tree)
    visitor.visit(tree)

    if visitor.reasons:
        unique_reasons = list(dict.fromkeys(visitor.reasons))
        details = "\n  - ".join(unique_reasons)

        raise UnsafeToTransform(
            "Unsafe constructs were found for this transformation:\n  - "
            + details
            + "\nRemove them or leave this file unobfuscated."
        )


def _validate_state_machine(
    state_ids: List[int],
    dispatch_order: List[int],
    body_count: int,
) -> None:

    if len(state_ids) != body_count:
        raise ValueError("Invalid number of states.")

    if len(set(state_ids)) != body_count:
        raise ValueError("State identifiers are not unique.")

    if len(dispatch_order) != body_count:
        raise ValueError("The dispatcher is incomplete.")

    if sorted(dispatch_order) != list(range(body_count)):
        raise ValueError("Invalid dispatcher order.")

    if 0 in state_ids:
        raise ValueError("The final state conflicts with a working state.")

    transitions = [
        state_ids[index + 1] if index + 1 < body_count else 0
        for index in range(body_count)
    ]

    if transitions[-1] != 0:
        raise ValueError("Invalid final state.")

    if any(
        state not in state_ids and state != 0
        for state in transitions
    ):
        raise ValueError("Invalid state-machine transition.")


def obfuscate(source: str, seed: Optional[int] = None) -> str:

    tree = ast.parse(source, type_comments=True)
    header, body = _module_header(list(tree.body))

    if not body:
        return source if source.endswith("\n") else source + "\n"

    _check_safety(tree)

    rng = random.Random(seed)
    identifiers = _source_identifiers(source)

    state_var = _new_state_name(rng, identifiers)

    state_ids = rng.sample(
        range(100_000, 2_147_483_647),
        len(body),
    )

    dispatch_order = list(range(len(body)))
    rng.shuffle(dispatch_order)

    _validate_state_machine(
        state_ids,
        dispatch_order,
        len(body),
    )

    lines: List[str] = []

    lines.append("#!/usr/bin/env python3")
    lines.append("# -*- coding: utf-8 -*-")

    for statement in header:
        lines.append(ast.unparse(statement))

    if header:
        lines.append("")

    valid_states = ", ".join(map(str, state_ids))

    if len(state_ids) == 1:
        valid_states += ","

    lines.append(f"{state_var} = {state_ids[0]}")
    lines.append(
        f"while {state_var} in ({valid_states}):"
    )

    for position, original_index in enumerate(dispatch_order):

        statement = body[original_index]
        state_id = state_ids[original_index]

        next_state = (
            state_ids[original_index + 1]
            if original_index + 1 < len(body)
            else 0
        )

        keyword = "if" if position == 0 else "elif"

        lines.append(
            f"    {keyword} {state_var} == {state_id}:"
        )

        rendered = ast.unparse(statement)
        rendered_lines = rendered.splitlines() or ["pass"]

        lines.extend(
            "        " + line if line else ""
            for line in rendered_lines
        )

        lines.append(f"        {state_var} = {next_state}")

    lines.append(f"del {state_var}")

    result = "\n".join(lines) + "\n"

    compile(result, "<obfuscated>", "exec")

    return result


def _verify(
    original: Path,
    transformed: Path,
    timeout: float,
) -> bool:

    try:
        a = subprocess.run(
            [sys.executable, str(original)],
            capture_output=True,
            text=True,
            timeout=timeout,
        )

        b = subprocess.run(
            [sys.executable, str(transformed)],
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    except subprocess.TimeoutExpired:
        print(
            "Verification was inconclusive: the time limit was exceeded."
        )
        return False

    identical = (
        a.returncode == b.returncode
        and a.stdout == b.stdout
        and a.stderr == b.stderr
    )

    if identical:
        print(
            "Runtime comparison: stdout, stderr, and exit codes match."
        )
    else:
        print("Runtime comparison: a difference was found.")

        print(f"Original exit code: {a.returncode}")
        print(f"Obfuscated exit code: {b.returncode}")

        if a.stdout != b.stdout:
            print("stdout differs:")
            print("--- original ---\n" + a.stdout)
            print("--- obfuscated ---\n" + b.stdout)

        if a.stderr != b.stderr:
            print("stderr differs:")
            print("--- original ---\n" + a.stderr)
            print("--- obfuscated ---\n" + b.stderr)

    return identical


def _atomic_write(path: Path, content: str) -> None:

    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name: Optional[str] = None

    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="\n",
            dir=str(path.parent),
            prefix=path.name + ".",
            suffix=".tmp",
            delete=False,
        ) as temp:
            temp.write(content)
            temp_name = temp.name

        Path(temp_name).replace(path)

    finally:
        if temp_name:
            try:
                Path(temp_name).unlink(missing_ok=True)
            except OSError:
                pass


def main(argv: Optional[Sequence[str]] = None) -> int:

    parser = argparse.ArgumentParser(
        description=(
            "Conservative Python control-flow obfuscator."
        )
    )

    parser.add_argument(
        "input",
        type=Path,
        help="input .py file",
    )

    parser.add_argument(
        "output",
        nargs="?",
        type=Path,
        help="output file",
    )

    parser.add_argument(
        "--seed",
        type=int,
        help="random seed for reproducible output",
    )

    parser.add_argument(
        "--verify",
        action="store_true",
        help="run both files and compare stdout, stderr, and exit codes",
    )

    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="time limit for each run with --verify",
    )

    args = parser.parse_args(argv)

    source_path = args.input.expanduser().resolve()

    output_path = (
        args.output.expanduser().resolve()
        if args.output
        else source_path.with_name(
            source_path.stem + "_flow.py"
        )
    )

    if source_path == output_path:
        parser.error("The output file cannot be the input file.")

    if source_path.parent != output_path.parent:
        parser.error(
            "The output file must be in the same directory as the input file."
        )

    if not source_path.is_file():
        parser.error(f"Input file not found: {source_path}")

    if args.timeout <= 0:
        parser.error("--timeout must be greater than zero.")

    try:
        with tokenize.open(str(source_path)) as file:
            source = file.read()

        transformed = obfuscate(source, args.seed)
        _atomic_write(output_path, transformed)

    except (
        OSError,
        SyntaxError,
        UnicodeError,
        UnsafeToTransform,
        ValueError,
    ) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Saved: {output_path}")

    print(
        "Note: runtime comparison does not prove program equivalence."
    )

    if args.verify:
        return (
            0
            if _verify(source_path, output_path, args.timeout)
            else 2
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
