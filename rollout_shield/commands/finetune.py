"""CLI subcommand tree: ``rollout-shield finetune ...``.

This module is imported by ``cli.py`` and dispatches to the right
function based on the subcommand. It mirrors the
``commands/{name}.py`` ``build_parser`` + ``cmd_X`` pattern from the
existing codebase.

Subcommands
-----------

``dataset add <path> --name N [--split 0.9] [--format FMT]``
    Register a JSONL dataset.
``dataset list [--json]``
    List registered datasets.
``dataset show <id> [--stats]``
    Show one dataset.
``dataset remove <id>``
    Delete a dataset and its samples.

``adapters list [--json]``
    List all adapter manifests.
``adapter show <id> [--json]``
    Show one adapter + the latest eval.
``adapter promote <id>``
    Register the adapter as a routable model.
``adapter unpromote <id>``
    Remove from registry.

``run --dataset D --base-model M [--recipe R] [--backend B] [...]``
    Start a training run synchronously.
``runs list [--status S]``
    List runs.
``runs show <id> [--events]``
    Show one run.
``runs abort <id>``
    Cooperative-abort a run.

``stats [--json]``
    Rolled counters.

``doctor``
    Self-check.

``sign-test --adapter A --prompt P``
    Print adapter fingerprint + one sample output.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..finetuning import (
    DatasetError,
    DoctorReport,
    TrainingError,
    abort_run,
    doctor,
    get_adapter,
    get_dataset,
    get_run,
    iter_split,
    list_adapters,
    list_datasets,
    list_promoted,
    list_runs,
    promote_adapter,
    register_dataset,
    remove_dataset,
    start_run,
    unpromote_adapter,
)
from ..finetuning.backends import backends as available_backends
from ..finetuning.backends import resolve as resolve_backend
from ..finetuning.recipes import (
    SUPPORTED_RECIPES,
    RecipeError,
    get_recipe,
)
from ..state import State


def _emit(obj, as_json: bool) -> None:
    if as_json:
        if hasattr(obj, "to_dict"):
            obj = obj.to_dict()
        print(json.dumps(obj, indent=2, sort_keys=True, default=str))
    else:
        if isinstance(obj, (list, tuple)):
            for row in obj:
                print(row)
        elif isinstance(obj, dict):
            for k, v in obj.items():
                print(f"{k}: {v}")
        else:
            print(obj)


def _print_datasets_table(state: State, datasets: list) -> None:
    if not datasets:
        print("(no datasets registered)")
        return
    print(f"{'DATASET_ID':<20}{'NAME':<24}{'FORMAT':<24}{'N':>6}{'TRAIN':>7}{'VAL':>7}")
    for d in datasets:
        print(f"{d.dataset_id:<20}{d.name[:22]:<24}{d.format[:22]:<24}"
              f"{d.n_samples:>6}{d.train_size:>7}{d.val_size:>7}")


def _print_adapters_table(state: State, adapters: list) -> None:
    if not adapters:
        print("(no adapters registered)")
        return
    print(f"{'ADAPTER_ID':<20}{'BASE':<24}{'RECIPE':<14}{'BACKEND':<10}{'STATUS':<18}")
    for a in adapters:
        print(f"{a.adapter_id:<20}{(a.base_model_id or '?')[:22]:<24}"
              f"{a.recipe_name[:12]:<14}{a.backend:<10}{a.status:<18}")


def _print_runs_table(state: State, runs: list) -> None:
    if not runs:
        print("(no runs)")
        return
    print(f"{'RUN_ID':<20}{'BASE':<24}{'RECIPE':<14}{'BACKEND':<10}{'STATUS':<18}{'ADAPTER':<20}")
    for r in runs:
        print(f"{r.run_id:<20}{(r.base_model_id or '?')[:22]:<24}"
              f"{r.recipe_name[:12]:<14}{r.backend:<10}{r.status:<18}"
              f"{(r.adapter_id or '-')[:18]:<20}")


def _print_doctor_report(r: DoctorReport) -> None:
    print(f"Python:               {r.python}")
    print(f"rollout_shield path:  {r.rollout_shield_path}")
    print(f"State root:           {r.state_root}  writable={r.state_writable}")
    print(f"Has [crypto]:         {r.has_crypto}")
    print(f"Has [finetune]:       {r.has_finetune}")
    print(f"stdlib backend:       {r.backend_stdlib}")
    print(f"peft backend:         {r.backend_peft}")
    print(f"AI registry size:     {r.ai_registry_size}")
    print(f"Datasets / Adapters / Promoted / Runs: {r.datasets} / {r.adapters} / "
          f"{r.promoted} / {r.runs}")
    print(f"Disk free:            {r.disk_free_bytes} bytes")
    for k, v in r.subdirs.items():
        print(f"  {k:<10}: {v}")
    if r.issues:
        print("Issues:")
        for issue in r.issues:
            print(f"  - {issue}")
    print(f"PASSED: {r.passed}")


# ---- top-level argparse ----------------------------------------------------

def build_parser(parent) -> None:
    """Add ``finetune ...`` subparsers to ``parent``."""
    from ..cli import _cmd_finetune  # local import to avoid circular import at module load
    p = parent.add_parser(
        "finetune",
        help="Adapter finetuning subsystem (datasets, runs, eval, promotion)",
        description=(
            "rollout-shield finetune: register a dataset, run a training "
            "step (stdlib or peft backend), score on a held-out split, "
            "and optionally promote the result as a routable model."
        ),
    )
    p.set_defaults(func=_cmd_finetune)
    sp = p.add_subparsers(dest="finetune_sub")

    # dataset
    ds = sp.add_parser("dataset", help="Manage datasets")
    ds_sp = ds.add_subparsers(dest="dataset_sub")
    add = ds_sp.add_parser("add", help="Register a JSONL dataset")
    add.add_argument("path", type=Path)
    add.add_argument("--name", required=True)
    add.add_argument("--split", type=float, default=0.9)
    add.add_argument("--format", choices=["prompt-target", "prompt-target-score", "raw"],
                     default="prompt-target")
    add.add_argument("--json", action="store_true")
    add.set_defaults(_cmd=cmd_dataset_add)
    ls = ds_sp.add_parser("list", help="List datasets")
    ls.add_argument("--json", action="store_true")
    ls.set_defaults(_cmd=cmd_dataset_list)
    sh = ds_sp.add_parser("show", help="Show one dataset")
    sh.add_argument("dataset_id")
    sh.add_argument("--stats", action="store_true")
    sh.add_argument("--json", action="store_true")
    sh.set_defaults(_cmd=cmd_dataset_show)
    rm = ds_sp.add_parser("remove", help="Remove a dataset")
    rm.add_argument("dataset_id")
    rm.set_defaults(_cmd=cmd_dataset_remove)

    # adapter
    ad = sp.add_parser("adapters", help="List adapter manifests")
    ad.add_argument("--json", action="store_true")
    ad.set_defaults(_cmd=cmd_adapters_list)
    ash = sp.add_parser("adapter", help="Inspect one adapter")
    ash_sp = ash.add_subparsers(dest="adapter_sub")
    ashow = ash_sp.add_parser("show", help="Show one adapter")
    ashow.add_argument("adapter_id")
    ashow.add_argument("--json", action="store_true")
    ashow.set_defaults(_cmd=cmd_adapter_show)
    aprom = ash_sp.add_parser("promote", help="Promote an adapter as a model")
    aprom.add_argument("adapter_id")
    aprom.set_defaults(_cmd=cmd_adapter_promote)
    aunprom = ash_sp.add_parser("unpromote", help="Remove from registry")
    aunprom.add_argument("adapter_id")
    aunprom.set_defaults(_cmd=cmd_adapter_unpromote)

    # runs
    run = sp.add_parser("run", help="Start a training run")
    run.add_argument("--dataset", required=True, dest="dataset_id")
    run.add_argument("--base-model", required=True, dest="base_model_id")
    run.add_argument("--recipe", default="sft-mini", dest="recipe_name")
    run.add_argument("--backend", choices=["auto", "stdlib"] + (
        ["peft"] if "peft" in available_backends() else []),
        default="stdlib")
    run.add_argument("--epochs", type=int, default=None)
    run.add_argument("--batch-size", type=int, default=None)
    run.add_argument("--lr", type=float, default=None)
    run.add_argument("--seed", type=int, default=None)
    run.add_argument("--max-steps", type=int, default=None)
    run.add_argument("--eval-threshold", type=float, default=None)
    run.add_argument("--register", dest="register", action="store_true",
                     default=False)
    run.add_argument("--no-register", dest="register", action="store_false")
    run.add_argument("--timeout-seconds", type=float, default=30.0)
    run.add_argument("--json", action="store_true")
    run.set_defaults(_cmd=cmd_run)
    runs = sp.add_parser("runs", help="Inspect runs")
    runs_sp = runs.add_subparsers(dest="runs_sub")
    rls = runs_sp.add_parser("list", help="List runs")
    rls.add_argument("--status", default=None)
    rls.add_argument("--json", action="store_true")
    rls.set_defaults(_cmd=cmd_runs_list)
    rsh = runs_sp.add_parser("show", help="Show one run")
    rsh.add_argument("run_id")
    rsh.add_argument("--events", action="store_true")
    rsh.add_argument("--json", action="store_true")
    rsh.set_defaults(_cmd=cmd_runs_show)
    rabort = runs_sp.add_parser("abort", help="Abort a run")
    rabort.add_argument("run_id")
    rabort.set_defaults(_cmd=cmd_runs_abort)

    # misc
    stats = sp.add_parser("stats", help="Rolled counters")
    stats.add_argument("--json", action="store_true")
    stats.set_defaults(_cmd=cmd_stats)
    sp.add_parser("doctor", help="Self-check").set_defaults(_cmd=cmd_doctor)
    st = sp.add_parser("sign-test", help="Sample one output from an adapter")
    st.add_argument("--adapter", required=True, dest="adapter_id")
    st.add_argument("--prompt", required=True)
    st.set_defaults(_cmd=cmd_sign_test)


# ---- command implementations ----------------------------------------------

def cmd_dataset_add(state: State, args: argparse.Namespace) -> int:
    try:
        rec = register_dataset(
            state, path=args.path, name=args.name,
            split=float(args.split), format_name=args.format,
        )
    except (DatasetError, FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(rec.to_dict(), indent=2, sort_keys=True, default=str))
    else:
        print(f"registered {rec.dataset_id}  n={rec.n_samples} "
              f"train={rec.train_size} val={rec.val_size} sha256={rec.content_sha256[:16]}...")
    return 0


def cmd_dataset_list(state: State, args: argparse.Namespace) -> int:
    items = list_datasets(state)
    if args.json:
        print(json.dumps([d.to_dict() for d in items], indent=2, sort_keys=True,
                         default=str))
    else:
        _print_datasets_table(state, items)
    return 0


def cmd_dataset_show(state: State, args: argparse.Namespace) -> int:
    rec = get_dataset(state, args.dataset_id)
    if rec is None:
        print(f"error: unknown dataset {args.dataset_id!r}", file=sys.stderr)
        return 1
    out = rec.to_dict()
    if args.stats:
        from collections import Counter
        c: Counter = Counter()
        tot = 0
        for row in iter_split(state, args.dataset_id, "train"):
            tot += len((row.get("target") or "").split())
            c["prompts"] += 1
        for _row in iter_split(state, args.dataset_id, "val"):
            c["prompts"] += 1
        out["stats"] = {"train_prompts": sum(1 for _ in iter_split(state, args.dataset_id, "train")),
                        "val_prompts": sum(1 for _ in iter_split(state, args.dataset_id, "val")),
                        "total_target_tokens_train": tot}
    if args.json:
        print(json.dumps(out, indent=2, sort_keys=True, default=str))
    else:
        for k, v in out.items():
            print(f"{k}: {v}")
    return 0


def cmd_dataset_remove(state: State, args: argparse.Namespace) -> int:
    remove_dataset(state, args.dataset_id)
    print(f"removed {args.dataset_id}")
    return 0


def cmd_adapters_list(state: State, args: argparse.Namespace) -> int:
    items = list_adapters(state)
    if args.json:
        print(json.dumps([a.to_dict() for a in items], indent=2, sort_keys=True,
                         default=str))
    else:
        _print_adapters_table(state, items)
    return 0


def cmd_adapter_show(state: State, args: argparse.Namespace) -> int:
    rec = get_adapter(state, args.adapter_id)
    if rec is None:
        print(f"error: unknown adapter {args.adapter_id!r}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(rec.to_dict(), indent=2, sort_keys=True, default=str))
    else:
        for k, v in rec.to_dict().items():
            print(f"{k}: {v}")
    return 0


def cmd_adapter_promote(state: State, args: argparse.Namespace) -> int:
    try:
        updated = promote_adapter(state, args.adapter_id)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"promoted {args.adapter_id} -> {updated.promoted_as_model_id}")
    return 0


def cmd_adapter_unpromote(state: State, args: argparse.Namespace) -> int:
    ok = unpromote_adapter(state, args.adapter_id)
    if not ok:
        print(f"error: {args.adapter_id!r} not in promoted list", file=sys.stderr)
        return 1
    print(f"unpromoted {args.adapter_id}")
    return 0


def cmd_run(state: State, args: argparse.Namespace) -> int:
    try:
        recipe = get_recipe(args.recipe_name)
    except RecipeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"starting run  recipe={recipe.name}  backend={args.backend}")
    try:
        rec = start_run(
            state,
            dataset_id=args.dataset_id,
            base_model_id=args.base_model_id,
            recipe_name=args.recipe_name,
            backend=args.backend,
            epochs=args.epochs,
            batch_size=args.batch_size,
            lr=args.lr,
            seed=args.seed,
            max_steps=args.max_steps,
            eval_threshold=args.eval_threshold,
            register=args.register,
            timeout_seconds=float(args.timeout_seconds),
        )
    except TrainingError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(rec.to_dict(), indent=2, sort_keys=True, default=str))
    else:
        print(f"run_id:    {rec.run_id}")
        print(f"status:    {rec.status}")
        print(f"adapter:   {rec.adapter_id}")
        if rec.last_error:
            print(f"error:     {rec.last_error}")
        if rec.meta.get("eval"):
            print("eval:")
            for k, v in rec.meta["eval"].items():
                print(f"  {k}: {v}")
    return 0


def cmd_runs_list(state: State, args: argparse.Namespace) -> int:
    items = list_runs(state, status=args.status)
    if args.json:
        print(json.dumps([r.to_dict() for r in items], indent=2, sort_keys=True,
                         default=str))
    else:
        _print_runs_table(state, items)
    return 0


def cmd_runs_show(state: State, args: argparse.Namespace) -> int:
    rec = get_run(state, args.run_id)
    if rec is None:
        print(f"error: unknown run {args.run_id!r}", file=sys.stderr)
        return 1
    out = rec.to_dict()
    if args.events:
        ev_path = state.finetuning_runs_dir / args.run_id / "events.jsonl"
        if ev_path.exists():
            out["events"] = [
                json.loads(line) for line in ev_path.read_text().splitlines()
                if line.strip()
            ]
    if args.json:
        print(json.dumps(out, indent=2, sort_keys=True, default=str))
    else:
        for k, v in out.items():
            if k == "events":
                print(f"{k}:")
                for ev in v:
                    print(f"  {ev}")
            else:
                print(f"{k}: {v}")
    return 0


def cmd_runs_abort(state: State, args: argparse.Namespace) -> int:
    try:
        updated = abort_run(state, args.run_id)
    except TrainingError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"aborted {args.run_id}: {updated.status}")
    return 0


def cmd_stats(state: State, args: argparse.Namespace) -> int:
    out = {
        "datasets": len(list_datasets(state)),
        "adapters": len(list_adapters(state)),
        "promoted": len(list_promoted(state)),
        "runs": len(list_runs(state)),
        "available_backends": available_backends(),
        "recipes": sorted(SUPPORTED_RECIPES),
    }
    if args.json:
        print(json.dumps(out, indent=2, sort_keys=True))
    else:
        for k, v in out.items():
            print(f"{k}: {v}")
    return 0


def cmd_doctor(state: State, args: argparse.Namespace) -> int:
    r = doctor(state)
    _print_doctor_report(r)
    return 0 if r.passed else 1


def cmd_sign_test(state: State, args: argparse.Namespace) -> int:
    rec = get_adapter(state, args.adapter_id)
    if rec is None:
        print(f"error: unknown adapter {args.adapter_id!r}", file=sys.stderr)
        return 1
    try:
        backend = resolve_backend(rec.backend)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    gen = backend.build_generator(state, args.adapter_id)
    text = gen(args.prompt)
    print(f"adapter_id:  {rec.adapter_id}")
    print(f"base:        {rec.base_model_id}")
    print(f"recipe:      {rec.recipe_name}")
    print(f"backend:     {rec.backend}")
    print(f"prompt:      {args.prompt!r}")
    print(f"output:      {text!r}")
    return 0


def dispatch(state: State, args: argparse.Namespace) -> int:
    """Top-level dispatcher used by ``cli.py``.

    Falls through to ``args._cmd(state, args)``. Returns 0 on success,
    non-zero on error.
    """
    cmd = getattr(args, "_cmd", None)
    if cmd is None:
        # no subcommand supplied
        print("error: 'finetune' requires a subcommand "
              "(dataset|adapters|adapter|runs|run|stats|doctor|sign-test)",
              file=sys.stderr)
        return 2
    return cmd(state, args)


__all__ = ["build_parser", "dispatch"]
