# Architecture

ObfuscatorEdu separates source transformations from orchestration. A stage only
receives source text and a `StageContext`; it does not select files, parse CLI
arguments, or write output. The engine owns ordering and seeds. The filesystem
layer owns project-wide analysis and output paths.

## Data flow

```text
CLI / Python API
      |
      v
PipelineConfig validation
      |
      v
filesystem project analysis
  - protected imported names
  - cross-module keyword-call contracts
  - shared class-field mapping
      |
      v
engine: layer -> ordered stages -> report
      |
      v
syntax/compile checks in stages
      |
      v
separate output file or tree
```

## Module responsibilities

### `configuration.py`

Defines the immutable `PipelineConfig`, registered stage IDs, the default order,
and validation. Configuration errors must fail before output is created.

### `context.py`

Contains mutable state that belongs to one pipeline pass. `SeedAllocator`
guarantees a distinct seed for every stage occurrence, layer, and project file.

### `registry.py`

Adapts independent transformations to the common signature:

```python
def stage(source: str, context: StageContext) -> tuple[str, str]:
    ...
    return transformed_source, report_message
```

The exported registry is immutable. Stage adapters perform syntax or compile
checks appropriate to their transformation.

### `engine.py`

Runs stages in the configured order, maintains occurrence counts, emits progress
events, and builds a deterministic report. It never reads or writes files.

### `filesystem.py`

Validates paths, respects source encodings, excludes generated directories,
collects cross-module contracts, and writes a separate output tree. If folder
processing fails, its incomplete output is removed.

### `pipeline.py`

Preserves the pre-2.0 argument-based API. New code should import the
configuration-based API from `code_editors` or `code_editors.core`.

### `project_layout.py` and `artifacts.py`

Layout planning is separate from AST transformations. It maps logical modules
to physical paths, relocates sibling resources, and installs an encoded
importlib finder through generated entrypoint bootstraps. Logical module
identities remain stable; conventional entrypoints and literal filesystem
contracts retain their paths. A public bootstrap supports library consumers.

Artifacts owns explicit differential subprocess execution, JSON reports and
the optional Nuitka subprocess adapter. Dependency and path validation precede
folder output creation. A failed build removes its new output tree. File
output is staged beside the destination and atomically replaced after explicit
verification succeeds. Native source removal follows successful compilation
and artifact detection. Compiler availability is an external requirement.

### `stages/scopes.py`

Project keyword rewriting checks lexical bindings, including parameters,
closures and comprehensions. A local binding can shadow a project callable;
external call keyword spellings then remain unchanged. Retained from-imports
receive a final consistency rewrite for shared project symbols.

Function dispatch leaves generators and coroutines in their original scope.
The optional extended mode preserves suspension points and treats exception
and context-manager suites as atomic dispatcher statements. Per-literal codec
variants are reversible before the layered decoder and use immutable chained
keys, allowing arbitrary read order and concurrent calls.

## Adding a stage

1. Implement the transformation in `code_editors/stages/` without filesystem
   side effects.
2. Add a small adapter to `core/registry.py`.
3. Add its ID to `REGISTERED_STAGE_IDS` and place it in the default order.
4. Add focused semantic-equivalence and safety tests.
5. Document the stage, its guarantees, and its skip conditions in the README.

A stage must return valid Python or raise a descriptive exception. When a
construct cannot be transformed safely, prefer an explicit skip result over a
partial transformation.

## Transformation contracts

- **Semantic preservation:** transformed code must retain observable behavior
  for supported input.
- **Project consistency:** imports and class-field spellings shared across files
  use project-wide analysis and mappings.
- **Determinism:** the same source, configuration, stage order, and seed produce
  the same result.
- **Isolation:** input files are never overwritten by the pipeline.
- **Validation:** AST-preserving stages compare trees; generated code is parsed
  or compiled before it is returned.
- **Conservative fallback:** reflection and other unsafe constructs are skipped.

## Security model

This project performs source obfuscation. It is useful for discouraging casual
inspection and raising reverse-engineering cost. It does not provide secrecy,
tamper resistance, sandboxing, or cryptographic protection of embedded data.
