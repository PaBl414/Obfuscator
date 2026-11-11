"""Deterministic single-file launchers containing a hexadecimal ZIP payload.

Packing is a preprocessing operation. Later AST stages transform the launcher
and its literals. At execution, ordinary files are restored in a temporary
tree so Python imports and __file__-relative resources use standard semantics.
"""
from __future__ import annotations

import io
import os
import re
import tokenize
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


@dataclass(frozen=True)
class HexBundle:
    source: str
    manifest: dict
    entry_module: str | None = None


def make_bundle(files: dict[str, bytes], entry: str, *, cwd: str = "caller",
                module: str | None = None, modes: dict[str, int] | None = None) -> HexBundle:
    if cwd not in {'caller', 'bundle'}:
        raise ValueError("Hex working directory must be 'caller' or 'bundle'")
    if entry not in files:
        raise ValueError(f"Hex entrypoint is not included in the project: {entry}")
    for name in files:
        relative = PurePosixPath(name)
        if (relative.is_absolute() or '..' in relative.parts or '\\' in name
                or not relative.parts or ':' in relative.parts[0]):
            raise ValueError(f"Invalid archive path: {name}")
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, content in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (0o100000 | (modes or {}).get(name, 0o644)) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content, compresslevel=9)
    payload = data.getvalue()
    source = f'''import atexit as _atexit
import io as _io
import os as _os
from pathlib import Path as _Path
import subprocess as _subprocess
import sys as _sys
import tempfile as _tempfile
import zipfile as _zipfile

def _launch():
    _payload = bytes.fromhex({payload.hex()!r})
    _temporary = _tempfile.TemporaryDirectory(prefix='obfuscator-edu-', ignore_cleanup_errors=True)
    _working_directory = _Path.cwd()
    def _cleanup():
        try:
            _os.chdir(_working_directory)
        except OSError:
            _os.chdir(_tempfile.gettempdir())
        _temporary.cleanup()
    _atexit.register(_cleanup)
    _root = _Path(_temporary.name)
    with _zipfile.ZipFile(_io.BytesIO(_payload)) as _archive:
        _archive.extractall(_root)
        if _os.name == 'posix':
            for _member in _archive.infolist():
                _mode = (_member.external_attr >> 16) & 0o777
                (_root / _member.filename).chmod(_mode)
    _entry = _root / {entry!r}
    _paths = ([str(_root)] if {module is not None!r}
              else [str(_entry.parent), str(_root)])
    if _os.environ.get('PYTHONPATH'):
        _paths.append(_os.environ['PYTHONPATH'])
    _environment = dict(_os.environ)
    _environment['PYTHONPATH'] = _os.pathsep.join(_paths)
    _arguments = ([_sys.executable, '-m', {module!r}] if {module is not None!r}
                  else [_sys.executable, str(_entry)])
    _arguments.extend(_sys.argv[1:])
    _completed = _subprocess.run(_arguments,
        cwd=_root if {cwd == 'bundle'!r} else _working_directory,
        env=_environment)
    raise SystemExit(_completed.returncode)

if __name__ == '__main__':
    _launch()
'''
    compile(source, '<hex-launcher>', 'exec')
    return HexBundle(source, {
        "format": "zip+hex", "entry": entry, "entry_module": module, "cwd": cwd,
        "files": sorted(files), "file_count": len(files),
        "source_bytes": sum(map(len, files.values())), "archive_bytes": len(payload),
        "hex_characters": len(payload) * 2,
    }, module)


def bundle_text(source: str, *, cwd: str = "caller") -> HexBundle:
    compile(source, '<hex-input>', 'exec')
    # A Unicode source API has already decoded its encoding cookie. Normalize
    # it before storing UTF-8 bytes; file/folder APIs retain original bytes.
    lines = source.splitlines(keepends=True)
    for index in range(min(2, len(lines))):
        if lines[index].lstrip().startswith('#'):
            lines[index] = re.sub(r'(coding[:=]\s*)[-\w.]+', r'\1utf-8', lines[index])
    return make_bundle({'main.py': ''.join(lines).encode('utf-8')}, 'main.py', cwd=cwd)


