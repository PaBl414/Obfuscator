"""Static project references: imports, reexports, aliases and class inheritance.

Only statically resolved project objects qualify for member/keyword rewrites.
External imports and unknown values remain unknown; source modules are never
imported or executed during analysis. Compiler symbol tables identify locals
that shadow outer bindings, including locals assigned later in a function.
"""
from __future__ import annotations

import ast
import symtable
from pathlib import Path

from ..stages.source_edits import SourceIndex, apply_edits

Reference = tuple[str, str, str]  # kind, module, qualified definition


class ProjectSymbols:
    def __init__(self, sources: dict[Path, str], root: Path):
        self.sources = sources
        self.trees = {path: ast.parse(source) for path, source in sources.items()}
        self.modules = {path: '.'.join((path.relative_to(root).parent.parts
            if path.name == '__init__.py' else path.relative_to(root).with_suffix('').parts))
            for path in sources}
        self.environments: dict[str, dict[str, Reference | None]] = {module: {} for module in self.modules.values()}
        for module in list(self.environments):
            parts = module.split('.')
            for length in range(1, len(parts)):
                self.environments.setdefault('.'.join(parts[:length]), {})
        self.classes: dict[Reference, ast.ClassDef] = {}
        self.functions = {}
        self.resolving_returns = set()
        self.members: dict[Reference, dict[str, Reference]] = {}
        self.bases: dict[Reference, list[Reference]] = {}
        self.tables = {}
        self.coverage = {}
        for path, source in sources.items():
            table = symtable.symtable(source, str(path), 'exec')
            tables = []
            def collect(item):
                tables.append(item)
                for child in item.get_children():
                    collect(child)
            collect(table)
            self.tables[path] = {(item.get_name(), item.get_lineno()): item for item in tables}
            self.coverage[path] = [{"scope": item.get_name(), "line": item.get_lineno(),
                "locals": sorted(symbol.get_name() for symbol in item.get_symbols() if symbol.is_local()),
                "free": sorted(symbol.get_name() for symbol in item.get_symbols() if symbol.is_free())}
                for item in tables]
            module = self.modules[path]
            for node in self.trees[path].body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    kind = 'class' if isinstance(node, ast.ClassDef) else 'function'
                    ref = (kind, module, node.name)
                    self.environments[module][node.name] = ref
                    if kind == 'function':
                        self.functions[ref] = (node, path)
                    if kind == 'class':
                        self.classes[ref] = node
                        members = self.members[ref] = {}
                        for member in node.body:
                            if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                                members[member.name] = ('function', module, node.name + '.' + member.name)
                                self.functions[members[member.name]] = (member, path)
                            elif isinstance(member, (ast.Assign, ast.AnnAssign)):
                                targets = member.targets if isinstance(member, ast.Assign) else [member.target]
                                for target in targets:
                                    for name in ast.walk(target):
                                        if isinstance(name, ast.Name):
                                            members[name.id] = ('value', module, node.name + '.' + name.id)
                        for member in ast.walk(node):
                            if (isinstance(member, ast.Attribute) and isinstance(member.ctx, ast.Store)
                                    and isinstance(member.value, ast.Name) and member.value.id in {'self', 'cls'}):
                                members[member.attr] = ('value', module, node.name + '.' + member.attr)
        # Fixed point includes reexports and imports encountered before providers.
        for _ in range(max(2, len(sources) + 1)):
            previous = repr(self.environments)
            for path, tree in self.trees.items():
                env = self.environments[self.modules[path]]
                for node in tree.body:
                    self.bind(node, env, path)
            if repr(self.environments) == previous:
                break
        for ref, node in self.classes.items():
            env = self.environments[ref[1]]
            self.bases[ref] = [value for base in node.bases
                              if (value := self.resolve(base, [env])) and value[0] == 'class']
        self.attributes = {}
        self.calls = {}
        self.project_import_bindings: set[str] = set()
        self.external_import_bindings: set[str] = set()
        self.unknown_keywords: set[str] = set()
        self.unstable_members: set[str] = set()
        for path, tree in self.trees.items():
            self._analyze(path, tree)

    def member(self, ref: Reference | None, name: str, seen=None):
        if ref is None:
            return None
        if ref[0] == 'module':
            child = ref[1] + '.' + name if ref[1] else name
            return self.environments.get(ref[1], {}).get(name) or (
                ('module', child, '') if child in self.environments else None)
        if ref[0] not in {'class', 'instance'}:
            return None
        ref = ('class', ref[1], ref[2])
        seen = set() if seen is None else seen
        if ref in seen:
            return None
        seen.add(ref)
        if name in self.members.get(ref, {}):
            return self.members[ref][name]
        for base in self.bases.get(ref, []):
            result = self.member(base, name, seen)
            if result:
                return result
        return None

    def resolve(self, expression, scopes):
        if isinstance(expression, ast.Name):
            return next((scope[expression.id] for scope in reversed(scopes)
                         if expression.id in scope), None)
        if isinstance(expression, ast.Attribute):
            return self.member(self.resolve(expression.value, scopes), expression.attr)
        if isinstance(expression, ast.Call):
            ref = self.resolve(expression.func, scopes)
            if ref and ref[0] == 'class':
                return ('instance', ref[1], ref[2])
            if ref and ref in self.functions and ref not in self.resolving_returns:
                function, path = self.functions[ref]
                body = [node for node in function.body if not (isinstance(node, ast.Expr)
                        and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str))]
                if (not isinstance(function, ast.AsyncFunctionDef) and not function.decorator_list
                        and len(body) == 1 and isinstance(body[0], ast.Return)):
                    table = self.tables[path].get((function.name, function.lineno))
                    local = {symbol.get_name(): None for symbol in table.get_symbols() if symbol.is_local()} if table else {}
                    self.resolving_returns.add(ref)
                    try:
                        return self.resolve(body[0].value, [self.environments[ref[1]], local])
                    finally:
                        self.resolving_returns.remove(ref)
        return None

    def member_names(self, ref, seen=None):
        if ref is None:
            return set()
        if ref[0] == 'module':
            return set(self.environments.get(ref[1], {}))
        if ref[0] not in {'class', 'instance'}:
            return set()
        ref = ('class', ref[1], ref[2])
        seen = set() if seen is None else seen
        if ref in seen:
            return set()
        seen.add(ref)
        return set(self.members.get(ref, {})) | set().union(
            *(self.member_names(base, seen) for base in self.bases.get(ref, [])))

    def bind(self, node, env, path, scopes=None):
        scopes = [env] if scopes is None else scopes
        if isinstance(node, ast.Import):
            for alias in node.names:
                module = alias.name if alias.asname else alias.name.split('.')[0]
                env[alias.asname or module] = ('module', module, '') if module in self.environments else None
        elif isinstance(node, ast.ImportFrom):
            package = self.modules[path] if path.name == '__init__.py' else self.modules[path].rpartition('.')[0]
            if node.level:
                parts = package.split('.') if package else []
                anchor = parts[:len(parts) - node.level + 1] if node.level <= len(parts) + 1 else []
                module = '.'.join([*anchor, *([node.module] if node.module else [])])
            else:
                module = node.module or ''
                sibling = '.'.join(filter(None, (package, module)))
                if module not in self.environments and sibling in self.environments:
                    module = sibling
            for alias in node.names:
                if alias.name != '*':
                    env[alias.asname or alias.name] = self.member(('module', module, ''), alias.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value = self.resolve(node.value, scopes)
            for target in targets:
                for name in ast.walk(target):
                    if isinstance(name, ast.Name) and isinstance(name.ctx, ast.Store):
                        binding = value if isinstance(target, ast.Name) else None
                        if binding is None and isinstance(target, ast.Name) and env is self.environments[self.modules[path]]:
                            binding = ('value', self.modules[path], name.id)
                        env[name.id] = binding

    def _analyze(self, path, tree):
        graph = self
        attributes, calls = [], []
        class Visitor(ast.NodeVisitor):
            def __init__(self):
                self.scopes = [dict(graph.environments[graph.modules[path]])]
                self.class_scopes = [False]
                self.owner = None
                self.comprehensions = set()

            def visit_Attribute(self, node):
                ref = graph.resolve(node, self.scopes)
                if ref:
                    attributes.append((node, ref))
                self.generic_visit(node)

            def visit_Call(self, node):
                ref = graph.resolve(node.func, self.scopes)
                if ref and ref[0] in {'class', 'function'}:
                    calls.append(node)
                else:
                    graph.unknown_keywords.update(keyword.arg for keyword in node.keywords if keyword.arg)
                self.generic_visit(node)

            def visit_Assign(self, node):
                self.generic_visit(node)
                graph.bind(node, self.scopes[-1], path, self.scopes)

            visit_AnnAssign = visit_Assign

            def visit_NamedExpr(self, node):
                self.visit(node.value)
                scope = next(scope for scope in reversed(self.scopes) if id(scope) not in self.comprehensions)
                scope[node.target.id] = graph.resolve(node.value, self.scopes)

            def visit_AugAssign(self, node):
                self.generic_visit(node)
                if isinstance(node.target, ast.Name):
                    self.scopes[-1][node.target.id] = None

            def visit_For(self, node):
                self.visit(node.iter)
                before = dict(self.scopes[-1])
                self.scopes[-1].update({name.id: None for name in ast.walk(node.target) if isinstance(name, ast.Name)})
                for child in (*node.body, *node.orelse):
                    self.visit(child)
                self.merge_bindings(before, dict(self.scopes[-1]))

            visit_AsyncFor = visit_For

            def merge_bindings(self, first, second):
                merged = {}
                for name in first.keys() | second.keys():
                    left, right = first.get(name), second.get(name)
                    merged[name] = left if left == right else None
                    if left != right:
                        graph.unstable_members.update(graph.member_names(left) | graph.member_names(right))
                self.scopes[-1].clear()
                self.scopes[-1].update(merged)

            def visit_If(self, node):
                self.visit(node.test)
                before = dict(self.scopes[-1])
                for child in node.body:
                    self.visit(child)
                first = dict(self.scopes[-1])
                self.scopes[-1].clear()
                self.scopes[-1].update(before)
                for child in node.orelse:
                    self.visit(child)
                self.merge_bindings(first, dict(self.scopes[-1]))

            def visit_While(self, node):
                self.visit(node.test)
                before = dict(self.scopes[-1])
                for child in (*node.body, *node.orelse):
                    self.visit(child)
                self.merge_bindings(before, dict(self.scopes[-1]))

            def visit_Try(self, node):
                before = dict(self.scopes[-1])
                for child in (*node.body, *node.orelse):
                    self.visit(child)
                branches = [dict(self.scopes[-1]), before]
                for handler in node.handlers:
                    self.scopes[-1].clear()
                    self.scopes[-1].update(before)
                    self.visit(handler)
                    branches.append(dict(self.scopes[-1]))
                for branch in branches:
                    self.merge_bindings(dict(self.scopes[-1]), branch)
                for child in node.finalbody:
                    self.visit(child)

            visit_TryStar = visit_Try

            def visit_Match(self, node):
                self.visit(node.subject)
                before = dict(self.scopes[-1])
                branches = [before]
                for case in node.cases:
                    self.scopes[-1].clear()
                    self.scopes[-1].update(before)
                    for pattern in ast.walk(case.pattern):
                        name = getattr(pattern, 'name', None) or getattr(pattern, 'rest', None)
                        if name:
                            self.scopes[-1][name] = None
                    if case.guard:
                        self.visit(case.guard)
                    for child in case.body:
                        self.visit(child)
                    branches.append(dict(self.scopes[-1]))
                for branch in branches:
                    self.merge_bindings(dict(self.scopes[-1]), branch)

            def visit_With(self, node):
                for item in node.items:
                    self.visit(item.context_expr)
                    if item.optional_vars:
                        self.scopes[-1].update({name.id: None for name in ast.walk(item.optional_vars)
                                                if isinstance(name, ast.Name)})
                for child in node.body:
                    self.visit(child)

            visit_AsyncWith = visit_With

            def visit_ExceptHandler(self, node):
                if node.type:
                    self.visit(node.type)
                if node.name:
                    self.scopes[-1][node.name] = None
                for child in node.body:
                    self.visit(child)

            def visit_Delete(self, node):
                self.generic_visit(node)
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        self.scopes[-1][target.id] = None

            def visit_Import(self, node):
                graph.bind(node, self.scopes[-1], path, self.scopes)
                for alias in node.names:
                    name = alias.asname or (alias.name.split('.')[0] if isinstance(node, ast.Import) else alias.name)
                    if name == '*':
                        continue
                    ref = self.scopes[-1].get(name)
                    if ref and ref[0] != 'module':
                        graph.project_import_bindings.add(name)
                    elif ref is None:
                        graph.external_import_bindings.add(name)

            visit_ImportFrom = visit_Import

            def visit_FunctionDef(self, node):
                for value in (*node.decorator_list, *node.args.defaults,
                              *(value for value in node.args.kw_defaults if value)):
                    self.visit(value)
                self.scopes[-1][node.name] = ('function', graph.modules[path], node.name)
                table = graph.tables[path].get((node.name, node.lineno))
                local = {symbol.get_name(): None for symbol in table.get_symbols()
                         if symbol.is_local()} if table else {}
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        local[child.name] = ('class' if isinstance(child, ast.ClassDef) else 'function',
                                             graph.modules[path], child.name)
                decorators = {item.id for item in node.decorator_list if isinstance(item, ast.Name)}
                args = (*node.args.posonlyargs, *node.args.args)
                if self.owner and args and 'staticmethod' not in decorators:
                    local[args[0].arg] = (('class' if 'classmethod' in decorators else 'instance'),
                                           self.owner[1], self.owner[2])
                saved, saved_classes, owner = self.scopes, self.class_scopes, self.owner
                self.scopes = [scope for scope, cls in zip(saved, saved_classes) if not cls] + [local]
                self.class_scopes = [False] * len(self.scopes)
                self.owner = None
                for child in node.body:
                    self.visit(child)
                self.scopes, self.class_scopes, self.owner = saved, saved_classes, owner

            visit_AsyncFunctionDef = visit_FunctionDef

            def visit_ClassDef(self, node):
                for value in (*node.bases, *node.decorator_list):
                    self.visit(value)
                previous = self.owner
                self.owner = ('class', graph.modules[path], node.name)
                self.scopes.append(dict(graph.members.get(self.owner, {})))
                self.class_scopes.append(True)
                for child in node.body:
                    self.visit(child)
                self.scopes.pop()
                self.class_scopes.pop()
                self.owner = previous
                self.scopes[-1][node.name] = ('class', graph.modules[path], node.name)

            def visit_Lambda(self, node):
                args = node.args
                for value in (*args.defaults, *(value for value in args.kw_defaults if value)):
                    self.visit(value)
                local = {arg.arg: None for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs)}
                for arg in (args.vararg, args.kwarg):
                    if arg:
                        local[arg.arg] = None
                self.scopes.append(local)
                self.class_scopes.append(False)
                self.visit(node.body)
                self.class_scopes.pop()
                self.scopes.pop()

            def visit_ListComp(self, node):
                self.scopes.append({})
                self.comprehensions.add(id(self.scopes[-1]))
                self.class_scopes.append(False)
                for generator in node.generators:
                    self.visit(generator.iter)
                    self.scopes[-1].update({name.id: None for name in ast.walk(generator.target)
                                            if isinstance(name, ast.Name)})
                    for value in generator.ifs:
                        self.visit(value)
                for value in (getattr(node, 'elt', None), getattr(node, 'key', None), getattr(node, 'value', None)):
                    if value is not None:
                        self.visit(value)
                self.comprehensions.discard(id(self.scopes[-1]))
                self.scopes.pop()
                self.class_scopes.pop()

            visit_SetComp = visit_ListComp
            visit_DictComp = visit_ListComp
            visit_GeneratorExp = visit_ListComp
        Visitor().visit(tree)
        self.attributes[path], self.calls[path] = attributes, calls

    def known_attributes(self, path):
        return {(node.lineno, node.col_offset, node.attr) for node, ref in self.attributes[path]
                if ref[0] != 'module'}

    def rewrite_references(self, path: Path, mapping: dict[str, str]) -> str:
        source = self.sources[path]
        index = SourceIndex(source)
        edits = {}
        for node, ref in self.attributes[path]:
            if ref[0] != 'module' and node.attr in mapping:
                end = index.offset(node.end_lineno, node.end_col_offset)
                edits[(end - len(node.attr), end)] = mapping[node.attr]
        for call in self.calls[path]:
            for keyword in call.keywords:
                if keyword.arg in mapping:
                    start = index.offset(keyword.lineno, keyword.col_offset)
                    edits[(start, start + len(keyword.arg))] = mapping[keyword.arg]
        return apply_edits(source, [(start, end, value) for (start, end), value in edits.items()])
