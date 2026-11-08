# ObfuscatorEdu

ObfuscatorEdu is a standard-library-only Python source obfuscation pipeline. It
combines literal encoding, identifier mangling, control-flow transformation,
opaque predicates, junk insertion, import rewriting, and source compaction in
a configurable sequence.

The project targets Python 3.10 or newer. Runtime transformations have no
third-party dependencies. Obfuscation raises the cost of reading and modifying
source code; it is not encryption and does not make embedded secrets safe.

## Installation

Install the project in editable mode during development:

```powershell
python -m pip install -e .
```

This installs the preferred `obfuscator-edu` command. The former
`code-editors` command remains available as a compatibility alias.

You may also run the checked-out project directly:

```powershell
python pipeline.py --help
python -m code_editors --help
```

Both launchers invoke `code_editors.cli.main` directly. The CLI validates
configuration and calls the core engine; `code_editors.pipeline` exists only
for compatibility with older Python API imports.

## Quick start

Transform one file:

```powershell
obfuscator-edu app.py
obfuscator-edu app.py --output app_obfuscated.py --seed 100
```

Transform a complete project while preserving its directory layout:

```powershell
obfuscator-edu --folder src --output src_obfuscated
```

If no input path is supplied, a native file or directory picker is opened.
Tkinter is only required for that picker.

The input is never modified. A file defaults to `<name>_edited.py`; a folder
defaults to `<folder>_processed`. An existing output folder is rejected.

## Strong profile

To permit flow and runtime-import transformation even when the source uses
`locals()`, `globals()`, `vars()` or `dir()`, explicitly add
`--allow-introspection`. This option can be combined with `--all`, but `--all`
does not enable it automatically:

```powershell
python pipeline.py --folder Test --output Test_strong --all --allow-introspection
```

These readers can observe generated dispatcher variables and renamed symbols.
Code that relies on their exact keys or uses returned namespaces to modify
variables can therefore behave differently. The opt-in only relaxes these
namespace-reader checks; dynamic execution, frame inspection and import-hook
checks remain active. The Python API exposes the same option through
`PipelineConfig(allow_introspection=True)`.

Enable the strong source-obfuscation preset with a single option:

```powershell
python pipeline.py --folder Test --output Test_strong --all
python pipeline.py app.py --all
```

`--all` selects runtime imports, polymorphic chained literal encoding, extended function flow, table dispatch,
six-character identifiers and the repeated stage order below. It runs at least
two layers with at least 16 junk blocks per occurrence. Larger explicit
`--layers` and `--junk-count` values are respected; other strength options and
`--stage-order` are overridden by the preset. Input/output paths and `--seed`
continue to work normally. This preset increases output size and startup time;
there is no finite maximum for the number of layers or junk blocks.
Physical module relocation remains an explicit `--rename-modules` choice;
verification, reports and native compilation are also opt-in.

Stage IDs may repeat. Every registered stage must appear at least once:

```powershell
obfuscator-edu --folder Test --output Test_o `
  --layers 1 `
  --import-mode runtime `
  --stage-order imports,junk,xor,name_mangler,function_flow,module_flow,flattener,junk,xor,name_mangler,numeric,cleanup
```

Use `--seed N` for reproducible output. Without a seed, every stage receives a
different cryptographically random 64-bit seed. `--layers N` repeats the whole
configured order and feeds each layer into the next one.

## Stages

Optional layered literal encoding is enabled with `--literal-mode layered`.
It combines XOR, byte rotation, an additive offset, a shared module key and
ciphertext feedback within each payload. Decoding is independent of execution
order and repeated calls. The module key is required in addition to the literal
payload and literal key; all decoding material remains recoverable from the
generated program. It does not provide cryptographic secrecy.

```powershell
python -m code_editors app.py --literal-mode layered --seed 100
```

For dependent literal keys and table-driven dispatch, use:

```powershell
python -m code_editors --folder Test --output Test_strong `
  --import-mode runtime --literal-mode chained --dispatch-mode table `
  --stage-order imports,junk,xor,name_mangler,function_flow,module_flow,flattener,junk,xor,name_mangler,numeric,cleanup
