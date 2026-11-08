"""Relocate physical modules while retaining logical Python import contracts.

An ordinary importlib finder resolves logical module names to randomized files.
This keeps dynamic imports, pickle module names and relative imports coherent.
Non-Python resources move with their containing package; resource-only folders
and conventional entrypoint filenames retain their names.
"""

from __future__ import annotations

import ast
import json
import random
import tokenize
from pathlib import Path

from ..stages.xor_encryption import find_insert_line, obfuscate_chained_literals
from ..stages import name_mangler


def plan_layout(files: list[Path], root: Path, seed: int | None):
    rng = random.Random(seed)
    used = {part.casefold() for path in root.rglob("*")
            for part in (*path.relative_to(root).parts, path.stem)}

    def fresh():
        while True:
            value = "_" + f"{rng.getrandbits(64):016x}"
            if value.casefold() not in used:
                used.add(value)
                return value

    strings = set()
    for path in files:
        with tokenize.open(str(path)) as stream:
            tree = ast.parse(stream.read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                strings.add(node.value)
    def referenced(name):
        return any(name in value for value in strings)

    directories = sorted({parent for path in files
                          for parent in path.relative_to(root).parents
                          if parent != Path(".")}, key=lambda p: (len(p.parts), p.as_posix()))
    directory_map = {Path("."): Path(".")}
    for directory in directories:
        # Standalone script trees and literal filesystem contracts retain their
        # layout; ordinary packages can move without rewriting their resources.
        keep = not (root / directory / "__init__.py").exists() or referenced(directory.name)
        directory_map[directory] = directory_map[directory.parent] / (directory.name if keep else fresh())
    paths = {}
    modules = {}
    for path in files:
        relative = path.relative_to(root)
        keep = (relative.name in {"main.py", "__main__.py", "__init__.py", "conftest.py", "setup.py"}
                or referenced(relative.name)
                or (relative.parent != Path(".") and not (path.parent / "__init__.py").exists()))
        output = directory_map[relative.parent] / (relative.name if keep else fresh() + ".py")
        paths[relative] = output
        parts = relative.with_suffix("").parts
        module = ".".join(parts[:-1] if path.name == "__init__.py" else parts)
        if module:
            modules[module] = [output.as_posix(), path.name == "__init__.py"]
    # Support namespace packages too, rather than relying on their old folders.
    for directory, output in directory_map.items():
        if directory != Path("."):
            modules.setdefault(".".join(directory.parts), [output.as_posix(), None])
    return paths, directory_map, modules, fresh(), fresh()


def relocated_path(relative: Path, files: dict, directories: dict) -> Path:
    if relative in files:
        return files[relative]
    for parent in (relative, *relative.parents):
        if parent in directories:
            return directories[parent] / relative.relative_to(parent)
    return relative


def loader_source(modules: dict, install_name: str, seed: int | None) -> str:
    source = f'''import importlib.abc as _a
import importlib.util as _u
import sys as _s
from pathlib import Path as _P
import json as _j
_root = _P(__file__).resolve().parent
_mapping = _j.loads({json.dumps(modules)!r})
_installed = False
class _Finder(_a.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        record = _mapping.get(fullname)
        if record is None:
            return None
        relative, package = record
        location = _root / relative
        if package is None:
            spec = _u.spec_from_loader(fullname, loader=None, is_package=True)
            spec.submodule_search_locations = [str(location)]
            return spec
        return _u.spec_from_file_location(fullname, location,
            submodule_search_locations=[str(location.parent)] if package else None)
def {install_name}():
    global _installed
    if not _installed:
        _s.meta_path.insert(0, _Finder())
        _installed = True
'''
    rng = random.Random(seed)
    key = bytes(rng.randrange(256) for _ in range(16))
    source, _ = obfuscate_chained_literals(source, key, polymorphic=True)
    definitions = name_mangler.ModuleDefinitions()
    definitions.visit(ast.parse(source))
    mapping = name_mangler.make_mapping(
        definitions.names - name_mangler.BUILTIN_NAMES - {install_name}, 6, rng,
        reserved={install_name})
    # Import bindings stay aligned with their declarations; find_spec retains
    # the importlib protocol spelling while all ordinary bindings are mangled.
    imported = {alias.asname or alias.name.split('.')[0]
                for node in ast.walk(ast.parse(source))
                if isinstance(node, (ast.Import, ast.ImportFrom)) for alias in node.names}
    mapping = {name: value for name, value in mapping.items() if name not in imported}
    source = name_mangler.apply_edits(source, name_mangler.python_edits(source, mapping, {}))
    compile(source, "<module-loader>", "exec")
    return source


def inject_loader(source: str, relative: Path, runtime_name: str, install_name: str) -> str:
    # Preserve the source header and __future__ placement, including shebangs.
    tree = ast.parse(source)
    insert = find_insert_line(tree, source)
    depth = len(relative.parts) - 1
    prefix = f"_{runtime_name}_p"
    bootstrap = (
        f"import sys as {prefix}\n"
        f"{prefix}.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[{depth}]))\n"
        f"__import__({runtime_name!r}).{install_name}()\n"
    )
    lines = source.splitlines(keepends=True)
    if insert and not lines[insert - 1].endswith("\n"):
        lines[insert - 1] += "\n"
    result = "".join(lines[:insert]) + bootstrap + "".join(lines[insert:])
    compile(result, str(relative), "exec")
    return result
