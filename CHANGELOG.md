# Changelog

## Unreleased

- The default `--all` CLI preset now includes HEX packing and outputs one `.py`
  file. An explicit `--stage-order` still selects the exact operations while
  retaining strong settings. Relocation/native/library-test combinations require
  an explicit source-only order, with an actionable error on conflicts.

- Added `--hex-only` and stage subsets, repeated source stages, `none`, and
  optional boundary-operation IDs in `--stage-order`. Explicit stage selection
  now takes precedence over the sequence supplied by `--all`.
- Disabled shared project identifier rewriting when `name_mangler` is not selected.

- Added opt-in `--hex-pack` / `--hexadecimal`: scripts or complete application
  folders become one ZIP+hex launcher before junk and all other pipeline stages.
- Added packed entry selection, script/module execution and working-directory
  choices; retained package resources, source encodings and exit-handler access
  to temporary files, with Windows-safe cleanup ordering.
- Integrated packed-file verification, reports, metrics and the source-text API.
- Cache decoded chained text as well as bytes to reduce repeated work during
  strong, multilayer dispatcher execution.

- Added static project symbol analysis for import/reexport aliases, qualified
  references, direct instances and inheritance, with compiler scope tables and
  conservative handling of ambiguous receiver/keyword contracts.
- Added JSON verification plans, multiple argument/stdin/exit-code cases,
  isolated generated-file checks and Python contract-test commands.
- Added stage time/size measurements, repeated process benchmarks and effective
  seeds in successful/failed reports for reproduction.
- Added explicit `--keep-name` public API contracts and exported `VerificationCase`.

- Documented Linux setup for APT, DNF, Pacman, Zypper and APK families, including
  isolated installation, versioned interpreters, optional Tk and headless use.
- Converted unavailable Tk/display errors into actionable CLI messages and
  shared picker resource cleanup across file and folder selection.

- Added polymorphic chained codecs and extended generator/coroutine dispatch;
  upgraded the `--all` preset to include both.
- Added optional physical module/package relocation with an encoded importlib
  mapping, resource relocation and a library bootstrap.
- Added JSON reports, input function skip reasons and explicit differential
  execution with stdin and timeout controls.
- Added the optional Nuitka module backend (`.[native]`). Native compilation
  and physical relocation remain separate distribution modes.
- Fixed nested standalone script import resolution, retained project imports
  in alias mode, reflective class-field protection across layers, and keyword
  rewriting when a parameter or comprehension shadows a project callable.
- Added semantic regression tests and a temporary Qt application smoke harness.
- Indexed AST source positions and assembled numeric edits in one pass to
  avoid repeated whole-source scans in large multilayer builds; covered UTF-8
  columns, CR/LF, form feeds and Unicode string separators.
- Cached decoded chained byte literals to avoid repeating nested codec work
  inside multilayer dispatcher loops.

## 2.0.0

- Removed fixed `decode` helper identifiers and `.decode(...)` calls from
  generated import/literal helpers; masked the character-builtin lookup and
  captured text conversion to tolerate shadowed builtin names.

- Added optional layered and chained literal encoding, combining module and
  literal keys, XOR, byte rotation, offsets and ciphertext feedback.
- Added randomized table/match dispatch for function and module flow while
  retaining opaque-predicate support and original statement scope.
- Preserved per-file stage context during project-wide stage traversal.
- Fixed encoding of literals adjacent to keywords in minified source and
  supported surrogate-containing strings.

- Split CLI, configuration, execution, filesystem operations, and the stage
  registry into focused modules.
- Added an immutable validated `PipelineConfig` API.
- Kept the original `code_editors.pipeline` API and `code-editors` command as
  compatibility layers.
- Added the preferred `obfuscator-edu` command and `python -m code_editors`.
- Fixed repeated identifier mangling when a class field shares a spelling with
  a local variable.
- Added regression coverage for namespace introspection and class-field mapping.
- Expanded architecture, extension, safety, and usage documentation.
