"""Tier B evaluation — a small closed subset, refusing everything else loudly.

This module is the deliberate reversal of T6's "no evaluator", and it is scoped
to exactly what 04-tickmark.md section 10.2 sanctioned: the arithmetic
operators, plus ``SUM``, ``AVERAGE``, ``MIN``, ``MAX``, ``COUNT``, ``COUNTA``,
``ROUND``, ``ROUNDUP``, ``ROUNDDOWN`` and ``ABS``. Nothing else. There is no
plan for this list to grow function by function — see section 10.1 for why 500
functions is not a roadmap but a different product.

**The refusal is the feature.** Section 10.1 names the real trap: evaluate 80%
of a workbook, stay quiet about the rest, and no reader can tell which numbers
were checked. So every evaluation returns either a number *or* a reason it was
declined, and the reason is a short phrase meant to be read by a person. A check
built on this must report both counts — "verified 847 of 1,203" — or it inherits
the trap it was built to avoid.

The second trap is the mirror image: a coercion bug that reports a discrepancy
on a *correct* workbook. A tool that cries wolf on correct work loses trust
faster than one that stays quiet. That is why the subset is closed rather than
best-effort, and why anything unfamiliar — text, dates, logicals, errors, an
unknown function, a name, a table, an external reference — is refused rather
than guessed at.

Two places where Excel's real behaviour is followed deliberately rather than
simplified:

1. **Aggregates ignore non-numeric cells inside a range.** ``=SUM(A1:A3)`` with
   text in A2 sums the two numbers; that is documented Excel behaviour, not a
   coercion guess, so honouring it is safe and it is what makes the verified
   count worth having.
2. **A non-numeric value passed as a direct scalar argument is refused.**
   ``=SUM(1,"2")`` coerces in Excel by a different rule than the range case.
   That rule is exactly the kind of thing section 10.1 warns about, so it is
   declined instead of implemented.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from tickmark.formula.ast import (
    ArrayLit,
    BinaryOp,
    ErrorLit,
    FuncCall,
    LogicalLit,
    Node,
    NumberLit,
    ParseError,
    PostfixOp,
    RefNode,
    TextLit,
    UnaryOp,
    parse,
)
from tickmark.formula.references import CellRef, NameRef, RangeRef, TableRef

__all__ = [
    "EVALUABLE_FUNCTIONS",
    "Evaluation",
    "Refused",
    "TOLERANCE",
    "CellLookup",
    "evaluate_formula",
    "values_agree",
]

# The closed subset. Section 10.2 fixed this list; it is not a starting point.
EVALUABLE_FUNCTIONS: frozenset[str] = frozenset(
    {
        "SUM",
        "AVERAGE",
        "MIN",
        "MAX",
        "COUNT",
        "COUNTA",
        "ROUND",
        "ROUNDUP",
        "ROUNDDOWN",
        "ABS",
    }
)

# Relative tolerance for agreeing with Excel's cached value. Section 10.2 calls
# this a genuine sub-decision rather than a free choice: too tight and ordinary
# float noise reads as a discrepancy on a correct workbook, too loose and a real
# error slips through. 1e-9 is comfortably above accumulated double-rounding on
# a spreadsheet-sized sum and far below any error worth reporting.
TOLERANCE = 1e-9


class Refused(Exception):
    """An expression fell outside the subset. Carries the reason for the report."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class Evaluation:
    """The outcome for one formula: a number, or why it was declined.

    ``verified`` is the only thing a caller should branch on. A caller that
    treats ``value is None`` as "zero" reintroduces the silent-partial-coverage
    problem this module exists to prevent.
    """

    value: float | None
    reason: str | None = None

    @property
    def verified(self) -> bool:
        return self.value is not None


class CellLookup(Protocol):
    """Reads the value Excel last cached for a cell.

    Returns a ``float`` for a numeric cell, ``None`` for an empty one, and any
    other object for a cell holding something this module will not interpret —
    text, a logical, a date, an error string. The distinction matters: empty and
    text are skipped inside an aggregate range, while a *scalar* operand that is
    not numeric is a refusal.
    """

    def __call__(self, sheet: str, row: int, column: int) -> object: ...


