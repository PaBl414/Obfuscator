# Changelog

## Unreleased

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