```

`chained` applies the layered codec and stores wrapped keys in a module-local
table. Each key depends on the preceding key and ciphertext. A literal's own
record is insufficient to derive its key; initialization reconstructs the chain
once and retains immutable keys. Decoding does not depend on which literals were
executed previously, so recursion, branches, loops and threads can share it.
Decoded byte values are cached per literal, preventing repeated codec work
inside dispatch loops and successive obfuscation layers.
The complete generated program still contains enough material to recover all
values. Repeated encoders and layers increase output size and initialization
time; use `--seed N` to reproduce a problem.

`--literal-mode polymorphic` extends `chained`: each literal receives a seeded
choice of reversible byte transformations (reversal, indexed addition or XOR)
outside the layered codec. The chain depends on the final encoded bytes.
It supports the same Unicode, binary, recursion and thread behavior as `chained`.

`--extended-flow` additionally transforms eligible generators, async generators
and coroutines. Yield, await, async-for and async-with remain in their original
function and lexical scope. Exception handlers and context-manager suites are
kept together so cleanup and cancellation retain their meaning. Decorated
functions and introspection-sensitive bodies can still be skipped.

## Optional project layout, reports and verification

```powershell
python -m code_editors --folder Test -o Test_protected --all `
  --rename-modules --report Test_protected.report.json `
  --verify-entry main.py --verify-stdin sample_input.txt --seed 42
```

`--rename-modules` randomizes physical Python filenames and package directories.
An encoded importlib loader retains logical module names, including relative
and dynamic imports. Package resources move with their package. Conventional
entrypoint filenames, standalone script directories and paths referenced by
literal filesystem strings are retained for compatibility. The report records
every Python path, including paths retained unchanged. Arbitrarily computed
filesystem paths, custom import hooks and source-layout path manipulation still
require application-specific verification. Select the import/source root as
the input directory. Project packaging metadata and external launch commands
are not rewritten automatically.

Launch a retained entrypoint normally. When using the output as a library,
import the generated `obfuscator_bootstrap` module before importing project
modules. Namespace packages and `importlib.resources` are supported. Native
extension compilation and physical module relocation are separate build modes.

`--report PATH` writes JSON outside the input/output trees: configuration,
per-file stage seeds and descriptions, protected names/members, input-function
eligibility with skip reasons, module path mappings, native artifacts and
verification results. Input eligibility describes the original source; stage
counts describe actual transformations. Keep reports private because they
reveal original names and paths.

Verification is explicit: `--verify` for a single file or `--verify-entry`
with a relative `.py` path for a folder. It **executes both programs**, compares
exit status, stdout and stderr, and fails on a mismatch, unsuccessful exit or
timeout. Use `--verify-stdin` to supply a UTF-8 input file and
`--verify-timeout SECONDS` to change the default 30-second limit per process.
Each program runs in its entrypoint's directory. Programs that print random
values, timestamps or their own filenames need a deterministic test harness.
This check covers the selected execution, not every possible application path.
Folder output is removed on failure; single-file output is replaced only after
successful verification. Reports are emitted for successful builds.

## Optional native modules

```powershell
python -m pip install -e ".[native]"
python -m code_editors --folder MyProject -o MyProject_native --all `
  --native-module package/core.py --report native.report.json
```

