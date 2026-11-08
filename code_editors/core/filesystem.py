"""Safe file and directory orchestration for the transformation engine."""

from __future__ import annotations

import ast
import random
import shutil
import tokenize
import tempfile
from pathlib import Path

from ..stages import name_mangler, import_obfuscator, function_flow
from ..stages.scopes import project_calls
from .configuration import PipelineConfig
from .context import ProgressCallback, SeedAllocator, StageContext
from .engine import transform_layers
from .registry import STAGE_REGISTRY
from .artifacts import (check_native_dependencies, compile_native_module,
                        project_entry, validate_report_path, verify_programs, write_report)
from .project_layout import plan_layout, relocated_path, inject_loader, loader_source

SKIPPED_DIRECTORIES = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
        "venv",
    }
)


def read_python_file(path: Path) -> str:
    """Read Python source using its declared PEP 263 encoding."""
    with tokenize.open(str(path)) as source_file:
        return source_file.read()


def iter_python_files(root: Path):
    """Yield source files in stable order while excluding generated trees."""
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root)
        if not any(part in SKIPPED_DIRECTORIES for part in relative.parts):
            yield path


def collect_imported_names(paths: list[Path]) -> set[str]:
    """Collect names whose spelling forms a cross-module contract."""
    names: set[str] = set()
    for path in paths:
        tree = ast.parse(read_python_file(path), filename=str(path))
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


def collect_keyword_names(paths: list[Path]) -> set[str]:
    """Collect keyword spellings that form call contracts across modules."""
    names: set[str] = set()
    for path in paths:
        tree = ast.parse(read_python_file(path), filename=str(path))
        names.update(
            node.arg
            for node in ast.walk(tree)
            if isinstance(node, ast.keyword) and node.arg is not None
        )
    return names


def collect_string_constants(paths: list[Path]) -> set[str]:
    """Collect string values that may act as reflective member names."""
    names: set[str] = set()
    for path in paths:
        tree = ast.parse(read_python_file(path), filename=str(path))
        names.update(
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        )
    return names


def collect_ambiguous_attribute_names(
    paths: list[Path], source_folder: Path, project_modules: set[str]
) -> set[str]:
    """Find members also accessed through receivers outside local class bodies."""
    ambiguous: set[str] = set()
    for path in paths:
        tree = ast.parse(read_python_file(path), filename=str(path))
        classes = [node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
        local_classes = {node.name for node in classes}
        visitor = name_mangler.ModuleDefinitions()
        visitor.visit(tree)
        internal_callables = visitor.names | visitor.attribute_names
        internal_callables.update(
            collect_project_imports(path, source_folder, project_modules)
        )
        parents = {
            child: parent
            for parent in ast.walk(tree)
            for child in ast.iter_child_nodes(parent)
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute):
                continue
            if (
                isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                and node.value.func.id in internal_callables
            ):
                continue
            owner = parents.get(node)
            while owner is not None and not isinstance(owner, ast.ClassDef):
                owner = parents.get(owner)
            receiver_name = node.value.id if isinstance(node.value, ast.Name) else None
            instance_names: set[str] = set()
            external_base = False
            declared: set[str] = set()
            if owner is not None:
                external_base = any(
                    not isinstance(base, ast.Name) or base.id not in local_classes
                    for base in owner.bases
                )
                methods = [
                    child for child in owner.body
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                ]
                instance_names = {
                    method.args.args[0].arg
                    for method in methods
                    if method.args.args
                    and not any(
                        isinstance(decorator, ast.Name)
                        and decorator.id == "staticmethod"
                        for decorator in method.decorator_list
                    )
                }
                declared.update(method.name for method in methods)
                class_nodes = [owner, *ast.walk(owner)]
                for child in class_nodes:
                    if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store):
                        declared.add(child.id)
                    elif (
                        isinstance(child, ast.Attribute)
                        and isinstance(child.ctx, ast.Store)
                        and isinstance(child.value, ast.Name)
                        and child.value.id in instance_names
                    ):
                        declared.add(child.attr)
            if receiver_name not in instance_names:
                ambiguous.add(node.attr)
            elif external_base and node.attr not in declared:
                ambiguous.add(node.attr)
    return ambiguous


