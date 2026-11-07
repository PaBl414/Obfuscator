# Code Editors

A standard-library-only toolkit for applying source transformations to Python files.

## Requirements

- Python 3.10 or newer
- Tkinter is only needed when using the graphical file or folder picker
- No third-party runtime packages are required

## Run the pipeline

Run the command without arguments to choose a Python file in a dialog:

```powershell
python pipeline.py
```

You can also pass the file directly:

```powershell
python pipeline.py path\to\script.py
python pipeline.py path\to\script.py --output path\to\result.py
```

The input file is preserved. By default, the result is written next to it as
`<name>_edited.py`.

## Process a folder

Select a folder in a dialog:

```powershell
python pipeline.py --folder
```

Or provide paths directly:

```powershell
python pipeline.py --folder path\to\source
python pipeline.py --folder path\to\source --output path\to\source_processed
```

Every `.py` file below the input folder is transformed. Relative paths are
preserved in a new output folder. Other files are copied unchanged. Common
generated and environment folders such as `.venv`, `venv`, `__pycache__`,
`build`, `dist`, and `node_modules` are excluded. The output folder must not
already exist and must be outside the input folder.

## Transformation order

The default sequence is defined by `STAGE_ORDER` in `code_editors/pipeline.py`.
Reorder those stage IDs there, or pass a different sequence to `transform`,
`transform_layers`, `process_file`, or `process_folder`. The pipeline validates
that every registered stage appears at least once; stage IDs may repeat and
there are no enforced ordering dependencies. From the command line, use
`--stage-order` with a comma-separated list:

```powershell
python pipeline.py path\to\script.py --stage-order junk,imports,xor,xor,function_flow,module_flow,flattener,name_mangler,cleanup,numeric
python pipeline.py path\to\script.py --stage-order imports,junk,xor,name_mangler,function_flow,module_flow,flattener,junk,xor,name_mangler,numeric,cleanup
```

The CLI reports each stage as it starts and completes, including the current
layer, occurrence number for repeated stages, and the stage seed. Flattener
placed before module flow reports that it was skipped. Numeric literal spelling
is preserved through later AST-rewriting stages such as cleanup.

1. Junk editor
2. Import obfuscation (conservative explicit aliases)
3. XOR encryption (distinct key per literal)
4. Function flow (supported simple synchronous functions)
5. Flow obfuscator
6. Flattener (opaque predicates)
7. Name mangler
8. Cleanup
9. Numeric literal obfuscation (equivalent integer bases)

The import pass adds redundant explicit aliases only where the compiled
instruction stream stays identical. It leaves existing aliases, dotted
`import` statements, star imports, and `__future__` imports unchanged. The
import names remain visible; this conservative pass changes their spelling
form without replacing Python's import mechanism.

For stronger import-name hiding, opt into runtime mode:

```powershell
python pipeline.py path\to\script.py --import-mode runtime
```

Runtime mode splits supported module paths into variable-sized chunks, XOR-
encodes each chunk with a separate key, and loads them through a generated
helper. It leaves `__future__`, star imports,
multi-name `from` imports, and files using import-hook or scope-introspection
features unchanged. Its generated helper uses dynamic import and scope access,
so the conservative Flow and function-flow stages may skip parts of that file.
It can affect programs that depend on import-call stack details or custom
import hooks, and the encoded names can still be recovered from the generated
code at runtime. The default `aliases` mode keeps the bytecode-preserving
behavior.

The XOR stage assigns a distinct key to each encrypted literal. The keys are
stored with their payloads in the output, so this is source obfuscation rather
than secure data encryption.

Function flow targets undecorated synchronous functions. Nested `if` suites
are split into independently dispatched states with randomized state IDs and
opaque arithmetic predicates. Loops, `try`, `with`, and `match` remain intact
inside a state so their control behavior is less likely to change. The stage
skips generators, async functions, unsupported statement forms, and functions
using constructs the safety visitor rejects. External debuggers or tools that
inspect code objects can observe the changed function bytecode.

If the module-level flow safety check rejects a file, the pipeline skips that
stage and its dependent opaque-predicate flattener, then continues with the
remaining transformations. When a module uses `__all__`, identifier mangling
is skipped to preserve the exported names. In folder mode, names imported by
Python files in the folder are also preserved across modules.
Numeric obfuscation changes only the spelling of integer literals (decimal,
hexadecimal, octal, or binary); it verifies that the parsed AST is unchanged.

Use `--seed N` for reproducible transformations. `--junk-count N` controls the
number of inserted junk blocks, and `--name-length N` sets generated identifier
length to 4, 5, or 6 characters.

Run the full sequence any positive number of times. Each layer uses the
previous layer's output:

```powershell
python pipeline.py path\to\script.py --layers 1
python pipeline.py path\to\script.py --layers 2 --seed 100
python pipeline.py path\to\script.py --layers 5 --seed 100
```

With `--seed S`, each stage run receives a distinct deterministic seed,
starting at `S` and incrementing across repeated stages, layers, and files.
Without `--seed`, each stage run receives a distinct random seed. Folder mode
uses the same per-run uniqueness guarantee across all files.

## Project layout

```text
pipeline.py                 Stable command-line entry point
code_editors/
  pipeline.py               File and folder orchestration
  stages/                    Individual transformation implementations
tests/                      Unit and end-to-end tests
.github/workflows/          GitHub Actions CI
```

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for
details.

## Run tests

```powershell
python -m unittest discover -s tests -v
```
