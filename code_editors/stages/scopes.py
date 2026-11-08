"""Lexical binding checks used when rewriting project call keywords.

A parameter or assignment can shadow a project function with an external
callable. Its keyword spellings must then follow that external callable's API.
"""
from __future__ import annotations

import ast


def project_calls(tree: ast.AST, candidates: set[str]) -> set[ast.Call]:
    result: set[ast.Call] = set()

    class Bindings(ast.NodeVisitor):
        def __init__(self):
            self.local: dict[str, bool] = {}
            self.declarations: set[str] = set()

        def visit_Name(self, node):
            if isinstance(node.ctx, (ast.Store, ast.Del)):
                self.local[node.id] = False

        def visit_FunctionDef(self, node):
            self.local[node.name] = node.name in candidates

        visit_AsyncFunctionDef = visit_FunctionDef
        visit_ClassDef = visit_FunctionDef

        def visit_Import(self, node):
            for alias in node.names:
                self.local[alias.asname or alias.name.split('.')[0]] = False

        def visit_ImportFrom(self, node):
            for alias in node.names:
                name = alias.asname or alias.name
                self.local[name] = name in candidates

        def visit_Global(self, node):
            self.declarations.update(node.names)

        visit_Nonlocal = visit_Global

        def visit_Lambda(self, node):
            pass

        def visit_ListComp(self, node):
            pass

        visit_SetComp = visit_ListComp
        visit_DictComp = visit_ListComp
        visit_GeneratorExp = visit_ListComp

    class Calls(ast.NodeVisitor):
        def __init__(self):
            self.scopes: list[dict[str, bool]] = []
            self.classes: list[bool] = []

        def visit_scope(self, node, body, args=None):
            bindings = Bindings()
            for statement in body:
                bindings.visit(statement)
            if args:
                for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs):
                    bindings.local[arg.arg] = False
                for arg in (args.vararg, args.kwarg):
                    if arg:
                        bindings.local[arg.arg] = False
            for name in bindings.declarations:
                bindings.local.pop(name, None)
            self.scopes.append(bindings.local)
            self.classes.append(isinstance(node, ast.ClassDef))
            for statement in body:
                self.visit(statement)
            self.scopes.pop()
            self.classes.pop()

        def visit_Module(self, node):
            self.visit_scope(node, node.body)

        def visit_FunctionDef(self, node):
            for expression in (*node.decorator_list, *node.args.defaults,
                               *(value for value in node.args.kw_defaults if value)):
                self.visit(expression)
            # A method's free names resolve outside the class namespace.
            saved = self.scopes
            self.scopes = [scope for scope, cls in zip(self.scopes, self.classes) if not cls]
            saved_classes = self.classes
            self.classes = [False] * len(self.scopes)
            self.visit_scope(node, node.body, node.args)
            self.scopes, self.classes = saved, saved_classes

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Lambda(self, node):
            self.visit_scope(node, [ast.Expr(value=node.body)], node.args)

        def visit_ClassDef(self, node):
            for expression in (*node.bases, *node.decorator_list):
                self.visit(expression)
            self.visit_scope(node, node.body)

        def visit_Call(self, node):
            if isinstance(node.func, ast.Name) and node.func.id in candidates:
                binding = next((scope[node.func.id] for scope in reversed(self.scopes)
                                if node.func.id in scope), True)
                if binding:
                    result.add(node)
            elif isinstance(node.func, ast.Attribute) and node.func.attr in candidates:
                result.add(node)
            self.generic_visit(node)

        def visit_ListComp(self, node):
            self.scopes.append({})
            self.classes.append(False)
            for generator in node.generators:
                self.visit(generator.iter)
                self.scopes[-1].update({name.id: False for name in ast.walk(generator.target)
                                       if isinstance(name, ast.Name)})
                for condition in generator.ifs:
                    self.visit(condition)
            for value in (getattr(node, "elt", None), getattr(node, "key", None), getattr(node, "value", None)):
                if value is not None:
                    self.visit(value)
            self.scopes.pop()
            self.classes.pop()

        visit_SetComp = visit_ListComp
        visit_DictComp = visit_ListComp
        visit_GeneratorExp = visit_ListComp

    Calls().visit(tree)
    return result