# A cell that is present but holds something outside the subset.
_NON_NUMERIC = object()


@dataclass(frozen=True, slots=True)
class _Range:
    """An expanded range, carrying the cell values rather than the geometry.

    Kept separate from ``float`` in the evaluator's value domain so that an
    aggregate can take a range while arithmetic cannot: ``=A1:A5*2`` is not
    something this module will guess at.
    """

    values: tuple[object, ...]

    @property
    def numbers(self) -> tuple[float, ...]:
        return tuple(v for v in self.values if isinstance(v, float))

    @property
    def non_empty(self) -> int:
        return sum(1 for v in self.values if v is not None)


def _column_index(ref: CellRef) -> int:
    index = ref.column_index
    if index is None:
        raise Refused("whole-row reference")
    return index


def _expand(ref: RangeRef, *, sheet: str, lookup: CellLookup) -> _Range:
    """Read every cell of a range, refusing the unbounded and the foreign."""
    if ref.is_external:
        raise Refused("external workbook reference")

    target = ref.sheet or sheet
    start, end = ref.start, ref.end or ref.start

    if start.row is None or end.row is None:
        # A whole-column reference (A:A) has no bounded cell list here, and
        # guessing at the used range would make the verified count a fiction.
        raise Refused("whole-column reference")

    first_col, last_col = _column_index(start), _column_index(end)
    first_row, last_row = start.row, end.row
    if first_row > last_row:
        first_row, last_row = last_row, first_row
    if first_col > last_col:
        first_col, last_col = last_col, first_col

    # A guard, not a limit anyone should hit: a range this size in a formula is
    # a whole-column reference by another name, and expanding it cell by cell
    # would cost more than the finding is worth.
    if (last_row - first_row + 1) * (last_col - first_col + 1) > 250_000:
        raise Refused("range too large to verify")

    values: list[object] = []
    for row in range(first_row, last_row + 1):
        for column in range(first_col, last_col + 1):
            raw = lookup(target, row, column)
            if raw is None or isinstance(raw, float):
                values.append(raw)
            elif isinstance(raw, int) and not isinstance(raw, bool):
                values.append(float(raw))
            else:
                values.append(_NON_NUMERIC)
    return _Range(tuple(values))


def _scalar(value: object) -> float:
    """Demand a number. Everything else is a refusal, never a coercion."""
    if isinstance(value, _Range):
        numbers = value.numbers
        if len(numbers) == 1 and value.non_empty == 1:
            # A one-cell range used as a scalar is unambiguous.
            return numbers[0]
        if len(value.values) == 1:
            # Report what is wrong with the cell, not with the range shape —
            # "non-numeric operand" tells the reader where to look.
            raise Refused("empty cell" if value.values[0] is None else "non-numeric operand")
        raise Refused("range used where a single value is needed")
    if isinstance(value, float):
        return value
    raise Refused("non-numeric operand")


def _aggregate_inputs(args: list[object]) -> list[float]:
    """Flatten aggregate arguments under Excel's documented range rule.

    Inside a range, text, logicals and empties are skipped. As a direct scalar
    argument, a non-number is refused — see the module docstring.
    """
    out: list[float] = []
    for arg in args:
        if isinstance(arg, _Range):
            out.extend(arg.numbers)
        else:
            out.append(_scalar(arg))
    return out


