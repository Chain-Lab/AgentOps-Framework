"""Safe condition expression evaluator for DAG workflows (Phase 13.3).

Supports a restricted DSL for conditional node execution:

    nodes.<node_id>.status == "completed"
    nodes.<node_id>.output.<field> == "value"
    nodes.<node_id>.output.<field> > number
    <expr> and <expr>
    <expr> or <expr>

Uses a hand-written recursive-descent parser — never calls eval().
"""

from __future__ import annotations

import re
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ConditionEvaluationError(Exception):
    """Raised when a condition expression cannot be evaluated."""


# ---------------------------------------------------------------------------
# AST nodes
# ---------------------------------------------------------------------------


@dataclass
class Identifier:
    name: str


@dataclass
class NumberLiteral:
    value: float


@dataclass
class StringLiteral:
    value: str


@dataclass
class BooleanLiteral:
    value: bool


@dataclass
class InExpression:
    left: Identifier
    op: str  # "in" | "not_in" | "starts_with" | "ends_with"
    right: list[Any]  # list of literal values for IN / list for STARTS_WITH


@dataclass
class Comparison:
    left: Identifier
    op: str
    right: NumberLiteral | StringLiteral | BooleanLiteral


@dataclass
class LogicalOp:
    op: str  # "and" | "or"
    left: Comparison | LogicalOp
    right: Comparison | LogicalOp


# ---------------------------------------------------------------------------
# Tokeniser
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(
    r'\s*('
    r'"(?:[^"\\]|\\.)*"'          # double-quoted string
    r"|'(?:[^'\\]|\\.)*'"          # single-quoted string
    r'|==|!=|>=|<=|>|<'           # comparison operators
    r'|\bAND\b|\bOR\b'             # logical operators (case-insensitive)
    r'|\bNOT\b'                    # unary not
    r'|\bSTARTS_WITH\b|\bENDS_WITH\b'  # string operators (before IN to avoid partial match)
    r'|\bIN\b'                     # IN operator
    r'|\bTRUE\b|\bFALSE\b'         # boolean literals
    r'|[0-9]+(?:\.[0-9]+)?'        # number
    r'|[a-zA-Z_][a-zA-Z0-9_.]*'   # identifier / path
    r'|\[|\]'                      # list brackets
    r'|,'                          # comma (for list elements)
    r'|[()]'                       # parens
    r')'
)

_COMPARISON_OPS = {"==", "!=", ">", ">=", "<", "<="}
_LOGICAL_OPS = {"AND", "OR"}
_BOOL_LITERALS = {"TRUE", "FALSE"}
_SET_OPS = {"IN", "STARTS_WITH", "ENDS_WITH"}


def _tokenize(expr: str) -> list[tuple[str, int]]:
    """Return list of (token, position) tuples."""
    tokens: list[tuple[str, int]] = []
    pos = 0
    while pos < len(expr):
        m = _TOKEN_RE.match(expr, pos)
        if m is None:
            raise ConditionEvaluationError(
                f"Unexpected character at position {pos} in condition: {expr!r}"
            )
        token = m.group(1).strip()
        tokens.append((token, m.start()))
        pos = m.end()
    return tokens


# ---------------------------------------------------------------------------
# Parser (recursive descent)
# ---------------------------------------------------------------------------


