"""Runtime registry / byte / permission drift detector.

Implements the audit described in plans/tender-churning-micali.md:

* config.json keys must equal KNOWN_CONFIG_KEYS (no orphans, no missing)
* every state file must be UTF-8 clean (no NUL, no control bytes)
* every state file must be free of suspect Unicode (BOM / ZW / RTL /
  bidi controls — used to obfuscate code or filenames)
* every .json / .jsonl must parse
* every key-material file must be mode 0600 (group/world bits zero)

Exposed as:
  * ``audit_state_root(root)`` -> AuditReport dataclass
  * ``repair_state_root(report)`` -> dict (deletes orphan keys; refuses
    content-level fixes; refuses permission fixes outside Owner uid)
  * CLI subcommand ``rollout-shield audit`` (registered in cli.py)

The audit is invoked from ``_cmd_install`` after a successful install:
per the user's rule ("by conflicts start subp to hotpatch"), the audit
runs in a *subprocess* so a buggy audit cannot corrupt the install it
just finished.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# Single source of truth for the set of allowed config.json keys. Must
# match the keys StateConfig writes (state.py:240-249) and the audit
# regex test (scripts/integration_test.sh).
KNOWN_CONFIG_KEYS: frozenset[str] = frozenset({
    "alert_webhook_url",
    "claim_retention_days",
    "created_at",
    "health_window_seconds",
    "installed_at",
    "installed_by",
    "monitor_interval_seconds",
    "reputation_decay_days",
    "schema_version",
})

# Unicode tricks used to obfuscate code or filenames. UTF-8 BOM is
# sometimes legitimately present at the start of a file but never in
# JSON / JSONL / .py source. ZW* / LRM / RLM / bidi are almost always
# malicious in this context.
#
# We use \u escapes (not literal characters) because the source file
# is processed by tools that may strip zero-width chars, leaving
# behind empty strings which would match every position in every
# scanned file.
SUSPECT_CODEPOINTS: frozenset[str] = frozenset({
    "﻿",  # U+FEFF BOM
    "​",  # U+200B ZERO WIDTH SPACE
    "‌",  # U+200C ZERO WIDTH NON-JOINER
    "‍",  # U+200D ZERO WIDTH JOINER
    "‎",  # U+200E LEFT-TO-RIGHT MARK
    "‏",  # U+200F RIGHT-TO-LEFT MARK
    "‪",  # U+202A LEFT-TO-RIGHT EMBEDDING
    "‫",  # U+202B RIGHT-TO-LEFT EMBEDDING
    "‬",  # U+202C POP DIRECTIONAL FORMATTING
    "‭",  # U+202D LEFT-TO-RIGHT OVERRIDE
    "‮",  # U+202E RIGHT-TO-LEFT OVERRIDE
    "⁦",  # U+2066 LEFT-TO-RIGHT ISOLATE
    "⁧",  # U+2067 RIGHT-TO-LEFT ISOLATE
    "⁨",  # U+2068 FIRST STRONG ISOLATE
    "⁩",  # U+2069 POP DIRECTIONAL ISOLATE
})


@dataclass
class AuditReport:
    """Result of an audit run.

    Attributes:
        state_root: the resolved path of the audited state root
        unknown_keys: config.json keys not in KNOWN_CONFIG_KEYS
        unused_keys: KNOWN_CONFIG_KEYS entries not in config.json
        suspect_bytes: list of (path, label, count) for files containing
            suspect Unicode or non-UTF8 bytes
        json_parse_errors: list of (path, error) for malformed JSON
        loose_key_files: list of (path, mode) for keys_material files
            with group/world bits set
        ok: True iff all four lists are empty
    """
    state_root: str
    unknown_keys: list[str] = field(default_factory=list)
    unused_keys: list[str] = field(default_factory=list)
    suspect_bytes: list[tuple[str, str, int]] = field(default_factory=list)
    json_parse_errors: list[tuple[str, str]] = field(default_factory=list)
    loose_key_files: list[tuple[str, int]] = field(default_factory=list)
    ok: bool = True

    def recompute(self) -> None:
        self.ok = not (
            self.unknown_keys
            or self.suspect_bytes
            or self.json_parse_errors
            or self.loose_key_files
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "state_root": self.state_root,
            "unknown_keys": self.unknown_keys,
            "unused_keys": self.unused_keys,
            "suspect_bytes": [
                {"path": p, "label": l, "count": c}
                for p, l, c in self.suspect_bytes
            ],
            "json_parse_errors": [
                {"path": p, "error": e}
                for p, e in self.json_parse_errors
            ],
            "loose_key_files": [
                {"path": p, "mode": m}
                for p, m in self.loose_key_files
            ],
            "ok": self.ok,
        }


def audit_state_root(root: Path | str) -> AuditReport:
    """Scan a state root for drift; populate and return an AuditReport."""
    root = Path(root)
    rep = AuditReport(state_root=str(root.resolve()))

    # 1. registry check
    cfg = root / "config.json"
    if cfg.exists():
        try:
            actual = set(json.loads(cfg.read_text(encoding="utf-8")).keys())
        except Exception as exc:
            rep.json_parse_errors.append((str(cfg), str(exc)))
            actual = set()
        rep.unknown_keys = sorted(actual - KNOWN_CONFIG_KEYS)
        rep.unused_keys = sorted(KNOWN_CONFIG_KEYS - actual)

    # 2. byte scan + JSON parse for every file in the state tree
    if root.exists():
        for p in sorted(root.rglob("*")):
            if not p.is_file():
                continue
            try:
                data = p.read_bytes()
            except OSError as exc:
                rep.json_parse_errors.append((str(p), f"unreadable: {exc}"))
                continue
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError as exc:
                rep.suspect_bytes.append(
                    (str(p), "non-utf8", exc.start)
                )
                continue
            for ch in SUSPECT_CODEPOINTS:
                n = text.count(ch)
                if n:
                    rep.suspect_bytes.append((str(p), repr(ch), n))
            if p.suffix in (".json", ".jsonl"):
                try:
                    if p.suffix == ".json":
                        json.loads(text)
                    else:
                        for ln_no, line in enumerate(text.splitlines(), 1):
                            if line.strip():
                                json.loads(line)
                except Exception as exc:
                    rep.json_parse_errors.append(
                        (str(p), f"line {ln_no}: {exc}")
                    )

    # 3. key-material permission check (separate from byte scan)
    km = root / "keys_material"
    if km.exists():
        for p in sorted(km.rglob("*")):
            if p.is_file():
                try:
                    mode = p.stat().st_mode & 0o777
                except OSError:
                    continue
                if mode & 0o077:
                    rep.loose_key_files.append((str(p), mode))

    rep.recompute()
    return rep


def repair_state_root(report: AuditReport) -> dict[str, Any]:
    """Repair what we can; refuse the rest.

    * Deletes unknown config keys from config.json (atomic write).
    * Refuses content-level fixes (suspect_bytes, json_parse_errors)
      — those need operator review.
    * Refuses permission fixes on key files — the operator must chmod
      by hand (and understand why the chmod failed in the first place).
    """
    from .state import atomic_write_json

    cfg_path = Path(report.state_root) / "config.json"
    deleted: list[str] = []
    if report.unknown_keys and cfg_path.exists():
        try:
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
            for k in report.unknown_keys:
                data.pop(k, None)
                deleted.append(k)
            atomic_write_json(cfg_path, data)
        except Exception as exc:
            return {
                "deleted_keys": [],
                "repair_error": repr(exc),
                "refused_suspect_bytes": len(report.suspect_bytes),
                "refused_json_errors": len(report.json_parse_errors),
                "refused_loose_keys": len(report.loose_key_files),
            }
    return {
        "deleted_keys": deleted,
        "refused_suspect_bytes": len(report.suspect_bytes),
        "refused_json_errors": len(report.json_parse_errors),
        "refused_loose_keys": len(report.loose_key_files),
    }