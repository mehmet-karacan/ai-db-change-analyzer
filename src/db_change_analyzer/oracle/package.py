"""Parse package declarations and source spans with the generated PL/SQL grammar."""

from __future__ import annotations

from dataclasses import dataclass

from antlr4 import CommonTokenStream, InputStream, ParserRuleContext
from antlr4.atn.PredictionMode import PredictionMode

from .generated.PlSqlLexer import PlSqlLexer
from .generated.PlSqlParser import PlSqlParser
from .ddl_tokens import code_tokens
from .parser_worker import CollectingErrorListener


@dataclass(frozen=True, slots=True)
class Parameter:
    name: str
    position: int
    mode: str | None
    data_type: str | None
    default: str | None
    nocopy: bool
    source: str


@dataclass(frozen=True, slots=True)
class Routine:
    name: str
    kind: str
    signature: str
    return_type: str | None
    parameters: tuple[Parameter, ...]
    source: str
    start_line: int
    end_line: int
    transactions: tuple[str, ...]
    exception_handlers: tuple[str, ...]
    dynamic_sql: tuple[str, ...]
    sql_statements: tuple[str, ...]
    conditions: tuple[str, ...]
    assignments: tuple[str, ...]
    control_flow: tuple[str, ...]
    raises: tuple[str, ...]
    call_references: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PackageExtraction:
    object_type: str
    authid: str | None
    routines: tuple[Routine, ...]
    declarations: dict[str, tuple[str, ...]]
    diagnostics: tuple[str, ...]
    initialization: str | None = None


def _span(source: str, context: ParserRuleContext | None) -> str | None:
    if context is None or context.start is None or context.stop is None:
        return None
    return source[context.start.start : context.stop.stop + 1]


def _walk(context: ParserRuleContext):
    yield context
    for child in context.getChildren():
        if isinstance(child, ParserRuleContext):
            yield from _walk(child)


def _walk_current_routine(context: ParserRuleContext):
    """Keep nested routine statements out of the enclosing routine's facts."""
    yield context
    for child in context.getChildren():
        if not isinstance(child, ParserRuleContext):
            continue
        if child is not context and isinstance(child, (PlSqlParser.Procedure_bodyContext, PlSqlParser.Function_bodyContext)):
            continue
        yield from _walk_current_routine(child)


def _parse(source: str) -> ParserRuleContext | None:
    for mode in (PredictionMode.SLL, PredictionMode.LL):
        lexer = PlSqlLexer(InputStream(source))
        lexer_errors = CollectingErrorListener()
        lexer.removeErrorListeners()
        lexer.addErrorListener(lexer_errors)
        parser = PlSqlParser(CommonTokenStream(lexer))
        parser_errors = CollectingErrorListener()
        parser.removeErrorListeners()
        parser.addErrorListener(parser_errors)
        parser._interp.predictionMode = mode
        root = parser.sql_script()
        if not lexer_errors.errors and not parser_errors.errors:
            return root
    return None


def _parameters(source: str, context: ParserRuleContext) -> tuple[Parameter, ...]:
    result: list[Parameter] = []
    for position, parameter in enumerate(context.parameter(), 1):
        mode = "IN OUT" if parameter.INOUT() or (parameter.IN() and parameter.OUT()) else "OUT" if parameter.OUT() else "IN" if parameter.IN() else None
        result.append(Parameter(
            name=_span(source, parameter.parameter_name()) or "",
            position=position,
            mode=mode,
            data_type=_span(source, parameter.type_spec()),
            default=_span(source, parameter.default_value_part()),
            nocopy=bool(parameter.NOCOPY()),
            source=_span(source, parameter) or "",
        ))
    return tuple(result)