class _Parser:
    def __init__(self, expr: str) -> None:
        self.tokens = _tokenize(expr)
        self.tokens.append(("EOF", len(expr)))
        self.pos = 0

    def _current(self) -> str:
        return self.tokens[self.pos][0]

    def _advance(self) -> str:
        tok = self._current()
        self.pos += 1
        return tok

    def _expect(self, token: str) -> None:
        if self._current().upper() != token.upper():
            raise ConditionEvaluationError(
                f"Expected '{token}' but got '{self._current()}' "
                f"at position {self.tokens[self.pos][1]} in condition: ..."
            )
        self._advance()

    def parse(self) -> Comparison | LogicalOp:
        result = self._parse_or()
        self._expect("EOF")
        return result

    def _parse_or(self) -> Comparison | LogicalOp:
        left = self._parse_and()
        while self._current().upper() == "OR":
            self._advance()
            right = self._parse_and()
            left = LogicalOp(op="or", left=left, right=right)
        return left

    def _parse_and(self) -> Comparison | LogicalOp:
        left = self._parse_not()
        while self._current().upper() == "AND":
            self._advance()
            right = self._parse_not()
            left = LogicalOp(op="and", left=left, right=right)
        return left

    def _parse_not(self) -> Comparison | LogicalOp:
        if self._current().upper() == "NOT":
            self._advance()
            operand = self._parse_not()
            # Wrap NOT as a special logical op with left=operand, right=None
            return LogicalOp(op="not", left=operand, right=None)  # type: ignore[arg-type]
        return self._parse_comparison()

    def _parse_comparison(self) -> Comparison | LogicalOp | InExpression:
        left_str = self._advance()  # should be an identifier path
        if self._current() in _COMPARISON_OPS:
            op = self._advance()
            right = self._parse_value()
            left_id = _parse_identifier(left_str)
            return Comparison(left=left_id, op=op, right=right)
        elif self._current().upper() == "NOT":
            # Check for NOT IN, NOT STARTS_WITH, NOT ENDS_WITH
            self._advance()
            next_tok = self._current().upper()
            if next_tok == "IN":
                self._advance()
                right_list = self._parse_list()
                left_id = _parse_identifier(left_str)
                return InExpression(left=left_id, op="not_in", right=right_list)
            elif next_tok == "STARTS_WITH":
                self._advance()
                right_val = self._parse_value()
                left_id = _parse_identifier(left_str)
                return InExpression(left=left_id, op="not_starts_with", right=[right_val])
            elif next_tok == "ENDS_WITH":
                self._advance()
                right_val = self._parse_value()
                left_id = _parse_identifier(left_str)
                return InExpression(left=left_id, op="not_ends_with", right=[right_val])
            else:
                raise ConditionEvaluationError(
                    f"Unexpected token '{self._current()}' after 'NOT' "
                    f"at position {self.tokens[self.pos][1]} in condition"
                )
        elif self._current().upper() == "IN":
            self._advance()
            right_list = self._parse_list()
            left_id = _parse_identifier(left_str)
            return InExpression(left=left_id, op="in", right=right_list)
        elif self._current().upper() == "STARTS_WITH":
            self._advance()
            right_val = self._parse_value()
            left_id = _parse_identifier(left_str)
            return InExpression(left=left_id, op="starts_with", right=[right_val])
        elif self._current().upper() == "ENDS_WITH":
            self._advance()
            right_val = self._parse_value()
            left_id = _parse_identifier(left_str)
            return InExpression(left=left_id, op="ends_with", right=[right_val])
        elif self._current().upper() in _LOGICAL_OPS or self._current() == "EOF":
            # Bare identifier — treat as boolean truthiness
            left_id = _parse_identifier(left_str)
            return Comparison(left=left_id, op="truthy", right=BooleanLiteral(True))
        else:
            raise ConditionEvaluationError(
                f"Unexpected token '{self._current()}' after identifier "
                f"'{left_str}' at position {self.tokens[self.pos][1]} in condition"
            )

    def _parse_list(self) -> list[NumberLiteral | StringLiteral | BooleanLiteral]:
        """Parse a bracketed list of literal values: [1, "a", TRUE]."""
        if self._current() != "[":
            raise ConditionEvaluationError(
                f"Expected '[' but got '{self._current()}' "
                f"at position {self.tokens[self.pos][1]} in condition"
            )
        self._advance()  # consume [
        values: list[NumberLiteral | StringLiteral | BooleanLiteral] = []
        if self._current() != "]":
            while True:
                val = self._parse_value()
                values.append(val)
                if self._current() == "]":
                    self._advance()  # consume ]
                    break
                if self._current() != ",":
                    raise ConditionEvaluationError(
                        f"Expected ',' or ']' but got '{self._current()}' "
                        f"at position {self.tokens[self.pos][1]} in condition"
                    )
                self._advance()  # consume ,
        else:
            self._advance()  # consume empty []
        return values

    def _parse_value(self) -> NumberLiteral | StringLiteral | BooleanLiteral:
        tok = self._current()
        if tok.upper() in _BOOL_LITERALS:
            self._advance()
            return BooleanLiteral(tok.upper() == "TRUE")
        if tok.startswith(("'", '"')):
            return self._parse_string()
        # Try number first, then identifier
        try:
            val = float(tok)
            self._advance()
            return NumberLiteral(val)
        except ValueError:
            raise ConditionEvaluationError(
                f"Expected string, number, or boolean literal but got '{tok}' "
                f"at position {self.tokens[self.pos][1]} in condition"
            )

    def _parse_string(self) -> StringLiteral:
        raw = self._advance()
        # Strip quotes and unescape
        inner = raw[1:-1]
        inner = inner.replace('\\"', '"').replace("\\'", "'")
        return StringLiteral(inner)


