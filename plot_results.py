"""Regenerate the evaluation figures from a run's CSVs.

Usage:  python plot_results.py [RUN_ID] [--out DIR]

Reads results/{RUN_ID}/ (default: the sweep reported in the paper,
20260719-153332) and writes the evaluation figures fig5..fig9 as PDFs into
figures/paper/. Needs only matplotlib, no Docker.

Figures:
  fig5_histogram.pdf  mean task completion time vs. number of tasks
  fig6_histogram.pdf  disregarded requests vs. number of tasks
  fig7_histogram.pdf  task completion ratio vs. number of tasks
  fig8_load.pdf       share of assigned compute load per MEC
  fig9_cdf.pdf        CDF of per-task completion time at the heaviest load
"""

import argparse
import csv
import os
import statistics

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

DEFAULT_RUN = "20260719-153332"
DEFAULT_OUT = "figures/paper"
TASK_COUNTS = [20, 40, 60, 80, 100, 120]

# CSV prefix -> label used in the paper. POB is SPOT itself; POR is its
# dependency-blind random-placement ablation.
STRATEGIES = [
    ("POB", "SPOT"),
    ("EDITORS", "EDITORS"),
    ("JPOFH", "JPOFH"),
    ("POR", "SPOT-R"),
    ("BO", "BO"),
    ("CO", "CO"),
    ("LE", "LE"),
]
COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300",
          "#4a3aa7"]
HATCHES = [""] * 7


def completion_times(run_dir, strategy, n):
    """Per-task completion times (seconds) recorded for one strategy/load."""
    path = os.path.join(run_dir, f"{strategy}_{n}.csv")
    with open(path, newline="") as f:
        return [float(row["Time"]) for row in csv.DictReader(f)]


def discarded(run_dir, strategy, n):
    """Task ids this strategy discarded (deadline miss or infeasible)."""
    path = os.path.join(run_dir, f"discarded_task_{strategy}_{n}.csv")
    with open(path, newline="") as f:
        return [t for t in f.read().strip().split(",") if t]


def assigned_work(run_dir, kind, n):
    """[(server, Mcycles, rate)] routed by SPOT ('balanced') / SPOT-R ('random')."""
    path = os.path.join(run_dir, f"{kind}_{n}.csv")
    with open(path, newline="") as f:
        return [(r["Server"].replace("mn.", ""), float(r["AssignedMcycles"]),
                 float(r["Rate"])) for r in csv.DictReader(f)]


def _bars(ax, series):
    """One bar group per task count, one bar per strategy."""
    width = 0.8 / len(series)
    xs = range(len(TASK_COUNTS))
    for i, (label, values) in enumerate(series):
        offs = [x - 0.4 + width * (i + 0.5) for x in xs]
        ax.bar(offs, values, width, label=label, color=COLORS[i],
               hatch=HATCHES[i], edgecolor="black", linewidth=0.4)
    ax.set_xticks(list(xs))
    ax.set_xticklabels(TASK_COUNTS)
    ax.grid(axis="y", linestyle=":", linewidth=0.5, alpha=0.7)
    ax.set_axisbelow(True)


def _grouped_bars(ax, series, ylabel):
    _bars(ax, series)
    ax.set_xlabel("Number of tasks")
    ax.set_ylabel(ylabel)
    # legend above the axes: six of the seven series are zero-height in these
    # figures, so an in-axes legend lands on top of the one that is not
    ax.legend(ncol=4, fontsize=6.5, loc="lower center",
              bbox_to_anchor=(0.5, 1.005), frameon=False,
              handlelength=1.4, columnspacing=1.0, handletextpad=0.4)


def fig_completion_time(run_dir, out_dir):
    """Broken y-axis: LE sits ~11x above every offloading strategy, so a
    single linear axis would flatten the differences that matter."""
    series = [(label, [statistics.mean(completion_times(run_dir, s, n))
                       for n in TASK_COUNTS]) for s, label in STRATEGIES]
    fig, (hi, lo) = plt.subplots(2, 1, sharex=True, figsize=(7.0, 3.4),
                                 gridspec_kw={"height_ratios": [1, 3],
                                              "hspace": 0.08})
    _bars(hi, series)
    _bars(lo, series)
    hi.set_ylim(4.5, 5.3)
    lo.set_ylim(0, 1.15)
    hi.spines["bottom"].set_visible(False)
    lo.spines["top"].set_visible(False)
    hi.tick_params(bottom=False)
    # axis-break marks
    kw = dict(marker=[(-1, -0.6), (1, 0.6)], markersize=7, linestyle="none",
              color="k", mec="k", mew=1, clip_on=False)
    hi.plot([0, 1], [0, 0], transform=hi.transAxes, **kw)
    lo.plot([0, 1], [1, 1], transform=lo.transAxes, **kw)
    lo.set_xlabel("Number of tasks")
    lo.set_ylabel("Mean completion time (s)")
    lo.yaxis.set_label_coords(-0.075, 0.7)
    hi.legend(ncol=4, fontsize=7, framealpha=0.9, loc="upper left",
              bbox_to_anchor=(0.0, 1.6))
    fig.tight_layout()
    fig.savefig(os.path.join(out_dir, "fig5_histogram.pdf"))
    plt.close(fig)
    return series