def collect_project_imports(
    path: Path, source_folder: Path, project_modules: set[str]
) -> set[str]:
    """Return imported symbols that resolve to modules in this source tree."""
    tree = ast.parse(read_python_file(path), filename=str(path))
    relative = path.relative_to(source_folder)
    package_parts = relative.parent.parts
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level:
            anchor = package_parts[: max(0, len(package_parts) - node.level + 1)]
            module_parts = anchor + tuple((node.module or "").split("."))
        else:
            module_parts = tuple((node.module or "").split("."))
        module_name = ".".join(part for part in module_parts if part)
        if not node.level and module_name not in project_modules:
            # A directly executed nested script imports siblings from its own
            # sys.path[0], rather than through the project package prefix.
            sibling = path.parent.joinpath(*module_parts)
            if sibling.with_suffix(".py").is_file() or (sibling / "__init__.py").is_file():
                module_name = ".".join((*package_parts, *module_parts))
        if module_name not in project_modules:
            continue
        names.update(
            alias.asname or alias.name
            for alias in node.names
            if alias.name != "*"
        )
    return names


def process_file(
    source_path: Path,
    output_path: Path | None,
    config: PipelineConfig,
    *,
    progress: ProgressCallback | None = None,
) -> list[str]:
    """Transform one Python file and write a separate UTF-8 output file."""
    source_path = source_path.expanduser().resolve()
    if not source_path.is_file() or source_path.suffix.lower() != ".py":
        raise ValueError("Input must be an existing .py file")
    destination = (
        source_path.with_name(f"{source_path.stem}_edited.py")
        if output_path is None
        else output_path.expanduser().resolve()
    )
    if destination == source_path:
        raise ValueError("The output file must be different from the input file")
    if config.rename_modules or config.native_modules:
        raise ValueError("Module relocation and native compilation require folder mode")
    validate_report_path(config, source_path, destination)

    transformed, report = transform_layers(
        read_python_file(source_path),
        config,
        progress=progress,
        progress_label=source_path.name,
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    compile(transformed, str(destination), "exec")
    verification = None
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="",
            prefix=f".{destination.stem}-", suffix=".py", dir=destination.parent,
            delete=False) as temporary:
        pending = Path(temporary.name)
        temporary.write(transformed)
    try:
        if config.verify_entry:
            verification = verify_programs(source_path, pending, config)
        pending.replace(destination)
    finally:
        pending.unlink(missing_ok=True)
    write_report(config, destination, report, verification=verification,
        input_function_coverage=function_flow.assess_functions(read_python_file(source_path),
            allow_introspection=config.allow_introspection, extended=config.extended_flow)
            if config.report_path else [])
    return [f"Saved: {destination}", *report]


