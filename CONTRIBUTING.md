# Contributing

## Development setup

ObfuscatorEdu requires Python 3.10 or newer and has no runtime dependencies.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e .
```

## Design rules

- Keep transformations in `code_editors/stages/` independent from CLI and
  filesystem concerns.
- Use the common stage adapter contract described in `docs/architecture.md`.
- Preserve behavior for supported syntax and explicitly skip unsafe constructs.
- Keep seeded transformations deterministic.
- Do not weaken existing transformations to make a failing example pass.
- Add a regression test for every fixed defect.
- Document new stage IDs, options, skip conditions, and behavior changes.

## Validation

Before submitting a change, run:

```powershell
python -m unittest discover -s tests -v
python -m compileall -q code_editors tests pipeline.py
```

Changes affecting folder processing or shared names should also be tested on a
multi-module package. Compare original and obfuscated output for the same input.

## Commit scope

Keep generated files out of commits. The repository ignores bytecode caches,
build output, distribution metadata, virtual environments, and generated
obfuscation output.

Local input projects, scratch examples, generated reports and development
output are excluded by the root `.gitignore`. Keep reusable regression cases
inside `tests/` using temporary directories rather than committing full input
applications or their obfuscated copies.

The source distribution includes documentation, tests and verification
scripts through `MANIFEST.in`. The installed wheel contains the
`code_editors` package and its command-line entrypoints.
