# Validation record

Validated on 2026-11-08 using CPython 3.12.10, Windows AMD64.

| Check | Result |
| --- | --- |
| `python -m unittest discover -s tests -q` | 69 tests: 68 passed, 1 optional native integration test skipped |
| `python -m compileall -q code_editors tests scripts pipeline.py` | Passed |
| Original Password-Generator pytest suite, offscreen Qt | 313 passed |
| `python scripts/verify_example.py Test --all --rename-modules` | Original 41 tests passed; original/generated CLI output matched for all 4 inputs |
| `python scripts/verify_qt_example.py --rename-modules --seed 391` | All 42 generated Python files compiled; Qt startup, manager/human generation and advanced rules passed |
| Qt smoke with seed 927 during implementation | Passed |
| Local dice example, complete strong preset, seeds 1/17/99 | Output matched with identical application RNG seed |
| `git diff --check` | Passed; Git emitted line-ending conversion notices |

The original application's pytest suite and the generated application's Qt
smoke test are separate checks. The generated pytest suite was not counted as
313 passing tests.

## New regression coverage

### Update: 2026-11-09 — project symbols, verification plans and metrics

Validated locally using CPython 3.12.10 on Windows AMD64:

- Full root suite: **81 tests, 80 passed and 1 optional native build skipped**.
- Existing NavigoGrade verifier with polymorphic literals, table dispatch,
  extended flow and physical relocation: 41 original tests passed and all
  four original/generated CLI outputs matched.
- Full `--all --rename-modules` NavigoGrade verification: the same four
  original/generated CLI outputs matched.
- Actual CLI execution with a two-case JSON plan, generated-file hashes,
  two benchmark repetitions, stage metrics and a JSON report passed.
- Syntax compilation and `git diff --check` passed.

New checks cover module aliases, reexports, inheritance, callable aliases,
module-returning factories, branch-dependent receivers, public API contracts,
isolated file mismatches, explicit nonzero exit cases, external unittest
harnesses, zero-test rejection, effective-seed reproduction, failed-stage
reports, plan validation and CLI configuration routing.

The historical Qt results above remain the earlier validation record. The Qt
smoke could not be repeated for this update because its local Password-Generator
input directory was unavailable. The native-build skip remains unchanged;
this update was not executed on Linux or additional Python versions.

- Every literal mode with both dispatcher modes and repeated pipeline layers.
- Polymorphic literals across 12 keys, all byte values, Unicode, lone
  surrogates, empty values, arbitrary read order and concurrent calls.
- Generator send/throw/close, yield-from return values and finally blocks.
- Coroutines, async generators, async iteration/context managers and task
  cancellation, including repeated strong stage sequences.
- Absolute/relative/dynamic imports, namespace packages, relocated packages,
  package resources, nested standalone scripts and library bootstrap imports.
- Project calls whose names are shadowed by parameters or comprehensions;
  retained project imports in alias mode and reflective class members.
- JSON reports, verification mismatch/nonzero exit/timeout, input path
  protection and retaining previous single-file output on verification failure.
- Native adapter success/failure, missing artifact and timeout handling using
  mocked compiler processes. Source survives unsuccessful compilation.
- Indexed UTF-8 source positions with CR, LF, CRLF, form feeds and Unicode
  separators; non-overlapping source replacement validation.
- Immutable chained-byte caching across repeated reads.

## Native integration boundary

Nuitka and a compatible C compiler were unavailable in this environment.
Consequently, no real native artifact was built or executed here. The optional
integration test performs a real build, imports the extension and compares
program output when enabled:

```powershell
python -m pip install -e ".[native]"
# Configure a compatible C compiler first.
$env:OBFUSCATOR_TEST_NATIVE='1'
python -m unittest discover -s tests -p test_new_features.py -v
```

These results cover the listed interpreter, fixtures and execution paths.
Application-specific reflection, custom import hooks, packaging metadata and
other Python versions require their own compatibility checks.
