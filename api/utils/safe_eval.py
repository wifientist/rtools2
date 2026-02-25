"""
Safe expression evaluator for workflow skip_if conditions.

Replaces bare eval() with an AST-based evaluator that only allows:
- Boolean operators: and, or, not
- Comparisons: ==, !=, <, >, <=, >=, is, is not, in, not in
- Constants: strings, numbers, booleans, None
- Name lookup: only 'options' (from the provided variables)
- Attribute access: .get() on dicts
- Method calls: dict.get(key) and dict.get(key, default)
"""
import ast
import operator

# Allowed comparison operators
_CMP_OPS = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.Gt: operator.gt,
    ast.LtE: operator.le,
    ast.GtE: operator.ge,
    ast.Is: operator.is_,
    ast.IsNot: operator.is_not,
}


class _SafeEvalError(Exception):
    pass


def _safe_eval_node(node, variables: dict):
    """Recursively evaluate an AST node with only safe operations."""

    # Constants: "per_unit", 42, True, None, False
    if isinstance(node, ast.Constant):
        return node.value

    # Name lookup: 'options'
    if isinstance(node, ast.Name):
        if node.id in variables:
            return variables[node.id]
        raise _SafeEvalError(f"Name '{node.id}' is not allowed")

    # Boolean operators: and, or
    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            result = True
            for val in node.values:
                result = _safe_eval_node(val, variables)
                if not result:
                    return result
            return result
        elif isinstance(node.op, ast.Or):
            result = False
            for val in node.values:
                result = _safe_eval_node(val, variables)
                if result:
                    return result
            return result

    # Unary operators: not, -
    if isinstance(node, ast.UnaryOp):
        operand = _safe_eval_node(node.operand, variables)
        if isinstance(node.op, ast.Not):
            return not operand
        if isinstance(node.op, ast.USub):
            return -operand
        raise _SafeEvalError(f"Unary operator {type(node.op).__name__} not allowed")

    # Comparisons: ==, !=, <, >, etc.
    if isinstance(node, ast.Compare):
        left = _safe_eval_node(node.left, variables)
        for op, comparator in zip(node.ops, node.comparators):
            right = _safe_eval_node(comparator, variables)
            op_type = type(op)

            if op_type in _CMP_OPS:
                if not _CMP_OPS[op_type](left, right):
                    return False
            elif op_type is ast.In:
                if left not in right:
                    return False
            elif op_type is ast.NotIn:
                if left in right:
                    return False
            else:
                raise _SafeEvalError(f"Comparison {op_type.__name__} not allowed")
            left = right
        return True

    # Method calls: options.get('key') or options.get('key', default)
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Attribute):
            obj = _safe_eval_node(node.func.value, variables)
            method_name = node.func.attr
            if method_name != "get" or not isinstance(obj, dict):
                raise _SafeEvalError(f"Only dict.get() calls are allowed, got .{method_name}()")
            args = [_safe_eval_node(a, variables) for a in node.args]
            if len(args) < 1 or len(args) > 2:
                raise _SafeEvalError("dict.get() requires 1-2 arguments")
            return obj.get(*args)
        raise _SafeEvalError("Only method calls (e.g. options.get()) are allowed")

    # Attribute access without call (not currently needed, but safe for dict)
    if isinstance(node, ast.Attribute):
        obj = _safe_eval_node(node.value, variables)
        if isinstance(obj, dict) and node.attr == "get":
            return obj.get
        raise _SafeEvalError(f"Attribute access .{node.attr} not allowed")

    # Expression wrapper
    if isinstance(node, ast.Expression):
        return _safe_eval_node(node.body, variables)

    raise _SafeEvalError(f"AST node type {type(node).__name__} not allowed")


def safe_eval(expression: str, variables: dict):
    """
    Safely evaluate a simple boolean expression.

    Only allows boolean logic, comparisons, constants, and dict.get() calls.
    Raises ValueError on disallowed operations.

    Usage:
        safe_eval("not options.get('configure_lan_ports', False)", {"options": job.options})
    """
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as e:
        raise ValueError(f"Invalid expression: {e}")

    try:
        return _safe_eval_node(tree, variables)
    except _SafeEvalError as e:
        raise ValueError(f"Disallowed operation in expression: {e}")