def _routine(source: str, context: ParserRuleContext, *, body: bool) -> Routine:
    kind = "FUNCTION" if isinstance(context, (PlSqlParser.Function_specContext, PlSqlParser.Function_bodyContext)) else "PROCEDURE"
    name = _span(source, context.identifier()) or ""
    parameters = _parameters(source, context)
    return_type = _span(source, context.type_spec()) if kind == "FUNCTION" else None
    declaration_end = context.body().start.start if body and context.body() is not None else context.stop.stop + 1
    signature = source[context.start.start : declaration_end].strip().rstrip(";").strip()
    transactions: list[str] = []
    exceptions: list[str] = []
    dynamic_sql: list[str] = []
    sql_statements: list[str] = []
    conditions: list[str] = []
    assignments: list[str] = []
    control_flow: list[str] = []
    raises: list[str] = []
    call_references: list[str] = []
    if body:
        for child in _walk_current_routine(context):
            if isinstance(child, (PlSqlParser.Commit_statementContext, PlSqlParser.Rollback_statementContext, PlSqlParser.Savepoint_statementContext)):
                transactions.append(_span(source, child) or "")
            elif isinstance(child, PlSqlParser.Exception_handlerContext):
                exceptions.append(_span(source, child) or "")
            elif isinstance(child, PlSqlParser.Execute_immediateContext):
                dynamic_sql.append(_span(source, child) or "")
            elif isinstance(child, PlSqlParser.Sql_statementContext) and child.data_manipulation_language_statements() is not None:
                sql_statements.append(_span(source, child) or "")
            elif isinstance(child, PlSqlParser.If_statementContext):
                condition = _span(source, child.condition())
                if condition:
                    conditions.append(condition)
                control_flow.append(_span(source, child) or "")
            elif isinstance(child, PlSqlParser.Loop_statementContext):
                condition = _span(source, child.condition())
                if condition:
                    conditions.append(condition)
                control_flow.append(_span(source, child) or "")
            elif isinstance(child, PlSqlParser.Case_statementContext):
                control_flow.append(_span(source, child) or "")
            elif isinstance(child, PlSqlParser.Assignment_statementContext):
                assignments.append(_span(source, child) or "")
            elif isinstance(child, PlSqlParser.Raise_statementContext):
                raises.append(_span(source, child) or "")
            elif isinstance(child, PlSqlParser.Call_statementContext):
                call_references.append(_span(source, child) or "")
    return Routine(
        name=name, kind=kind, signature=signature, return_type=return_type,
        parameters=parameters, source=_span(source, context) or "",
        start_line=context.start.line, end_line=context.stop.line,
        transactions=tuple(transactions), exception_handlers=tuple(exceptions),
        dynamic_sql=tuple(dynamic_sql), sql_statements=tuple(sql_statements),
        conditions=tuple(conditions), assignments=tuple(assignments),
        control_flow=tuple(control_flow), raises=tuple(raises),
        call_references=tuple(call_references),
    )


def extract_package(source: str, object_type: str) -> PackageExtraction:
    if object_type not in {"PACKAGE_SPEC", "PACKAGE_BODY"}:
        raise ValueError("Not a package object type")
    # This second parse runs after the bounded parser worker. Keep it small;
    # large package sources retain identity and source evidence as limited.
    if len(source.encode("utf-8")) > 128_000:
        return PackageExtraction(object_type, None, (), {}, ("PACKAGE_EXTRACTION_SIZE_LIMIT",))
    if "WRAPPED" in {token.text.upper() for token in _visible_tokens(source)}:
        return PackageExtraction(object_type, None, (), {}, ("WRAPPED_SOURCE_OPAQUE",))
    root = _parse(source)
    if root is None:
        return PackageExtraction(object_type, None, (), {}, ("PACKAGE_PARSE_UNRESOLVED",))
    expected = PlSqlParser.Create_packageContext if object_type == "PACKAGE_SPEC" else PlSqlParser.Create_package_bodyContext
    matches = [context for context in _walk(root) if isinstance(context, expected)]
    if len(matches) != 1:
        return PackageExtraction(object_type, None, (), {}, ("PACKAGE_IDENTITY_UNRESOLVED",))
    package = matches[0]
    members = package.package_obj_spec() if object_type == "PACKAGE_SPEC" else package.package_obj_body()
    routines: list[Routine] = []
    nested_routines = False
    declarations: dict[str, list[str]] = {
        "type_declaration": [], "constant": [], "variable_declaration": [], "cursor_declaration": [],
        "exception_declaration": [], "pragma_declaration": [],
    }
    for member in members:
        for method in ("procedure_spec", "function_spec", "procedure_body", "function_body"):
            context = getattr(member, method, lambda: None)()
            if context is not None:
                routines.append(_routine(source, context, body=method.endswith("_body")))
                if method.endswith("_body") and any(
                    child is not context and isinstance(child, (PlSqlParser.Procedure_bodyContext, PlSqlParser.Function_bodyContext))
                    for child in _walk(context)
                ):
                    nested_routines = True
        for method in declarations:
            if method == "constant":
                continue
            context = getattr(member, method, lambda: None)()
            if context is not None:
                value = _span(source, context) or ""
                tokens = code_tokens(value) if method == "variable_declaration" else None
                destination = "constant" if tokens and any(token.upper == "CONSTANT" for token in tokens) else method
                declarations[destination].append(value)
    authid = _span(source, package.invoker_rights_clause()) if object_type == "PACKAGE_SPEC" else None
    names = [(routine.kind, routine.name.upper()) for routine in routines]
    diagnostics = []
    if len(names) != len(set(names)):
        diagnostics.append("OVERLOAD_PAIRING_REQUIRES_REVIEW")
    if nested_routines:
        diagnostics.append("NESTED_ROUTINE_LIMITED")
    initialization = _span(source, package.seq_of_statements()) if object_type == "PACKAGE_BODY" else None
    return PackageExtraction(object_type, authid, tuple(routines), {key: tuple(value) for key, value in declarations.items()}, tuple(diagnostics), initialization)


def _visible_tokens(source: str):
    lexer = PlSqlLexer(InputStream(source))
    while True:
        token = lexer.nextToken()
        if token.type == -1:
            break
        if token.channel == 0:
            yield token