def process_folder(
    source_folder: Path,
    output_folder: Path | None,
    config: PipelineConfig,
    *,
    progress: ProgressCallback | None = None,
) -> list[str]:
    """Transform a source tree atomically from the caller's perspective."""
    source_folder = source_folder.expanduser().resolve()
    if not source_folder.is_dir():
        raise ValueError("Input must be an existing directory")
    destination_root = (
        source_folder.with_name(source_folder.name + "_processed")
        if output_folder is None
        else output_folder.expanduser().resolve()
    )
    if destination_root == source_folder or source_folder in destination_root.parents:
        raise ValueError("The output directory must be outside the input directory")
    if destination_root.exists():
        raise FileExistsError(f"Output directory already exists: {destination_root}")

    python_files = list(iter_python_files(source_folder))
    if not python_files:
        raise ValueError("No Python files were found in the input directory")
    validate_report_path(config, source_folder, destination_root)
    entry = project_entry(source_folder, config.verify_entry) if config.verify_entry else None
    native_paths = [project_entry(source_folder, item).relative_to(source_folder)
                    for item in config.native_modules]
    if ((entry is not None and entry not in python_files)
            or any(source_folder / path not in python_files for path in native_paths)):
        raise ValueError("Entrypoints and native modules must belong to the included Python source files")
    if any(path.name in {"__init__.py", "__main__.py", "main.py"} for path in native_paths):
        raise ValueError("Native compilation requires ordinary modules, not entrypoints/packages")
    check_native_dependencies(config.native_modules)
    if config.rename_modules and (source_folder / "obfuscator_bootstrap.py").exists():
        raise ValueError("Input already contains the reserved obfuscator_bootstrap.py")
    keyword_names = collect_keyword_names(python_files)
    string_constants = collect_string_constants(python_files)
    definitions: set[str] = set()
    local_callables: dict[Path, set[str]] = {}
    exported_names: set[str] = set()
    for path in python_files:
        tree = ast.parse(read_python_file(path), filename=str(path))
        visitor = name_mangler.ModuleDefinitions()
        visitor.visit(tree)
        definitions.update(visitor.names)
        definitions.update(visitor.attribute_names)
        local_callables[path] = {node.name for node in ast.walk(tree)
                                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
        for node in tree.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if any(isinstance(target, ast.Name) and target.id == "__all__" for target in targets):
                    value = node.value
                    if isinstance(value, (ast.List, ast.Tuple, ast.Set)):
                        exported_names.update(
                            item.value for item in value.elts
                            if isinstance(item, ast.Constant) and isinstance(item.value, str)
                        )
    all_project_identifiers = set(definitions)
    project_modules = {
        ".".join(path.relative_to(source_folder).with_suffix("").parts)
        if path.name != "__init__.py"
        else ".".join(path.relative_to(source_folder).parent.parts)
        for path in python_files
    }
    class_attribute_names = name_mangler.collect_class_attribute_names(python_files)
    reflective_attribute_names = class_attribute_names & (
        string_constants
        | collect_ambiguous_attribute_names(python_files, source_folder, project_modules)
    )
    definitions.difference_update(
        exported_names | keyword_names | reflective_attribute_names
    )
    internal_keyword_names: set[str] = set()
    callable_names_by_path: dict[Path, set[str]] = {}
    for path in python_files:
        tree = ast.parse(read_python_file(path), filename=str(path))
        callable_names = local_callables[path] | collect_project_imports(
            path, source_folder, project_modules
        )
        callable_names_by_path[path] = callable_names
        for node in project_calls(tree, callable_names):
            if isinstance(node.func, ast.Name):
                callable_name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                callable_name = node.func.attr
            else:
                continue
            if callable_name in callable_names:
                internal_keyword_names.update(
                    keyword.arg for keyword in node.keywords if keyword.arg is not None
                )
    internal_keyword_names.difference_update(exported_names)
    internal_keyword_names.difference_update(reflective_attribute_names)
    definitions.update(internal_keyword_names)
    shared_name_mapping = name_mangler.make_mapping(
        {name for name in definitions
         if name not in name_mangler.BUILTIN_NAMES
         and name not in exported_names
         and not (name.startswith("__") and name.endswith("__"))},
        config.name_length,
        random.Random(config.seed),
    )
    folder_protected_names = exported_names | (
        all_project_identifiers - set(shared_name_mapping)
    )
    attribute_names = name_mangler.collect_class_attribute_names(
        python_files,
        protected_names=(keyword_names - internal_keyword_names)
        | exported_names
        | reflective_attribute_names,
    )
    attribute_mapping = {
        name: shared_name_mapping[name]
        for name in attribute_names
        if name in shared_name_mapping
    }

    destination_root.mkdir(parents=True)
    sources = {path: read_python_file(path) for path in python_files}
    function_coverage = ({path.relative_to(source_folder).as_posix():
        function_flow.assess_functions(source, allow_introspection=config.allow_introspection,
                                      extended=config.extended_flow)
        for path, source in sources.items()} if config.report_path else {})
    allocator = SeedAllocator(config.seed)
    stage_records = []
    layout = {}
    directories = {}
    verification = None
    native_artifacts = []
    try:
        for layer in range(1, config.layers + 1):
            occurrences: dict[str, int] = {}
            contexts = {
                path: StageContext(
                    stage_seed=0,
                    junk_count=config.junk_count,
                    name_length=config.name_length,
                    protected_names=folder_protected_names,
                    attribute_mapping=attribute_mapping,
                    shared_name_mapping=shared_name_mapping,
                    project_modules=project_modules | {
                        child.stem for child in path.parent.glob("*.py")
                        if child.name != "__init__.py"
                    } | {
                        child.name for child in path.parent.iterdir()
                        if child.is_dir() and (child / "__init__.py").is_file()
                    },
                    project_keyword_names=internal_keyword_names,
                    project_callable_names=callable_names_by_path[path] | {
                        shared_name_mapping.get(name, name) for name in callable_names_by_path[path]
                    },
                    import_mode=config.import_mode,
                    literal_mode=config.literal_mode,
                    dispatch_mode=config.dispatch_mode,
                    allow_introspection=config.allow_introspection,
                    extended_flow=config.extended_flow,
                ) for path in python_files
            }
            for stage_index, stage_id in enumerate(config.stage_order, 1):
                occurrences[stage_id] = occurrences.get(stage_id, 0) + 1
                for source_path in python_files:
                    relative_name = source_path.relative_to(source_folder).as_posix()
                    seed = allocator.next_seed()
                    context = contexts[source_path]
                    context.stage_seed = seed
                    context.current_stage_occurrence = occurrences[stage_id]
                    prefix = (
                        f"{relative_name} | Layer {layer}/{config.layers} | "
                        f"Stage {stage_index}/{len(config.stage_order)}: {stage_id} "
                        f"({occurrences[stage_id]}) seed={seed}"
                    )
                    if progress:
                        progress(f"{prefix} — started")
                    try:
                        sources[source_path], description = STAGE_REGISTRY[stage_id](
                            sources[source_path], context
                        )
                    except Exception as exc:
                        if progress:
                            progress(f"{prefix} — failed: {type(exc).__name__}: {exc}")
                        raise
                    if progress:
                        progress(f"{prefix} — completed: {description}")
                    stage_records.append({"file": relative_name, "layer": layer,
                                          "stage": stage_id, "seed": seed,
                                          "description": description})

        # A shared project map is applied as a final consistency pass. This
        # catches references in modules that do not bind a name locally and
        # ensures generated import bindings and their call sites stay aligned.
        for source_path, source in sources.items():
            source = import_obfuscator.rewrite_project_imports(source, shared_name_mapping,
                contexts[source_path].project_modules or set())
            edits = name_mangler.python_edits(
                source, shared_name_mapping, attribute_mapping, internal_keyword_names,
                callable_names_by_path[source_path],
            )
            sources[source_path] = name_mangler.apply_edits(source, edits)
            compile(sources[source_path], str(source_path), "exec")

        if config.rename_modules:
            layout, directories, modules, runtime_name, install_name = plan_layout(
                python_files, source_folder, config.seed)
            for source_path in sources:
                sources[source_path] = inject_loader(sources[source_path],
                    layout[source_path.relative_to(source_folder)], runtime_name, install_name)
            runtime_source = loader_source(modules, install_name, config.seed)
            compile(runtime_source, runtime_name, "exec")
            (destination_root / (runtime_name + ".py")).write_text(runtime_source, encoding="utf-8")
            (destination_root / "obfuscator_bootstrap.py").write_text(
                f"from {runtime_name} import {install_name} as install\ninstall()\n", encoding="utf-8")

        for source_path in sorted(source_folder.rglob("*")):
            relative = source_path.relative_to(source_folder)
            if any(part in SKIPPED_DIRECTORIES for part in relative.parts):
                continue
            destination = destination_root / (relocated_path(relative, layout, directories)
                                              if config.rename_modules else relative)
            if source_path.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
            elif source_path.suffix.lower() == ".py":
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(sources[source_path], encoding="utf-8", newline="")
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_path, destination)
        for relative in dict.fromkeys(native_paths):
            if progress:
                progress(f"Compiling native module: {relative}")
            native_artifacts.append(str(compile_native_module(destination_root / relative).relative_to(destination_root)))
        if entry:
            relative = entry.relative_to(source_folder)
            verification = verify_programs(entry, destination_root / layout.get(relative, relative), config)
        write_report(config, destination_root, stage_records,
            protected_names=sorted(folder_protected_names),
            protected_members=sorted(reflective_attribute_names),
            protection_reason="Reflection, ambiguous/external receivers, exports or keyword contracts",
            module_paths={str(key): str(value) for key, value in layout.items()},
            input_function_coverage=function_coverage,
            verification=verification, native_artifacts=native_artifacts)
    except Exception:
        shutil.rmtree(destination_root, ignore_errors=True)
        raise

    return [
        f"Saved folder: {destination_root}",
        f"Transformed Python files: {len(python_files)}",
        f"Layers per file: {config.layers}",
        "Copied other files unchanged.",
    ]
