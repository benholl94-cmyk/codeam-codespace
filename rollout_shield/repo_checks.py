"""Repo-level health checks for rollout-shield.

These checks observe the repo workspace itself — the source tree, the
documentation, the workflow files, and the install prefix — not just the
runtime state. They are part of the **selfhealthed, healed, self_management_tools**
property of the repo:

- A check failure here means something is wrong with the repo as a
  management tool, not just with a running claim.
- The daemon includes these checks in its cycle alongside state + host
  checks, so the operator gets a single unified health view.
- Every check is independent, read-only, and fast (<1s).

The checks are designed to be **repairable** by ``commands/self_heal.py``:
each failing check has a paired repair routine.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Any

from .health_checks import HealthResult
from .state import State

PREFIX_DEFAULT = Path.home() / "usr"


# ---------- helpers ----------


def _discover_repo_root() -> Path | None:
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        if (parent / "rollout_shield" / "__init__.py").exists() \
                and (parent / "scripts" / "install.sh").exists():
            return parent
    return None


def _run(cmd: list[str], cwd: Path | None = None,
         timeout: float = 5.0) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True,
                              text=True, timeout=timeout, check=False)
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        return 1, "", repr(exc)


# ---------- checks ----------


def check_repo_clean(state: State) -> HealthResult:
    """Working tree is clean (no uncommitted changes)."""
    start = time.perf_counter()
    repo_root = _discover_repo_root()
    if not repo_root:
        return HealthResult(
            name="repo_clean", ok=True,
            message="(no repo source discovered; skipped)",
            duration_ms=(time.perf_counter() - start) * 1000,
        )
    rc, out, _err = _run(["git", "status", "--porcelain"], cwd=repo_root)
    if rc != 0:
        return HealthResult(
            name="repo_clean", ok=True,
            message=f"(git status unavailable: {out or _err}; informational only)",
            duration_ms=(time.perf_counter() - start) * 1000,
        )
    dirty = [ln for ln in out.splitlines() if ln.strip()]
    ok = not dirty
    return HealthResult(
        name="repo_clean",
        ok=ok,
        message="working tree clean" if ok else f"{len(dirty)} uncommitted entries",
        details={"dirty_count": len(dirty), "dirty_files": dirty[:10],
                 "repo_root": str(repo_root)},
        duration_ms=(time.perf_counter() - start) * 1000,
    )


def check_docs_integrity(state: State) -> HealthResult:
    """CLAUDE.md carries the BEGIN/END BEADS INTEGRATION markers."""
    start = time.perf_counter()
    repo_root = _discover_repo_root()
    if not repo_root:
        return HealthResult(
            name="docs_integrity", ok=True,
            message="(no repo source discovered; skipped)",
            duration_ms=(time.perf_counter() - start) * 1000,
        )
    claude_md = repo_root / "CLAUDE.md"
    if not claude_md.exists():
        return HealthResult(
            name="docs_integrity", ok=False,
            message=f"CLAUDE.md missing at {claude_md}",
            duration_ms=(time.perf_counter() - start) * 1000,
        )
    try:
        text = claude_md.read_text(encoding="utf-8")
    except OSError as exc:
        return HealthResult(
            name="docs_integrity", ok=False,
            message=f"CLAUDE.md unreadable: {exc}",
            duration_ms=(time.perf_counter() - start) * 1000,
        )
    begin = "<!-- BEGIN BEADS INTEGRATION" in text
    end = "<!-- END BEADS INTEGRATION -->" in text
    ok = begin and end
    return HealthResult(
        name="docs_integrity",
        ok=ok,
        message=("CLAUDE.md Beads markers intact" if ok
                 else f"markers missing (begin={begin}, end={end})"),
        details={"begin_present": begin, "end_present": end,
                 "path": str(claude_md)},
        duration_ms=(time.perf_counter() - start) * 1000,
    )


def check_workflow_files_present(state: State) -> HealthResult:
    """Expected files under .github/ exist (if the repo uses GitHub Actions)."""
    start = time.perf_counter()
    repo_root = _discover_repo_root()
    if not repo_root:
        return HealthResult(
            name="workflow_files_present", ok=True,
            message="(no repo source discovered; skipped)",
            duration_ms=(time.perf_counter() - start) * 1000,
        )
    expected_workflows = [
        "docs-integrity.yml",
        "issue-triage.yml",
        "pr-validate.yml",
        "beads-health-report.yml",
    ]
    wf_dir = repo_root / ".github" / "workflows"
    if not wf_dir.is_dir():
        return HealthResult(
            name="workflow_files_present", ok=True,
            message="(.github/workflows/ missing; no GH Actions required — informational)",
            duration_ms=(time.perf_counter() - start) * 1000,
        )
    found = sorted(p.name for p in wf_dir.glob("*.yml"))
    missing = [w for w in expected_workflows if w not in found]
    ok = not missing
    return HealthResult(
        name="workflow_files_present",
        ok=ok,
        message=(f"all {len(expected_workflows)} expected workflows present"
                 if ok else f"missing workflows: {missing}"),
        details={"found": found, "expected": expected_workflows, "missing": missing},
        duration_ms=(time.perf_counter() - start) * 1000,
    )


def check_bin_executable(state: State) -> HealthResult:
    """The bin/rollout-shield CLI script is executable."""
    start = time.perf_counter()
    repo_root = _discover_repo_root()
    candidates: list[Path] = []
    if repo_root:
        candidates.append(repo_root / "bin" / "rollout-shield")
    candidates.append(PREFIX_DEFAULT / "bin" / "rollout-shield")
    candidates.append(Path.home() / ".local" / "bin" / "rollout-shield")

    found: list[dict] = []
    for c in candidates:
        if c.exists():
            found.append({"path": str(c), "executable": os.access(c, os.X_OK)})
    ok = any(p["executable"] for p in found)
    if not found:
        return HealthResult(
            name="bin_executable", ok=False,
            message="rollout-shield CLI not found in any known location",
            details={"candidates": [str(c) for c in candidates]},
            duration_ms=(time.perf_counter() - start) * 1000,
        )
    if not ok:
        return HealthResult(
            name="bin_executable", ok=False,
            message=f"rollout-shield CLI found but not executable: {[p['path'] for p in found]}",
            details={"found": found},
            duration_ms=(time.perf_counter() - start) * 1000,
        )
    return HealthResult(
        name="bin_executable", ok=True,
        message=f"rollout-shield CLI executable at {[p['path'] for p in found]}",
        details={"found": found},
        duration_ms=(time.perf_counter() - start) * 1000,
    )


def check_prefix_installed(state: State, prefix: Path = PREFIX_DEFAULT) -> HealthResult:
    """The user-local install prefix at ~/usr/ is intact (hard build target)."""
    start = time.perf_counter()
    expected = [
        prefix / "bin" / "rollout-shield",
        prefix / "bin" / "rollout-shield-monitor",
        prefix / "lib" / "python" / "rollout_shield" / "__init__.py",
        prefix / "share" / "rollout-shield" / "interface" / "index.html",
        prefix / "etc" / "rollout-shield" / "config.example.json",
    ]
    missing = [str(p) for p in expected if not p.exists()]
    ok = not missing
    return HealthResult(
        name="prefix_installed",
        ok=ok,
        message=(f"hard build intact at {prefix}"
                 if ok else f"install prefix incomplete: {missing}"),
        details={"prefix": str(prefix), "missing": missing},
        duration_ms=(time.perf_counter() - start) * 1000,
    )


def check_beads_db_present(state: State) -> HealthResult:
    """The .beads/ Dolt DB exists (the issue tracker is reachable)."""
    start = time.perf_counter()
    repo_root = _discover_repo_root()
    if not repo_root:
        return HealthResult(
            name="beads_db_present", ok=True,
            message="(no repo source discovered; skipped)",
            duration_ms=(time.perf_counter() - start) * 1000,
        )
    beads_dir = repo_root / ".beads"
    if not beads_dir.is_dir():
        return HealthResult(
            name="beads_db_present", ok=False,
            message=f".beads/ directory missing at {beads_dir}",
            duration_ms=(time.perf_counter() - start) * 1000,
        )
    # the local Dolt DB lives at .dolt/ (gitignored)
    dolt_dir = beads_dir / ".dolt"
    has_dolt = dolt_dir.is_dir()
    # the export file should also exist
    export = beads_dir / "issues.jsonl"
    has_export = export.exists()
    ok = has_dolt or has_export
    return HealthResult(
        name="beads_db_present",
        ok=ok,
        message=("beads tracker reachable" if ok
                 else "beads tracker not initialized (no .dolt/ + no export)"),
        details={"dolt_dir": str(dolt_dir), "has_dolt": has_dolt,
                 "has_export": has_export},
        duration_ms=(time.perf_counter() - start) * 1000,
    )


def check_scripts_executable(state: State) -> HealthResult:
    """scripts/*.sh are executable (so install.sh / uninstall.sh / verify-install.sh work)."""
    start = time.perf_counter()
    repo_root = _discover_repo_root()
    if not repo_root:
        return HealthResult(
            name="scripts_executable", ok=True,
            message="(no repo source discovered; skipped)",
            duration_ms=(time.perf_counter() - start) * 1000,
        )
    scripts_dir = repo_root / "scripts"
    if not scripts_dir.is_dir():
        return HealthResult(
            name="scripts_executable", ok=False,
            message=f"scripts/ directory missing at {scripts_dir}",
            duration_ms=(time.perf_counter() - start) * 1000,
        )
    non_exec: list[str] = []
    for sh in sorted(scripts_dir.glob("*.sh")):
        if not os.access(sh, os.X_OK):
            non_exec.append(sh.name)
    ok = not non_exec
    return HealthResult(
        name="scripts_executable",
        ok=ok,
        message=("all scripts/*.sh executable" if ok
                 else f"non-executable scripts: {non_exec}"),
        details={"non_executable": non_exec,
                 "scripts_dir": str(scripts_dir)},
        duration_ms=(time.perf_counter() - start) * 1000,
    )


# ---------- registry ----------


DEFAULT_REPO_CHECKS: list[Any] = [
    check_repo_clean,
    check_docs_integrity,
    check_workflow_files_present,
    check_bin_executable,
    check_prefix_installed,
    check_beads_db_present,
    check_scripts_executable,
]


def run_repo_checks(state: State,
                    disabled: list[str] | None = None,
                    prefix: Path = PREFIX_DEFAULT) -> list[dict]:
    """Run every registered repo-level check and return results as dicts."""
    disabled = set(disabled or [])
    out: list[dict] = []
    for check in DEFAULT_REPO_CHECKS:
        name = check.__name__.replace("check_", "")
        if name in disabled:
            continue
        try:
            # prefix checks need a prefix arg; inject it via inspection
            import inspect
            sig = inspect.signature(check)
            params = sig.parameters
            if "prefix" in params:
                result = check(state, prefix=prefix)
            else:
                result = check(state)
            out.append(result.to_dict())
        except Exception as exc:  # noqa: BLE001 — health checks must never raise
            out.append({
                "name": name,
                "ok": False,
                "message": f"check raised: {exc}",
                "duration_ms": 0.0,
                "details": {"exception": repr(exc)},
            })
    return out
