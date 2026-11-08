#!/usr/bin/env python3
"""
strip_py.py — removes comments, docstrings, extra spaces, and line breaks.

The output is semantically equivalent to the original (like python -OO):
- comments are removed,
- docstrings are removed (__doc__ = None),
- blank lines and extra spaces are removed,
- adjacent simple statements are joined with semicolons,
- indentation is reduced to a minimum.

Wymaga Pythona 3.9+ (ast.unparse).

Usage:
    python strip_py.py input.py                # output to stdout
    python strip_py.py input.py -o output.py   # write to a file
    python strip_py.py input.py -i             # modify in place
    python strip_py.py input.py --no-semicolons
"""

from __future__ import annotations

import argparse
import ast
import io
import sys
import tokenize

# Multi-character operators: joining two operators may change tokenization.
_MULTI_OPS = frozenset({
    '**', '//', '<<', '>>', '<=', '>=', '==', '!=', ':=', '->',
    '+=', '-=', '*=', '/=', '%=', '&=', '|=', '^=', '@=',
    '**=', '//=', '<<=', '>>=',
})

# Compound statement starters: a semicolon must not be inserted after them.
_COMPOUND_STARTERS = frozenset({
    'if', 'elif', 'else', 'for', 'while', 'def', 'class',
    'with', 'try', 'except', 'finally', 'async', 'match', 'case', '@',
})

_FSTRING_START = getattr(tokenize, 'FSTRING_START', None)
_FSTRING_MIDDLE = getattr(tokenize, 'FSTRING_MIDDLE', None)
_DOC_OWNERS = (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _is_name_or_num(ttype: int) -> bool:
    return ttype in (tokenize.NAME, tokenize.NUMBER)


def _is_string_start(ttype: int) -> bool:
    if ttype == tokenize.STRING:
        return True
    return _FSTRING_START is not None and ttype == _FSTRING_START


def _needs_space(prev_type, prev_str, cur_type, cur_str) -> bool:
    if prev_str is None:
        return False
    # NAME/NUMBER + NAME/NUMBER would otherwise merge into one token.
    if _is_name_or_num(prev_type) and _is_name_or_num(cur_type):
        return True
    # NAME + prefixed string (for example f"..."): without a space the prefix absorbs the name.
    if _is_name_or_num(prev_type) and _is_string_start(cur_type):
        if cur_str and cur_str[0].isalpha():
            return True
    # 1 .x vs 1.x: a dot immediately after a number is unsafe.
    if prev_type == tokenize.NUMBER and cur_type == tokenize.OP and cur_str == '.':
        return True
    # OP + OP must be separated when joining them would form a valid operator.
    if prev_type == tokenize.OP and cur_type == tokenize.OP:
        if prev_str + cur_str in _MULTI_OPS:
            return True
    return False


def _strip_docstrings(tree: ast.AST) -> None:
    for node in ast.walk(tree):
        if not isinstance(node, _DOC_OWNERS):
            continue
        body = node.body
        if body and isinstance(body[0], ast.Expr):
            v = body[0].value
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                node.body = body[1:]
                if not node.body and not isinstance(node, ast.Module):
                    node.body = [ast.Pass()]


def _minify_tokens(source: str, indent_width: int, use_semicolons: bool) -> str:
    tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    out: list[str] = []
    level = 0
    need_indent = False
    prev_type = None
    prev_str = None
    n = len(tokens)
    i = 0

    while i < n:
        t = tokens[i]
        i += 1

        if t.type in (tokenize.COMMENT, tokenize.NL, tokenize.ENCODING):
            continue
        if t.type == tokenize.ENDMARKER:
            break
        if t.type == tokenize.INDENT:
            level += 1
            continue
        if t.type == tokenize.DEDENT:
            level -= 1
            continue
        if t.type == tokenize.NEWLINE:
            # Optionally replace the newline with a semicolon when the next
            # line contains a simple statement at the same indentation level.
            if use_semicolons:
                j = i
                while j < n and tokens[j].type in (tokenize.COMMENT, tokenize.NL):
                    j += 1
                if j < n:
                    nt = tokens[j]
                    if (
                        nt.type not in (tokenize.INDENT, tokenize.DEDENT, tokenize.ENDMARKER)
                        and nt.string not in _COMPOUND_STARTERS
                    ):
                        out.append(';')
                        prev_type, prev_str = tokenize.OP, ';'
                        need_indent = False
                        continue
            out.append('\n')
            need_indent = True
            prev_type = prev_str = None
            continue

        if need_indent:
            out.append(' ' * (level * indent_width))
            need_indent = False
        elif prev_str is not None and _needs_space(prev_type, prev_str, t.type, t.string):
            out.append(' ')

        if _FSTRING_MIDDLE is not None and t.type == _FSTRING_MIDDLE:
            # Python 3.12 tokenization removes one level of doubled braces from
            # literal f-string text. Restore the source escaping before joining
            # tokens, or CSS/JSON braces become Python format expressions.
            out.append(t.string.replace('{', '{{').replace('}', '}}'))
        else:
            out.append(t.string)
        prev_type, prev_str = t.type, t.string

    result = ''.join(out)
    if not result.endswith('\n'):
        result += '\n'
    return result


def minify(source: str, indent_width: int = 1, use_semicolons: bool = True) -> str:
    tree = ast.parse(source)
    _strip_docstrings(tree)
    base = ast.unparse(tree)
    return _minify_tokens(base, indent_width, use_semicolons)


def process(source: str, **kw) -> str:
    """Preserve an optional shebang and minify the rest."""
    if source.startswith('#!'):
        nl = source.find('\n')
        if nl == -1:
            return source
        shebang, body = source[: nl + 1], source[nl + 1 :]
        return shebang + minify(body, **kw)
    return minify(source, **kw)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument('file', help='path to a .py file')
    p.add_argument('-o', '--output', help='output file')
    p.add_argument('-i', '--in-place', action='store_true', help='modify the file in place')
    p.add_argument('--indent-width', type=int, default=1,
                   help='indentation width per level (default: 1)')
    p.add_argument('--no-semicolons', action='store_true',
                   help='do not join adjacent statements with semicolons')
    args = p.parse_args(argv)

    with tokenize.open(args.file) as f:      # respects the declared source encoding
        source = f.read()

    result = process(source,
                     indent_width=args.indent_width,
                     use_semicolons=not args.no_semicolons)

    if args.in_place:
        with open(args.file, 'w', encoding='utf-8') as f:
            f.write(result)
    elif args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            f.write(result)
    else:
        sys.stdout.write(result)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
