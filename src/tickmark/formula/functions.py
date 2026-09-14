"""Function classification (check 6, and input to check 7).

Volatile functions force Excel to recalculate the whole dependency chain on every
edit. A handful of them in a large workbook is the usual reason a file "feels
broken" — which is a finding worth reporting even though nothing is numerically
wrong.
"""

from __future__ import annotations

# 04-tickmark.md check 6 names seven; the rest are volatile in current Excel and
# belong here for the same reason.
VOLATILE_FUNCTIONS: frozenset[str] = frozenset(
    {
        "CELL",
        "INDIRECT",
        "INFO",
        "NOW",
        "OFFSET",
        "RAND",
        "RANDARRAY",
        "RANDBETWEEN",
        "TODAY",
    }
)

# Volatile only in specific argument forms. Reported at lower confidence, because
# calling them volatile unconditionally produces noise on workbooks that use the
# safe form.
CONDITIONALLY_VOLATILE: frozenset[str] = frozenset({"INDEX", "SUMIF"})

# Lookups worth surfacing in the complexity ranking (check 7): they are the usual
# performance cliff in a large model, and the usual source of silent wrong
# answers when used with approximate match.
LOOKUP_FUNCTIONS: frozenset[str] = frozenset(
    {"HLOOKUP", "LOOKUP", "MATCH", "VLOOKUP", "XLOOKUP", "XMATCH"}
)


# Functions that consume a range and reduce it to one value. Checks 33 and 34
# key off these: a range argument to one of them is a claim about which cells
# belong together, and that claim can be wrong.
AGGREGATE_FUNCTIONS: frozenset[str] = frozenset(
    {
        "AVERAGE",
        "AVERAGEA",
        "COUNT",
        "COUNTA",
        "MAX",
        "MAXA",
        "MEDIAN",
        "MIN",
        "MINA",
        "PRODUCT",
        "STDEV",
        "STDEV.P",
        "STDEV.S",
        "SUM",
        "VAR",
        "VAR.P",
        "VAR.S",
    }
)


def is_aggregate(name: str) -> bool:
    """True if the function reduces a range to a single value."""
    return normalize(name) in AGGREGATE_FUNCTIONS


def normalize(name: str) -> str:
    """Normalise a function name for comparison.

    Strips the ``_xlfn.`` prefix that OOXML uses for functions newer than the
    file format, and the ``@`` implicit-intersection marker. Without this,
    ``_xlfn.XLOOKUP`` would never match ``XLOOKUP`` and the check would silently
    miss every modern workbook.
    """
    cleaned = name.strip().lstrip("@")
    for prefix in ("_xlfn.", "_xlws."):
        if cleaned.upper().startswith(prefix.upper()):
            cleaned = cleaned[len(prefix) :]
    return cleaned.upper()


def is_volatile(name: str) -> bool:
    """True if the function forces full recalculation on every edit."""
    return normalize(name) in VOLATILE_FUNCTIONS


def is_conditionally_volatile(name: str) -> bool:
    """True if the function is volatile only in some argument forms."""
    return normalize(name) in CONDITIONALLY_VOLATILE


def is_lookup(name: str) -> bool:
    """True if the function is a lookup worth flagging in complexity ranking."""
    return normalize(name) in LOOKUP_FUNCTIONS
