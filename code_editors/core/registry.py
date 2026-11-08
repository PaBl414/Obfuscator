"""Stage adapters and the immutable transformation registry."""

from __future__ import annotations

import ast
import io
import random
import tokenize
from collections import defaultdict, deque
from collections.abc import Callable, Mapping
from types import MappingProxyType

from ..stages import cleanup, flattener, flow_obfuscator, function_flow
from ..stages import import_obfuscator, junk_editor, name_mangler, numeric_obfuscator
from ..stages import xor_encryption
from ..stages.source_edits import SourceIndex, apply_edits
from .context import StageContext

StageHandler = Callable[[str, StageContext], tuple[str, str]]


def preserve_integer_spellings(original: str, rewritten: str) -> str:
    """Carry equivalent integer spellings through AST-unparse stages."""
    original_tree = ast.parse(original, type_comments=True)
    original_index = SourceIndex(original)
    spellings: dict[int, deque[str]] = defaultdict(deque)
    for node in ast.walk(original_tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, int)
            and not isinstance(node.value, bool)
        ):
            spelling = original_index.segment(node)
            if spelling is not None:
                spellings[node.value].append(spelling)

    if not spellings:
        return rewritten

    tree = ast.parse(rewritten, type_comments=True)
    rewritten_index = SourceIndex(rewritten)
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
        edits.append(
            (
                rewritten_index.offset(node.lineno, node.col_offset),
                rewritten_index.offset(node.end_lineno, node.end_col_offset),
                spelling,
            )
        )

    restored = apply_edits(rewritten, edits)
    if ast.dump(tree, include_attributes=False) != ast.dump(
        ast.parse(restored, type_comments=True), include_attributes=False
    ):
        raise ValueError("Preserving integer spellings changed the syntax tree")
    return restored