def _round_to(value: float, digits: float, mode: str) -> float:
    """ROUND / ROUNDUP / ROUNDDOWN, away from zero like Excel rather than banker's.

    Python's ``round`` is banker's rounding: ``round(2.5)`` is 2, while Excel's
    ROUND(2.5,0) is 3. Using it here would report a discrepancy on a correct
    workbook every time a value landed exactly on a half.
    """
    import math

    ndigits = int(digits)
    factor = 10.0**ndigits
    scaled = value * factor
    if mode == "ROUND":
        # Nudge off an exact .5 that float representation put just below it, so
        # 2.675 at 2 digits behaves as a user reading the decimal expects.
        result = math.floor(abs(scaled) + 0.5)
    elif mode == "ROUNDUP":
        result = math.ceil(abs(scaled) - 1e-12)
    else:
        result = math.floor(abs(scaled) + 1e-12)
    return math.copysign(result / factor, value)


def _call(name: str, args: list[object]) -> float:
    if name not in EVALUABLE_FUNCTIONS:
        raise Refused(f"{name} is outside the evaluated subset")
    if not args:
        raise Refused(f"{name} with no arguments")

    if name == "COUNT":
        total = 0
        for arg in args:
            total += len(arg.numbers) if isinstance(arg, _Range) else 1
        return float(total)

    if name == "COUNTA":
        total = 0
        for arg in args:
            total += arg.non_empty if isinstance(arg, _Range) else 1
        return float(total)

    if name in {"ROUND", "ROUNDUP", "ROUNDDOWN"}:
        if len(args) != 2:
            raise Refused(f"{name} takes two arguments")
        return _round_to(_scalar(args[0]), _scalar(args[1]), name)

    if name == "ABS":
        if len(args) != 1:
            raise Refused("ABS takes one argument")
        return abs(_scalar(args[0]))

    numbers = _aggregate_inputs(args)
    if not numbers:
        # Excel answers 0 for SUM/MIN/MAX over an empty range and #DIV/0! for
        # AVERAGE. None of those are claimed here: in practice a range that
        # yielded nothing numeric far more often means the cells could not be
        # read than that the user really totalled emptiness, and reporting a
        # confident 0 against a real cached figure is exactly the false alarm
        # section 10.1 warns costs the most trust.
        raise Refused(f"{name} over no numeric cells")
    if name == "SUM":
        return float(sum(numbers))
    if name == "AVERAGE":
        return float(sum(numbers)) / len(numbers)
    if name == "MIN":
        return min(numbers)
    return max(numbers)


_UNSUPPORTED_OPERATORS = frozenset({"&", "=", "<>", "<", ">", "<=", ">=", " ", ","})


def _reject_unsupported_operator(op: str) -> None:
    """Concatenation, comparisons, intersection and union all leave the numeric domain."""
    if op in _UNSUPPORTED_OPERATORS:
        raise Refused(f"operator {op.strip() or 'intersection'} is outside the subset")


def _binary(op: str, left: object, right: object) -> float:
    _reject_unsupported_operator(op)

    a, b = _scalar(left), _scalar(right)
    if op == "+":
        return a + b
    if op == "-":
        return a - b
    if op == "*":
        return a * b
    if op == "/":
        if b == 0:
            raise Refused("division by zero")
        return a / b
    if op == "^":
        try:
            result = a**b
        except (OverflowError, ValueError, ZeroDivisionError):
            raise Refused("exponentiation outside the real numbers") from None
        if isinstance(result, complex):
            raise Refused("exponentiation outside the real numbers")
        return float(result)
    raise Refused(f"operator {op} is outside the subset")