def fig_disregarded(run_dir, out_dir):
    series = [(label, [len(discarded(run_dir, s, n)) for n in TASK_COUNTS])
              for s, label in STRATEGIES]
    fig, ax = plt.subplots(figsize=(3.4, 2.7))
    _grouped_bars(ax, series, "Disregarded requests")
    fig.tight_layout()
    fig.savefig(os.path.join(out_dir, "fig6_histogram.pdf"))
    plt.close(fig)
    return series


def fig_completion_ratio(run_dir, out_dir):
    series = [(label, [100.0 * (n - len(discarded(run_dir, s, n))) / n
                       for n in TASK_COUNTS]) for s, label in STRATEGIES]
    fig, ax = plt.subplots(figsize=(3.4, 2.7))
    _grouped_bars(ax, series, "Task completion ratio (%)")
    ax.set_ylim(0, 115)
    fig.tight_layout()
    fig.savefig(os.path.join(out_dir, "fig7_histogram.pdf"))
    plt.close(fig)
    return series


def fig_load(run_dir, out_dir, n=120):
    """Share of the run's total assigned compute per MEC: SPOT vs. SPOT-R.

    One panel at the heaviest load: the split is near-identical at every
    task count (the numbers for all counts are printed by main() and
    tabulated in the paper), so four panels would repeat one result.
    """
    shares = {}
    for count in TASK_COUNTS:
        for kind in ("balanced", "random"):
            rows = assigned_work(run_dir, kind, count)
            total = sum(w for _, w, _ in rows)
            shares[(kind, count)] = [100.0 * w / total for _, w, _ in rows]

    fig, ax = plt.subplots(figsize=(3.4, 2.5))
    width = 0.38
    for i, (kind, label) in enumerate([("balanced", "SPOT"),
                                       ("random", "SPOT-R")]):
        rows = assigned_work(run_dir, kind, n)
        offs = [x - width / 2 + width * i for x in range(len(rows))]
        ax.bar(offs, shares[(kind, n)], width, label=label,
               color=COLORS[i * 3], hatch=HATCHES[i], edgecolor="black",
               linewidth=0.4)
        ticks = [f"{name}\n{rate/1000:.1f} GHz" for name, _, rate in rows]
    ax.set_xticks(range(len(ticks)))
    ax.set_xticklabels(ticks, fontsize=8)
    ax.set_ylabel("Share of assigned load (%)")
    ax.set_ylim(0, 70)
    ax.grid(axis="y", linestyle=":", linewidth=0.5, alpha=0.7)
    ax.set_axisbelow(True)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(os.path.join(out_dir, "fig8_load.pdf"))
    plt.close(fig)
    return shares


def fig_cdf(run_dir, out_dir, n=120):
    fig, ax = plt.subplots(figsize=(3.4, 2.7))
    for i, (s, label) in enumerate(STRATEGIES):
        times = sorted(completion_times(run_dir, s, n))
        # normalised by the *offered* load, not by the completed subset, so a
        # strategy that discards tasks (LE) tops out below 100%
        ys = [100.0 * (k + 1) / n for k in range(len(times))]
        ax.step(times, ys, where="post", label=label, color=COLORS[i],
                linewidth=1.3)
    ax.set_xscale("log")
    ax.set_ylim(0, 102)
    ax.set_xlabel(f"Per-task completion time (s), {n} tasks")
    ax.set_ylabel("Tasks completed (%)")
    ax.grid(linestyle=":", linewidth=0.5, alpha=0.7)
    ax.set_axisbelow(True)
    ax.legend(fontsize=7, loc="lower right")
    fig.tight_layout()
    fig.savefig(os.path.join(out_dir, "fig9_cdf.pdf"))
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("run_id", nargs="?", default=DEFAULT_RUN)
    ap.add_argument("--out", default=DEFAULT_OUT)
    args = ap.parse_args()

    run_dir = os.path.join("results", args.run_id)
    os.makedirs(args.out, exist_ok=True)

    times = fig_completion_time(run_dir, args.out)
    disc = fig_disregarded(run_dir, args.out)
    ratio = fig_completion_ratio(run_dir, args.out)
    shares = fig_load(run_dir, args.out)
    fig_cdf(run_dir, args.out)

    # Print the numbers quoted in the paper alongside the figures
    print(f"run {args.run_id} -> {args.out}\n")
    print("mean completion time (s)")
    print("        " + "".join(f"{n:>9}" for n in TASK_COUNTS))
    for label, vals in times:
        print(f"{label:<8}" + "".join(f"{v:9.4f}" for v in vals))
    pob = dict(times)["SPOT"]
    print("\nSPOT reduction vs. baseline (%)")
    print("        " + "".join(f"{n:>9}" for n in TASK_COUNTS))
    for label, vals in times:
        if label == "SPOT":
            continue
        print(f"{label:<8}" + "".join(f"{100*(v-p)/v:9.1f}"
                                      for v, p in zip(vals, pob)))
    print("\ndisregarded requests")
    for label, vals in disc:
        print(f"{label:<8}" + "".join(f"{v:9d}" for v in vals))
    print("\ncompletion ratio (%)")
    for label, vals in ratio:
        print(f"{label:<8}" + "".join(f"{v:9.1f}" for v in vals))
    print("\nload share per MEC (%)")
    for (kind, n), pct in sorted(shares.items()):
        print(f"{kind:<9}{n:>4}  " + " ".join(f"{p:5.1f}" for p in pct))


if __name__ == "__main__":
    main()
