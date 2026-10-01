"""Figure: MEAN completion time vs number of tasks.

Writes TWO variants, because LE runs seconds while every offloading
strategy sits ~10x lower:

  *_all.png   all seven strategies on a broken y-axis: LE alone in a top
              band with break marks, so it visibly spans higher without
              flattening the others;
  *_noLE.png  LE dropped, axis scaled to the offloaders, LE's off-scale
              range stated in the footnote.

    python figscripts/fig_mean_completion_vs_tasks.py [--run RUN]
"""
import argparse

import fig_common as fc


def main(stat="mean", argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run", default=None,
                    help="results run id (default: newest timestamped run)")
    args = ap.parse_args(argv)
    run = args.run or fc.latest_run()

    fc.apply_style()
    for suffix, include_le in (("all", True), ("noLE", False)):
        fig = fc.completion_vs_tasks(run, stat=stat, include_le=include_le)
        fc.save(fig, f"{stat}_completion_vs_tasks_{run}_{suffix}",
                note=f"run={run} stat={stat} include_le={include_le}")


if __name__ == "__main__":
    main("mean")