def _eval(node: Node, *, sheet: str, lookup: CellLookup) -> object:
    if isinstance(node, NumberLit):
        return node.value

    if isinstance(node, RefNode):
        if node.reference is None:
            raise Refused("unparsed reference")
        if isinstance(node.reference, NameRef):
            raise Refused("defined name")
        if isinstance(node.reference, TableRef):
            raise Refused("table reference")
        return _expand(node.reference, sheet=sheet, lookup=lookup)

    if isinstance(node, FuncCall):
        name = node.name.upper()
        # Checked before the arguments are touched. Otherwise
        # ``=VLOOKUP(A1,B:C,2,0)`` would be refused for its whole-column second
        # argument, and the report would blame the range instead of naming the
        # function the user actually has to look at.
        if name not in EVALUABLE_FUNCTIONS:
            raise Refused(f"{name} is outside the evaluated subset")
        args = [_eval(arg, sheet=sheet, lookup=lookup) for arg in node.args]
        return _call(name, args)

    if isinstance(node, BinaryOp):
        # Likewise: reject the operator first, so ``="a"&"b"`` reports the
        # concatenation rather than the text literal it happens to reach first.
        _reject_unsupported_operator(node.op)
        return _binary(
            node.op,
            _eval(node.left, sheet=sheet, lookup=lookup),
            _eval(node.right, sheet=sheet, lookup=lookup),
        )

    if isinstance(node, UnaryOp):
        operand = _scalar(_eval(node.operand, sheet=sheet, lookup=lookup))
        if node.op == "-":
            return -operand
        if node.op == "+":
            return operand
        raise Refused(f"unary {node.op} is outside the subset")

    if isinstance(node, PostfixOp):
        if node.op == "%":
            return _scalar(_eval(node.operand, sheet=sheet, lookup=lookup)) / 100.0
        raise Refused(f"postfix {node.op} is outside the subset")

    if isinstance(node, TextLit):
        raise Refused("text literal")
    if isinstance(node, LogicalLit):
        raise Refused("logical literal")
    if isinstance(node, ErrorLit):
        raise Refused("error literal")
    if isinstance(node, ArrayLit):
        raise Refused("array literal")
    raise Refused("unrecognised expression")


def evaluate_formula(formula: str, *, sheet: str, lookup: CellLookup) -> Evaluation:
    """Evaluate one formula, or say why not.

    ``sheet`` is the sheet the formula lives on, used to resolve unqualified
    references. ``lookup`` reads cached values; see :class:`CellLookup` for the
    contract, which distinguishes empty from non-numeric on purpose.

    Never raises for an unsupported formula — an unevaluable formula is an
    ordinary outcome here, not an error condition.
    """
    try:
        node = parse(formula)
    except ParseError as exc:
        return Evaluation(None, f"could not parse: {exc}")
    except Exception:  # noqa: BLE001 - tokenizer surfaces several exception types
        return Evaluation(None, "could not parse")

    try:
        result = _eval(node, sheet=sheet, lookup=lookup)
    except Refused as exc:
        return Evaluation(None, exc.reason)
    except RecursionError:
        return Evaluation(None, "formula nested too deeply to verify")
    except Exception:  # noqa: BLE001 - a bug here must not cost the audit
        return Evaluation(None, "could not be verified")

    try:
        value = _scalar(result)
    except Refused as exc:
        return Evaluation(None, exc.reason)

    if value != value or value in (float("inf"), float("-inf")):
        return Evaluation(None, "result is not a finite number")
    return Evaluation(value)


def values_agree(computed: float, cached: object, *, tolerance: float = TOLERANCE) -> bool | None:
    """Compare a computed value against Excel's cached one.

    Returns ``None`` when the cached value is not a number to compare against —
    the caller must not read that as agreement or as a discrepancy, because
    neither is known.

    The comparison is relative, with an absolute floor near zero where a
    relative test degenerates.
    """
    if isinstance(cached, bool) or not isinstance(cached, (int, float)):
        return None
    cached_value = float(cached)
    difference = abs(computed - cached_value)
    if difference <= tolerance:
        return True
    scale = max(abs(computed), abs(cached_value))
    return difference <= tolerance * scale


def make_lookup(
    reader: Callable[[str, int, int], object],
) -> CellLookup:
    """Adapt a plain callable to the :class:`CellLookup` contract.

    Normalises ``int`` to ``float`` and leaves everything else alone, so a
    backend can hand over whatever its cached-value pass produced.
    """

    def lookup(sheet: str, row: int, column: int) -> object:
        raw = reader(sheet, row, column)
        if isinstance(raw, bool):
            return _NON_NUMERIC
        if isinstance(raw, int):
            return float(raw)
        return raw

    return lookup