def mangle_source(
    source: str,
    length: int,
    rng: random.Random,
    protected_names: set[str] | None = None,
    attribute_mapping: dict[str, str] | None = None,
    shared_name_mapping: dict[str, str] | None = None,
    keyword_names: set[str] | None = None,
    internal_callable_names: set[str] | None = None,
) -> tuple[str, int]:
    """Rename identifiers while keeping project-wide attributes consistent."""
    tree = ast.parse(source)
    visitor = name_mangler.ModuleDefinitions()
    visitor.visit(tree)
    reflective_names = visitor.attribute_names & {
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    generated_attributes = set(attribute_mapping.values()) if attribute_mapping else set()
    generated_names = set(shared_name_mapping.values()) if shared_name_mapping else set()
    candidates = {
        name
        for name in visitor.names
        if name not in visitor.protected
        and name not in (protected_names or set())
        and name not in reflective_names
        and name not in generated_attributes
        and name not in generated_names
        and name not in name_mangler.BUILTIN_NAMES
        and not (name.startswith("__") and name.endswith("__"))
        and name.isidentifier()
    }
    mapping = name_mangler.make_mapping(
        candidates, length, rng, reserved=generated_attributes | generated_names
    )
    if shared_name_mapping is not None:
        mapping.update({name: value for name, value in shared_name_mapping.items()
                        if name in visitor.names or name in (keyword_names or set())})
    if attribute_mapping is None:
        attributes = {
            name
            for name in visitor.attribute_names
            if name not in name_mangler.BUILTIN_NAMES
            and name not in reflective_names
            and name not in (protected_names or set())
            and not (name.startswith("__") and name.endswith("__"))
        }
        attribute_mapping = name_mangler.make_mapping(
            attributes, length, rng, reserved=set(mapping.values())
        )
    rewritten = name_mangler.apply_edits(
        source, name_mangler.python_edits(
            source, mapping, attribute_mapping, keyword_names,
            internal_callable_names,
        )
    )
    compile(rewritten, "<pipeline-name_mangler>", "exec")
    return rewritten, len(mapping) + len(attribute_mapping)


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


def _junk(source: str, context: StageContext) -> tuple[str, str]:
    source, count = junk_editor.inject_junk(
        source, count=context.junk_count, seed=context.stage_seed
    )
    return source, f"Junk editor: inserted {count} blocks"


def _imports(source: str, context: StageContext) -> tuple[str, str]:
    if context.import_mode == "runtime":
        source, count, skipped = import_obfuscator.obfuscate_imports_runtime(
            source,
            seed=context.stage_seed,
            name_mapping=context.shared_name_mapping,
            project_modules=context.project_modules,
            allow_introspection=context.allow_introspection,
        )
        return source, (
            f"Import obfuscation (runtime): hid {count} imports; "
            f"left {skipped} unchanged"
        )
    source, count = import_obfuscator.obfuscate_imports(source)
    return source, f"Import obfuscation: added explicit aliases to {count} imports"


def _xor(source: str, context: StageContext) -> tuple[str, str]:
    key_rng = random.Random(context.stage_seed)
    key = bytes(key_rng.randrange(256) for _ in range(16))
    decoder_names = _xor_decoder_names(source, context.current_stage_occurrence)
    if context.literal_mode in {"chained", "polymorphic"}:
        source, count = xor_encryption.obfuscate_chained_literals(
            source, key, decoder_names, polymorphic=context.literal_mode == "polymorphic"
        )
        label = "Polymorphic chained" if context.literal_mode == "polymorphic" else "Chained"
        return source, f"{label} encoding: encoded {count} literals with dependent keys"
    encrypted, tree = xor_encryption.transform_source(
        source, key, decoder_names=decoder_names, mode=context.literal_mode
    )
    insert_at = xor_encryption.find_insert_line(tree, source)
    lines = encrypted.split("\n")
    source = "\n".join(
        lines[:insert_at]
        + xor_encryption.build_decryptor(key, decoder_names=decoder_names, mode=context.literal_mode)
        + lines[insert_at:]
    )
    compile(source, "<pipeline-xor>", "exec")
    return source, "XOR encryption: encrypted literals with per-literal keys"


def _function_flow(source: str, context: StageContext) -> tuple[str, str]:
    original = source
    source, count = function_flow.flatten_simple_functions(
        source, seed=context.stage_seed, dispatch_mode=context.dispatch_mode,
        allow_introspection=context.allow_introspection,
        extended=context.extended_flow,
    )
    return (
        preserve_integer_spellings(original, source),
        f"Function flow: flattened {count} simple functions",
    )


def _module_flow(source: str, context: StageContext) -> tuple[str, str]:
    original = source
    try:
        source = flow_obfuscator.obfuscate(
            source, seed=context.stage_seed, dispatch_mode=context.dispatch_mode,
            allow_introspection=context.allow_introspection,
        )
    except flow_obfuscator.UnsafeToTransform as exc:
        context.module_flow_succeeded = False
        details = "; ".join(
            line.removeprefix("  - ") for line in str(exc).splitlines()[1:]
        )
        return source, f"Flow obfuscator: skipped ({details})"
    context.module_flow_succeeded = True
    return (
        preserve_integer_spellings(original, source),
        "Flow obfuscator: transformed module control flow",
    )


def _flattener(source: str, context: StageContext) -> tuple[str, str]:
    if not context.module_flow_succeeded:
        return source, "Flattener: skipped (requires flow obfuscation)"
    source, count = flattener.add_opaque_predicates(source, seed=context.stage_seed)
    return source, f"Flattener: added {count} predicates"


def _name_mangler(source: str, context: StageContext) -> tuple[str, str]:
    tree = ast.parse(source)
    has_exports = any(
        isinstance(node, ast.Name) and node.id == "__all__" for node in ast.walk(tree)
    )
    if has_exports and context.shared_name_mapping is None:
        return source, "Name mangler: skipped (module uses __all__)"
    source, count = mangle_source(
        source,
        context.name_length,
        random.Random(context.stage_seed),
        context.protected_names,
        context.attribute_mapping,
        context.shared_name_mapping,
        context.project_keyword_names,
        context.project_callable_names,
    )
    return source, f"Name mangler: renamed {count} identifiers"


def _cleanup(source: str, context: StageContext) -> tuple[str, str]:
    del context
    source = preserve_integer_spellings(source, cleanup.process(source))
    compile(source, "<pipeline-cleanup>", "exec")
    return source, "Cleanup: removed comments and compacted formatting"


def _numeric(source: str, context: StageContext) -> tuple[str, str]:
    source, count = numeric_obfuscator.obfuscate_numeric_constants(
        source, seed=context.stage_seed
    )
    return source, f"Numeric obfuscation: rewrote {count} integer literals"


STAGE_REGISTRY: Mapping[str, StageHandler] = MappingProxyType(
    {
        "junk": _junk,
        "imports": _imports,
        "xor": _xor,
        "function_flow": _function_flow,
        "module_flow": _module_flow,
        "flattener": _flattener,
        "name_mangler": _name_mangler,
        "cleanup": _cleanup,
        "numeric": _numeric,
    }
)
