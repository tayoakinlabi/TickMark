"""Loading ``tickmark.toml`` — the answer to T4, and the home of item 19.

T4 asked where check 2's hardcoded-constant ignore list should live, and said it
should be the same file that later holds user-defined rules. This is that file.

**TOML rather than the YAML the decision leaned towards.** The lean predated
the dependency list actually being short. Tickmark has three runtime
dependencies, each earning its place by doing something the standard library
cannot; ``pyyaml`` would be a fourth, bundled into every installer, purely to
read a config file that Python 3.11+ can already parse with ``tomllib`` from the
standard library. TOML is also the better fit for the content: the settings are
flat key-values, and its single-quoted literal strings take a regular expression
verbatim, with no escaping rules layered on top of the ones the regex already
has. YAML's indentation sensitivity is a worse trade for a file people will hand-
edit under time pressure. Recorded as an amendment in 04-tickmark.md §8.

**A bad config never costs someone their audit.** Every failure here is reported
and skipped rather than raised: an unknown key is ignored so a file written for
a later version still works on an older build, and a malformed rule is dropped
with a message naming it. Someone running this against a deadline should get
their findings, minus whatever they typo'd — not a stack trace.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

from tickmark.config.rules import DEFAULT_RULES, CustomRule, Rules

__all__ = ["CONFIG_NAME", "ConfigError", "find_config", "load_config"]

CONFIG_NAME = "tickmark.toml"

_VALID_SEVERITIES = frozenset({"high", "medium", "low", "info"})
_VALID_TARGETS = frozenset({"formula", "value"})


class ConfigError(Exception):
    """The config file exists but could not be read at all.

    Raised only for a file that cannot be parsed as TOML. Individual bad
    *settings* inside a readable file are skipped with a warning instead — the
    difference matters, because "your file is not TOML" is worth stopping for
    and "rule 3 has no pattern" is not.
    """


def find_config(start: Path) -> Path | None:
    """Look for ``tickmark.toml`` beside the audit target, then upwards.

    Walking up matters for the shape this is used in: a shared drive of
    workbooks in folders, audited as a batch, with one config at the top. Stops
    at the filesystem root.
    """
    directory = start if start.is_dir() else start.parent
    for candidate in [directory, *directory.parents]:
        path = candidate / CONFIG_NAME
        if path.is_file():
            return path
    return None


def _custom_rules(entries: Any, warn: list[str]) -> list[CustomRule]:
    if not isinstance(entries, list):
        warn.append("'rule' must be a list of [[rule]] tables; ignoring it")
        return []

    out: list[CustomRule] = []
    for index, entry in enumerate(entries, start=1):
        label = f"rule {index}"
        if not isinstance(entry, dict):
            warn.append(f"{label}: not a table; skipped")
            continue

        name = str(entry.get("name") or "").strip()
        pattern = entry.get("pattern")
        summary = str(entry.get("summary") or "").strip()
        if not name:
            warn.append(f"{label}: has no name; skipped")
            continue
        if not isinstance(pattern, str) or not pattern:
            warn.append(f"rule '{name}': has no pattern; skipped")
            continue
        if not summary:
            warn.append(f"rule '{name}': has no summary; skipped")
            continue

        severity = str(entry.get("severity", "medium")).lower()
        if severity not in _VALID_SEVERITIES:
            warn.append(f"rule '{name}': severity '{severity}' is not one of high/medium/low/info")
            continue

        target = str(entry.get("target", "formula")).lower()
        if target not in _VALID_TARGETS:
            warn.append(f"rule '{name}': target '{target}' is not 'formula' or 'value'")
            continue

        # Compiled here purely to reject it here. A pattern that does not compile
        # is dropped either way, but only at this point is there anywhere to say
        # so — the check runs per cell and cannot warn a thousand times, which
        # would otherwise leave a typo'd rule silently matching nothing at all.
        try:
            re.compile(pattern)
        except re.error as exc:
            warn.append(f"rule '{name}': pattern is not a valid regular expression ({exc})")
            continue

        out.append(
            CustomRule(
                name=name,
                pattern=pattern,
                summary=summary,
                severity=severity,
                explanation=str(entry.get("explanation") or ""),
                target=target,
            )
        )
    return out


def load_config(path: Path, base: Rules = DEFAULT_RULES) -> tuple[Rules, list[str]]:
    """Read a config file. Returns the rules and any warnings worth printing.

    Warnings are returned rather than printed so the CLI and the server can each
    present them their own way; neither should have to scrape stderr.
    """
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path.name} is not valid TOML: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"could not read {path}: {exc}") from exc

    warn: list[str] = []
    settings = data.get("rules")
    mapping: dict[str, Any] = {}
    if isinstance(settings, dict):
        mapping.update(settings)
    elif settings is not None:
        warn.append("'[rules]' must be a table; ignoring it")

    rules = _custom_rules(data.get("rule", []), warn) if "rule" in data else []
    if rules:
        mapping["custom_rules"] = rules

    try:
        built = Rules.from_mapping(mapping) if mapping else base
    except (TypeError, ValueError) as exc:
        # A setting of the wrong type — dominance = "high", say. One bad value
        # must not cost the whole file.
        warn.append(f"{path.name}: {exc}; using defaults")
        return base, warn

    return built, warn