def _parse_identifier(path: str) -> Identifier:
    """Parse 'nodes.<node_id>.status' or 'nodes.<node_id>.output.<field>'."""
    parts = path.split(".")
    if len(parts) < 3 or parts[0].lower() != "nodes":
        raise ConditionEvaluationError(
            f"Invalid identifier '{path}' — must be of the form "
            f"'nodes.<node_id>.status' or 'nodes.<node_id>.output.<field>'"
        )
    if parts[2].lower() != "status" and parts[2].lower() != "output":
        raise ConditionEvaluationError(
            f"Invalid identifier '{path}' — second segment must be 'status' "
            f"or 'output', got '{parts[2]}'"
        )
    if parts[2].lower() == "status" and len(parts) != 3:
        raise ConditionEvaluationError(
            f"Invalid identifier '{path}' — 'status' takes no field, "
            f"use 'nodes.<node_id>.status'"
        )
    if parts[2].lower() == "output" and len(parts) < 4:
        raise ConditionEvaluationError(
            f"Invalid identifier '{path}' — 'output' requires a field name, "
            f"use 'nodes.<node_id>.output.<field>'"
        )
    return Identifier(name=path)


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------


def _resolve(identifier: Identifier, node_results: dict[str, NodeExecutionResult]) -> Any:
    """Resolve an identifier against node execution results."""
    parts = identifier.name.split(".")
    node_id = parts[1]
    if node_id not in node_results:
        raise ConditionEvaluationError(
            f"Unknown node '{node_id}' in condition expression "
            f"'{identifier.name}' — node has not been executed yet"
        )
    result = node_results[node_id]
    segment = parts[2].lower()
    if segment == "status":
        return result.status.value
    elif segment == "output":
        field = ".".join(parts[3:])
        output = result.output
        if isinstance(output, dict):
            if field not in output:
                raise ConditionEvaluationError(
                    f"Unknown output field '{field}' for node '{node_id}' "
                    f"in condition expression '{identifier.name}'"
                )
            return output[field]
        raise ConditionEvaluationError(
            f"Node '{node_id}' output is not a dict — "
            f"cannot access field '{field}' in condition expression "
            f"'{identifier.name}'"
        )
    raise ConditionEvaluationError(
        f"Invalid identifier path '{identifier.name}'"
    )


def _compare(left: Any, op: str, right: Any) -> bool:
    """Perform a comparison, returning a boolean."""
    if op == "truthy":
        return bool(left)
    if op == "==":
        return left == right
    if op == "!=":
        return left != right
    if isinstance(left, str) or isinstance(right, str):
        raise ConditionEvaluationError(
            f"Cannot compare string ({left!r}) with number using '{op}'"
        )
    if op == ">":
        return left > right
    if op == ">=":
        return left >= right
    if op == "<":
        return left < right
    if op == "<=":
        return left <= right
    raise ConditionEvaluationError(f"Unsupported comparison operator: {op}")


def _eval_node(
    node: Comparison | LogicalOp | InExpression,
    node_results: dict[str, NodeExecutionResult],
) -> bool:
    """Recursively evaluate an AST node."""
    if isinstance(node, Comparison):
        if node.op == "truthy":
            raw = _resolve(node.left, node_results)
            return bool(raw)
        left_val = _resolve(node.left, node_results)
        right_val = node.right.value if hasattr(node.right, "value") else node.right  # type: ignore[union-attr]
        return _compare(left_val, node.op, right_val)
    elif isinstance(node, InExpression):
        left_val = _resolve(node.left, node_results)
        right_values = [v.value if hasattr(v, "value") else v for v in node.right]  # type: ignore[union-attr]
        if node.op == "in":
            return left_val in right_values
        elif node.op == "not_in":
            return left_val not in right_values
        elif node.op == "starts_with":
            if not isinstance(left_val, str):
                raise ConditionEvaluationError(
                    f"STARTS_WITH requires string value, got {type(left_val).__name__}"
                )
            return any(
                isinstance(rv, str) and left_val.startswith(rv)
                for rv in right_values
            )
        elif node.op == "ends_with":
            if not isinstance(left_val, str):
                raise ConditionEvaluationError(
                    f"ENDS_WITH requires string value, got {type(left_val).__name__}"
                )
            return any(
                isinstance(rv, str) and left_val.endswith(rv)
                for rv in right_values
            )
        elif node.op == "not_starts_with":
            if not isinstance(left_val, str):
                raise ConditionEvaluationError(
                    f"NOT STARTS_WITH requires string value, got {type(left_val).__name__}"
                )
            return not any(
                isinstance(rv, str) and left_val.startswith(rv)
                for rv in right_values
            )
        elif node.op == "not_ends_with":
            if not isinstance(left_val, str):
                raise ConditionEvaluationError(
                    f"NOT ENDS_WITH requires string value, got {type(left_val).__name__}"
                )
            return not any(
                isinstance(rv, str) and left_val.endswith(rv)
                for rv in right_values
            )
        raise ConditionEvaluationError(f"Unknown set operator: {node.op}")
    elif isinstance(node, LogicalOp):
        left_val = _eval_node(node.left, node_results)
        if node.op == "not":
            return not left_val
        right_val = _eval_node(node.right, node_results)  # type: ignore[arg-type]
        if node.op == "and":
            return left_val and right_val
        if node.op == "or":
            return left_val or right_val
        raise ConditionEvaluationError(f"Unknown logical operator: {node.op}")
    raise ConditionEvaluationError(f"Unknown AST node type: {type(node)}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def evaluate_condition(
    condition: "DagCondition",
    node_results: dict[str, "NodeExecutionResult"],
) -> bool:
    """Evaluate a DAG condition expression against current node results.

    Args:
        condition: The DagCondition with an expr string.
        node_results: Mapping of node_id -> NodeExecutionResult for
                      all nodes that have already executed.

    Returns:
        True if the condition is satisfied, False otherwise.

    Raises:
        ConditionEvaluationError: On parse errors, unknown nodes, or
                                  unknown output fields.
    """
    try:
        ast = _Parser(condition.expr).parse()
    except ConditionEvaluationError:
        raise
    except Exception as exc:
        raise ConditionEvaluationError(
            f"Failed to parse condition '{condition.expr}': {exc}"
        ) from exc
    return _eval_node(ast, node_results)


