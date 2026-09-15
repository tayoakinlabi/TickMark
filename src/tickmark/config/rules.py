"""Tunable rules (T4).

T4 resolves the hardcoded-constant ignore list into a config file, and says it
should be the same file that later holds custom rules (item 19). This module is
that structure. The *file format* is deliberately not decided here — everything
loads from a plain mapping, so whichever format wins later is a thin adapter
rather than a refactor, and no dependency is taken before it is needed.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

__all__ = ["Rules", "DEFAULT_RULES"]

# Literals that carry no business meaning. A formula containing *100 or /12 is
# doing arithmetic, not hiding a rate; flagging those is how a check becomes
# noise that people switch off.
_DEFAULT_IGNORED_NUMBERS: frozenset[float] = frozenset(
    {
        0.0,
        1.0,
        -1.0,
        2.0,
        0.5,
        7.0,  # days in a week
        12.0,  # months
        24.0,  # hours
        30.0,
        31.0,
        52.0,  # weeks
        60.0,  # minutes, seconds
        100.0,  # percentages
        365.0,
        1000.0,
    }
)

# Positions where a bare number is the idiomatic way to write the formula, not a
# buried constant. Keyed by function name to the 0-based argument indexes that
# are structural rather than numeric-business values.
_DEFAULT_STRUCTURAL_ARGS: dict[str, frozenset[int]] = {
    "VLOOKUP": frozenset({2}),  # column index
    "HLOOKUP": frozenset({2}),  # row index
    "INDEX": frozenset({1, 2}),  # row, column
    "MATCH": frozenset({2}),  # match type
    "XLOOKUP": frozenset({4, 5}),  # match mode, search mode
    "OFFSET": frozenset({1, 2, 3, 4}),
    "ROUND": frozenset({1}),
    "ROUNDUP": frozenset({1}),
    "ROUNDDOWN": frozenset({1}),
    "MROUND": frozenset({1}),
    "LEFT": frozenset({1}),
    "RIGHT": frozenset({1}),
    "MID": frozenset({1, 2}),
    "LARGE": frozenset({1}),
    "SMALL": frozenset({1}),
    "SUBTOTAL": frozenset({0}),  # aggregation code
    "WEEKDAY": frozenset({1}),
    "TEXT": frozenset(),
    "RANK": frozenset({2}),
    "PERCENTILE": frozenset({1}),
    "QUARTILE": frozenset({1}),
}


@dataclass(frozen=True, slots=True)
class Rules:
    """Everything a user can tune, in one place."""

    ignored_numbers: frozenset[float] = _DEFAULT_IGNORED_NUMBERS
    structural_args: dict[str, frozenset[int]] = field(
        default_factory=lambda: dict(_DEFAULT_STRUCTURAL_ARGS)
    )
    # Check 1 gates. See tickmark.checks.inconsistent_range for why there are two.
    min_run_length: int = 4
    dominance: float = 0.75
    # Whether an integer literal in ordinary arithmetic is worth reporting at all.
    # Off by default: integers are usually counts, and the finding that pays for
    # itself is the non-integer rate.
    report_integer_constants: bool = False
    # How many complex formulas check 7 names. Bounded because "this is
    # complicated" repeated four hundred times is wallpaper, not advice.
    complexity_limit: int = 10
    # Check 35, the tier B evaluator. On by default: it only ever reports a
    # disagreement between a formula and the value stored beside it, which is a
    # fact about the file rather than a judgement about it. Switchable because
    # it is the one check that costs real time on a large workbook.
    evaluate_formulas: bool = True

    def is_ignored_number(self, value: float) -> bool:
        return value in self.ignored_numbers

    def is_structural_arg(self, function: str, index: int) -> bool:
        return index in self.structural_args.get(function.upper(), frozenset())

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> Rules:
        """Build rules from a parsed config mapping.

        Unknown keys are ignored rather than rejected: a config written for a
        later version should not stop an older build from running, and a typo
        should not be a crash in a tool someone runs on a deadline.
        """
        base = cls()
        updates: dict[str, Any] = {}

        if (numbers := data.get("ignored_numbers")) is not None:
            updates["ignored_numbers"] = frozenset(float(n) for n in numbers)
        if (args := data.get("structural_args")) is not None:
            merged = dict(base.structural_args)
            for name, indexes in args.items():
                merged[str(name).upper()] = frozenset(int(i) for i in indexes)
            updates["structural_args"] = merged
        if (value := data.get("min_run_length")) is not None:
            updates["min_run_length"] = int(value)
        if (value := data.get("dominance")) is not None:
            updates["dominance"] = float(value)
        if (value := data.get("report_integer_constants")) is not None:
            updates["report_integer_constants"] = bool(value)
        if (value := data.get("complexity_limit")) is not None:
            updates["complexity_limit"] = int(value)
        if (value := data.get("evaluate_formulas")) is not None:
            updates["evaluate_formulas"] = bool(value)

        return replace(base, **updates)


DEFAULT_RULES = Rules()
