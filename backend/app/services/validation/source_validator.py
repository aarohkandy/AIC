from __future__ import annotations

import ast

from app.models.schemas import WhitelistFinding


ALLOWED_IMPORTS = {"cadquery"}
ALLOWED_NODES = {
    ast.Module,
    ast.Import,
    ast.ImportFrom,
    ast.alias,
    ast.FunctionDef,
    ast.arguments,
    ast.arg,
    ast.Return,
    ast.Assign,
    ast.AnnAssign,
    ast.Expr,
    ast.Call,
    ast.Name,
    ast.Load,
    ast.Store,
    ast.Constant,
    ast.Attribute,
    ast.BinOp,
    ast.UnaryOp,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.Pow,
    ast.Mod,
    ast.USub,
    ast.Dict,
    ast.List,
    ast.Tuple,
    ast.keyword,
    ast.For,
    ast.If,
    ast.Compare,
    ast.Eq,
    ast.NotEq,
    ast.Gt,
    ast.GtE,
    ast.Lt,
    ast.LtE,
    ast.ListComp,
    ast.comprehension,
    ast.Subscript,
    ast.Slice,
    ast.IfExp,
}


class SourceValidator:
    def validate(self, source: str) -> list[WhitelistFinding]:
        findings: list[WhitelistFinding] = []
        try:
            compile(source, "<generated>", "exec")
            tree = ast.parse(source)
        except SyntaxError as exc:
            return [
                WhitelistFinding(
                    severity="error",
                    message=f"Generated source did not parse: {exc.msg}",
                )
            ]

        for node in ast.walk(tree):
            if type(node) not in ALLOWED_NODES:
                findings.append(
                    WhitelistFinding(
                        severity="warning",
                        message=f"Non-whitelisted AST node detected: {type(node).__name__}",
                    )
                )
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name not in ALLOWED_IMPORTS:
                        findings.append(
                            WhitelistFinding(
                                severity="error",
                                message=f"Import not allowed in deterministic compiler output: {alias.name}",
                            )
                        )
            if isinstance(node, ast.ImportFrom):
                findings.append(
                    WhitelistFinding(
                        severity="error",
                        message="from-import statements are not allowed in deterministic compiler output",
                    )
                )

        if not findings:
            findings.append(
                WhitelistFinding(
                    severity="info",
                    message="AST whitelist passed. This is a lint check, not a sandbox boundary.",
                )
            )
        return findings