def bundle_file(path: Path, *, cwd: str = "caller") -> HexBundle:
    with tokenize.open(path) as stream:
        compile(stream.read(), str(path), 'exec')
    return make_bundle({path.name: path.read_bytes()}, path.name, cwd=cwd,
                       modes={path.name: path.stat().st_mode & 0o777})


def discover_entrypoint(root: Path, skipped: frozenset[str]) -> str:
    """Choose a conventional entrypoint, or fail rather than guess between apps."""
    candidates: list[str] = []
    for directory, directories, names in os.walk(root, followlinks=False):
        directories[:] = sorted(name for name in directories if name not in skipped)
        relative = Path(directory).relative_to(root)
        if "main.py" in names:
            candidates.append((relative / "main.py").as_posix())
        if "__main__.py" in names:
            candidates.append((relative / "__main__.py").as_posix())

    # Root entrypoints are unambiguous and most widely used.
    for preferred in ("main.py", "__main__.py"):
        if preferred in candidates:
            return preferred

    package_mains = sorted(name for name in candidates
                           if name.endswith("/__main__.py"))
    if len(package_mains) == 1:
        return package_mains[0]
    if len(package_mains) > 1:
        raise ValueError("Multiple package entrypoints found; select one with --hex-entry")

    mains = sorted(name for name in candidates if name.endswith("/main.py"))
    if len(mains) == 1:
        return mains[0]
    if len(mains) > 1:
        raise ValueError("Multiple main.py entrypoints found; select one with --hex-entry")

    python_files = []
    for directory, directories, names in os.walk(root, followlinks=False):
        directories[:] = sorted(name for name in directories if name not in skipped)
        python_files.extend((Path(directory) / name).relative_to(root).as_posix()
                            for name in names if Path(name).suffix.lower() == ".py")
    if len(python_files) == 1:
        return python_files[0]
    raise ValueError("Could not determine one application entrypoint; specify it with --hex-entry")


def bundle_project(root: Path, entry: str | None, skipped: frozenset[str], *, cwd: str = "caller",
                   entry_mode: str = "auto") -> HexBundle:
    chosen = Path(entry) if entry is not None else Path(discover_entrypoint(root, skipped))
    entry_path = (root / chosen).resolve()
    if (chosen.is_absolute() or '..' in chosen.parts or root not in entry_path.parents
            or entry_path.suffix.lower() != '.py' or not entry_path.is_file()):
        raise ValueError("Select a relative .py entrypoint with --hex-entry (default: main.py or __main__.py)")
    files, modes = {}, {}
    for directory, directories, names in os.walk(root, followlinks=False):
        directories[:] = sorted(name for name in directories if name not in skipped)
        names = sorted(name for name in names if name not in skipped)
        for name in (*directories, *names):
            path = Path(directory) / name
            if path.is_symlink() or getattr(path, 'is_junction', lambda: False)():
                raise ValueError(f"Hex packing does not support symlinks/junctions: {path}")
        for name in sorted(names):
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            if path.suffix.lower() in {'.pyc', '.pyo'}:
                continue
            if path.suffix.lower() == '.py':
                with tokenize.open(path) as stream:
                    compile(stream.read(), str(path), 'exec')
            files[relative] = path.read_bytes()
            modes[relative] = path.stat().st_mode & 0o777
    relative_entry = entry_path.relative_to(root)
    parts = relative_entry.with_suffix('').parts
    regular_package = len(parts) > 1 and all((root.joinpath(*parts[:length]) / '__init__.py').is_file()
                                           for length in range(1, len(parts)))
    valid_module = all(part.isidentifier() for part in parts)
    namespace_main = relative_entry.name == '__main__.py' and len(parts) > 1 and valid_module
    use_module = entry_mode == 'module' or (entry_mode == 'auto' and valid_module
                                             and (regular_package or namespace_main))
    if use_module and not valid_module:
        raise ValueError("Module entrypoints require Python identifier path components")
    module = '.'.join(parts) if use_module else None
    return make_bundle(files, relative_entry.as_posix(), cwd=cwd, module=module, modes=modes)
