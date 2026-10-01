"""Regenerate ALL paper figures in one go.

Runs every figure script in-process, so everything lands in ONE
figures/{YYYYmmdd-HHMMSS}/ directory (the stamp is taken once per process;
set SPOT_FIG_ID to reuse an existing directory). The transfer-leg figure
is skipped with a notice unless a SPOT_XFER_LOG capture is available.

    python figscripts/fig_main.py [--run RUN] [--log XFER_LOG]
"""
import argparse

import fig_common as fc
import fig_load_balance
import fig_load_balance_all
import fig_mean_completion_vs_size
import fig_mean_completion_vs_tasks
import fig_parallelism
import fig_transfer_legs


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run", default=None,
                    help="results run id (default: newest timestamped run)")
    ap.add_argument("--log", default=None,
                    help="SPOT_XFER_LOG capture for the transfer-leg figure")
    args = ap.parse_args(argv)

    run_args = ["--run", args.run] if args.run else []
    steps = [
        ("mean completion vs number of tasks",
         lambda: fig_mean_completion_vs_tasks.main("mean", run_args)),
        ("total completion vs number of tasks",
         lambda: fig_mean_completion_vs_tasks.main("total", run_args)),
        ("mean completion vs task size",
         lambda: fig_mean_completion_vs_size.main("mean", run_args)),
        ("total completion vs task size",
         lambda: fig_mean_completion_vs_size.main("total", run_args)),
        ("load balance (POB vs POR)",
         lambda: fig_load_balance.main(run_args)),
        ("load balance (five strategies)",
         lambda: fig_load_balance_all.main(run_args)),
        ("degree of parallelism",
         lambda: fig_parallelism.main([])),
        ("transfer-leg decomposition",
         lambda: fig_transfer_legs.main(
             run_args + (["--log", args.log] if args.log else []))),
    ]

    failures = []
    for name, step in steps:
        print(f"\n=== {name} ===")
        try:
            step()
        except SystemExit as e:      # a script's own "can't proceed" exit
            print(f"skipped: {e}")
            failures.append(name)

    print(f"\nAll figures in {fc.FIG_DIR}")
    if failures:
        print("skipped: " + ", ".join(failures))


if __name__ == "__main__":
    main()