Repeat `--native-module` to select ordinary modules to compile with Nuitka.
Compilation requires a compatible C compiler, as described in the
[Nuitka manual](https://nuitka.net/user-documentation/user-manual.html).
The generated extension retains its module basename; source is removed only
after the compiler succeeds and the extension exists. Entry scripts and
package initializers are excluded. No compiler is downloaded automatically by
ObfuscatorEdu, and native compilation is not included in `--all`. Distribute
native output for the matching platform and Python ABI. Private CPython
code-object behavior can differ in compiled functions.

`table` dispatch uses a randomized state-to-label table and `match` cases in the
same scope as the original statements. It does not extract blocks into callable
closures. Function and module dispatchers both support it, including opaque
predicate guards. Original user conditions inside states remain intact.

Decoder arithmetic, state labels, state order and predicates vary with stage
seeds. Normal runs choose fresh seeds; specifying a seed makes output repeatable.
The existing defaults (`xor` and `branches`) remain available for comparison.
No transformer can promise identical behavior for every Python program,
especially reflection and code-object inspection; safety checks keep skipping
unsupported cases.

| ID | Transformation | Main safety behavior |
|---|---|---|
| `junk` | Inserts unreachable blocks | Uses fresh identifiers and false guards |
| `imports` | Rewrites imports | `aliases` preserves bytecode; `runtime` hides supported paths |
| `xor` | Encodes text and byte literals | Uses a distinct derived key for every literal |
| `function_flow` | Builds dispatchers inside eligible functions | Skips generators, async code, decorators, and introspection |
| `module_flow` | Builds a module-level state machine | Skips modules with constructs rejected by the safety visitor |
| `flattener` | Adds opaque predicates | Runs only after successful module flow transformation |
| `name_mangler` | Renames identifiers and class fields | Coordinates project-wide imports and field names |
| `numeric` | Rewrites integer spelling | Verifies that the parsed AST is unchanged |
| `cleanup` | Removes comments/docstrings and compacts tokens | Recompiles output before returning it |

The default order is defined by `DEFAULT_STAGE_ORDER` in
`code_editors/core/configuration.py`.

### Import modes

`aliases` is the conservative default. It adds redundant explicit aliases only
when the compiled instruction stream remains identical.

`runtime` encodes supported module paths and loads them through a generated
helper. Future imports, star imports, import-hook manipulation, and
scope-introspection cases remain unchanged. Multi-name `from` imports are
expanded into separate runtime-loaded bindings, with each symbol encoded
independently. This mode is more obscure but can affect software that depends
on custom import hooks or import
call-stack details.

Generated helpers use randomized local reader names instead of a fixed
`decode` identifier. Text conversion uses a captured string constructor rather
than a visible `.decode(...)` method. The character-conversion builtin is
resolved from a masked byte payload instead of a plain `chr` reference; chained
literal encoding also protects that payload. These changes hide fixed textual
signatures without introducing dynamic source execution. Original application
uses of these builtins and methods are preserved.

## Python API

The configuration-based API is recommended for integrations:

```python
from code_editors import PipelineConfig, transform

config = PipelineConfig(seed=42, junk_count=4, import_mode="runtime")
result, report = transform('message = "hello"\n', config)
```

For project trees:

```python
from pathlib import Path

from code_editors import PipelineConfig, process_folder

config = PipelineConfig(layers=2, seed=42)
process_folder(Path("src"), Path("src_obfuscated"), config)
```

The original argument-based functions remain available from
`code_editors.pipeline` for backward compatibility.

## Architecture

```text
code_editors/
  cli.py                  command-line parsing and user interaction
  pipeline.py             compatibility facade for the previous API
  core/
    configuration.py      validated immutable settings
    context.py            per-run state and seed allocation
    engine.py             ordered stage and layer execution
    filesystem.py         file/folder processing and project analysis
    project_layout.py     physical module relocation and importlib loader
    artifacts.py          reports, differential execution, native backend
    registry.py           stage adapters and immutable registry
  stages/                 independent source transformations
tests/                    unit, regression, and integration tests
docs/architecture.md      extension contracts and data flow
```

See [docs/architecture.md](docs/architecture.md) before adding or reordering a
stage.

## Development

Run all tests:

```powershell
python -m unittest discover -s tests -v
```

Check that every module compiles:

```powershell
python -m compileall -q code_editors tests pipeline.py
```

`Test` and `Password-Generator` are optional local development fixtures and
are not included in the repository or installed package. The core test suite
creates its own fixtures and runs without them. The Qt example check also
requires the example application's dependencies.

If the local NavigoGrade example is available in `Test`, run its test suite,
obfuscate it with the strong runtime-import profile, and compare the original
and transformed application:

```powershell
python scripts/verify_example.py Test
```

To verify both new modes on the example, use:

```powershell
python scripts/verify_example.py Test --literal-mode chained --dispatch-mode table
python scripts/verify_example.py Test --literal-mode polymorphic --dispatch-mode table --extended-flow --rename-modules
python scripts/verify_qt_example.py --rename-modules
python scripts/verify_example.py Test --all --rename-modules
```

The verification scripts create temporary output and remove it after the check.

The native integration test is optional because it requires Nuitka and a C
compiler. Enable it with `$env:OBFUSCATOR_TEST_NATIVE='1'` before running the
test suite. Adapter failure/success handling is covered without a compiler.
See [the validation record](docs/validation.md) for tested scenarios and the
boundary between original-application tests and generated-output checks.

Contribution requirements and the validation checklist are documented in
[CONTRIBUTING.md](CONTRIBUTING.md).

## Known boundaries

- Generated decoder keys and encoded payloads coexist in the output and can be
  recovered at runtime.
- Reflection, serialization by field name, plugin discovery, custom import
  hooks, and code-object inspection can observe or depend on transformed names.
- The pipeline deliberately skips unsafe constructs instead of guessing.
- Always test obfuscated output with the same supported Python versions as the
  original application.

## License

MIT. See [LICENSE](LICENSE).
