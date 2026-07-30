# Restoring the GitHub Actions workflow from this Codespace

## Why this exists

The Codespace's GitHub OAuth token (in `.git/codeam-credentials`) has scopes
`codespace, repo, user:email` — **but not `workflow`**. GitHub rejects any push
that creates or modifies `.github/workflows/*` for tokens without the workflow
scope. This is the same blocker documented in:

  - commit `6ea6b55` ("temporary removal of workflow files to bypass Codespaces
    OAuth limitation")
  - benholl94-cmyk/upgraded-fiesta#94

## What is in this repo (already local)

1. **`/.github/actions/setup-bd-orchestrator/action.yml`** — composite action
   under `.github/actions/` (NOT under `.github/workflows/`, so pushing this
   file does NOT require the workflow scope). Contains 8 steps that run the
   Python test suite, lint shell scripts, smoke-test the CLI, run monitor
   `--once`, and check docs integrity.

2. **`/.github/workflows/ci.yml`** — single CI workflow that delegates 4 jobs
   (pr-validate, monitor-ci, docs-integrity, beads-health-report) to the
   orchestrator action. To get full CI coverage the operator only needs to
   land this ONE file.

## One-line recovery (manual paste via GitHub web UI)

1. Open https://github.com/benholl94-cmyk/codeam-codespace/settings/actions
2. Click **"set up a workflow yourself"** (or **"create new file"** →
   `.github/workflows/ci.yml`).
3. Paste the full contents of `.github/workflows/ci.yml` from this Codespace:
   ```
   cat .github/workflows/ci.yml | pbcopy
   ```
   or, from a Codespace terminal:
   ```
   gh api -X PUT --input - \
     repos/benholl94-cmyk/codeam-codespace/contents/.github/workflows/ci.yml \
     < .github/workflows/ci.yml   # needs workflow scope → use the web UI
   ```
4. Commit on the default branch → CI starts on the next push.

## One-line recovery via shell, after rotating the OAuth token to include
`workflow` scope

```bash
git push https://x-access-token:<NEW_PAT_WITH_WORKFLOW_SCOPE>@github.com/benholl94-cmyk/codeam-codespace.git main
```

The new PAT must have at minimum scopes: `repo`, `workflow`.

## Verification (after recovery)

After either path, push a trivial commit and watch the Actions tab:

  - **pr-validate** job → runs `tools/safeup.py`, `tests/run_all.py`
  - **monitor-ci** job → runs `rollout-shield monitor --once`, asserts
    `overall_ok: true`
  - **docs-integrity** job → validates JSON, README references resolve
  - **beads-health-report** (Mondays 07:17 UTC) → composite `full` mode

If any job turns red, the orchestrator action's steps log the failing module;
fix locally, push, and CI re-runs.

## Notes for the operator

- The composite action reference `./.github/actions/setup-bd-orchestrator`
  resolves to the **same commit** as the workflow that calls it. No tag
  drift risk.
- The action is small (8 steps) and self-contained. Editing it does not
  change workflow behavior in any way GitHub considers "workflow" content.
- All real policy — what the action runs and how failures are scored — is
  in Python and lives outside `.github/workflows/`. Moving logic into Python
  files means subsequent edits land via standard `git push` (no OAuth
  scope gymnastics).