def resolve_expression_value(
    expr: str,
    node_results: dict[str, "NodeExecutionResult"],
) -> Any:
    """Resolve an expression to its raw value (not boolean).

    Used by SWITCH nodes to evaluate the switch expression and get the
    actual value for case matching. Supports identifier paths like
    ``nodes.<id>.output.<field>``.

    Args:
        expr: The expression string to evaluate.
        node_results: Mapping of node_id -> NodeExecutionResult.

    Returns:
        The resolved value (string, number, bool, etc.).

    Raises:
        ConditionEvaluationError: On parse errors, unknown nodes, or
                                  unknown output fields.
    """
    try:
        ast = _Parser(expr).parse()
    except ConditionEvaluationError:
        raise
    except Exception as exc:
        raise ConditionEvaluationError(
            f"Failed to parse expression '{expr}': {exc}"
        ) from exc

    # For switch expressions, we want the raw value, not boolean evaluation
    # Handle simple identifier paths: nodes.<id>.output.<field>
    if isinstance(ast, Identifier):
        return _resolve(ast, node_results)
    # Handle bare comparisons (for literal values)
    if isinstance(ast, Comparison):
        if ast.op == "truthy":
            return _resolve(ast.left, node_results)
        left_val = _resolve(ast.left, node_results)
        right_val = ast.right.value if hasattr(ast.right, "value") else ast.right
        return right_val
    # For InExpression, return the left side value
    if isinstance(ast, InExpression):
        return _resolve(ast.left, node_results)
    # For logical ops, return the left side (best effort)
    if isinstance(ast, LogicalOp):
        return _resolve_value(ast, node_results)

    raise ConditionEvaluationError(
        f"Cannot resolve value from expression '{expr}': unsupported AST node"
    )


def _resolve_value(
    node: Comparison | LogicalOp | InExpression,
    node_results: dict[str, "NodeExecutionResult"],
) -> Any:
    """Extract a representative value from an AST node for switch matching."""
    if isinstance(node, Comparison):
        if node.op == "truthy":
            return _resolve(node.left, node_results)
        return _resolve(node.left, node_results)
    if isinstance(node, InExpression):
        return _resolve(node.left, node_results)
    if isinstance(node, LogicalOp):
        # For logical ops, return the left value
        return _resolve_value(node.left, node_results)
    raise ConditionEvaluationError(f"Cannot resolve value from {type(node)}")


# ---------------------------------------------------------------------------
# DagCondition model (lazy import to avoid circular deps)
# ---------------------------------------------------------------------------

from pydantic import BaseModel, Field  # noqa: E402


class DagCondition(BaseModel):
    """A boolean condition that gates DAG node execution.

    Uses a safe, restricted expression DSL — never calls eval().

    Supported expressions:
        nodes.<id>.status == "completed"
        nodes.<id>.output.<field> == "value"
        nodes.<id>.output.<field> > number
        nodes.<id>.output.<field> IN ["a", "b", "c"]
        NOT nodes.<id>.output.<field> IN ["a", "b"]
        nodes.<id>.output.<field> STARTS_WITH "prefix"
        nodes.<id>.output.<field> ENDS_WITH ".txt"
        <expr> AND <expr>
        <expr> OR <expr>
        NOT <expr>
    """

    expr: str = Field(..., description="Condition expression (safe DSL)")
