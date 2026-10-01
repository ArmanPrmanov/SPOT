"""Figure: MEAN completion time vs task size, one line per strategy.

JPOFH's "effect of amount of input data" plot (its Fig. 8), derived from
one existing run instead of a new sweep; also shows where offloading starts
paying off vs LE. Survivors only (the per-task
CSVs exclude deadline discards).

    python figscripts/fig_mean_completion_vs_size.py [--run RUN] [--n N]
                                          [--by cpu|memory] [--bins 6] [--logy]
"""
import argparse

import fig_common as fc


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run", default=None,
                    help="results run id (default: newest timestamped run)")
    ap.add_argument("--n", type=int, default=None,
                    help="task count to plot (default: largest in the run)")
    ap.add_argument("--by", choices=("cpu", "memory"), default="cpu",
                    help="task-size axis: total Mcycles (cpu) or input KB (memory)")
    ap.add_argument("--bins", type=int, default=6)
    ap.add_argument("--logy", action="store_true",
                    help="log y-axis (useful when LE dwarfs the offloaders)")
    return ap.parse_args(argv)


def main(stat="mean", argv=None):
    args = parse_args(argv)
    run = args.run or fc.latest_run()
    n = args.n or fc.available_ns(run)[-1]

    fc.apply_style()
    fig, bin_counts = fc.completion_vs_size(
        run, n, stat=stat, by=args.by, bins=args.bins, logy=args.logy)
    stem = (f"{stat}_completion_vs_size_{args.by}_{run}_n{n}"
            + ("_logy" if args.logy else ""))
    fc.save(fig, stem,
            note=f"run={run} n={n} by={args.by} bins={args.bins} "
                 f"bin_counts={list(bin_counts)}")


if __name__ == "__main__":
    main("mean")
