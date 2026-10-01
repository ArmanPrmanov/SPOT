"""Figure: transfer-leg decomposition.

Splits each strategy's mean completion time into
  compute (residual) / upload (host->MEC or host->cloud) /
  horizontal (MEC->MEC, between dependent subtasks) / return (->host)
from a SPOT_XFER_LOG capture. Directly evidences the "vertical AND
horizontal offloading" claim: POB is the only strategy with substantial
horizontal traffic; BO/CO/JPOFH have none between subtasks.

The log has no strategy column. Strategies run sequentially and each
serves every task in one contiguous block, so segments are recovered by
splitting wherever an already-seen task id starts a new block, then labeled
in execution order (LE never transfers, hence its absence from --labels).
A sweep log holds one group of segments per task count; the group is picked
by --n (default: the last group).

Capture a log on the Linux box with
    SPOT_XFER_LOG=results/$RUN/xfer.log python network_graph.py
then:
    python figscripts/fig_transfer_legs.py --log results/{RUN}/xfer.log [--run RUN] [--n N]
"""
import argparse
import os
import re

import numpy as np
import matplotlib.pyplot as plt

import fig_common as fc

DEFAULT_LABELS = ["POB", "POR", "BO", "CO", "JPOFH", "EDITORS"]

# leg identity by node class of src -> dst (names are convention-driven:
# mn.host* / mn.mec* / mn.cloud*)
LEGS = ["Compute", "Upload", "Horizontal", "Return"]
LEG_COLOR = {"Compute": "#2a78d6", "Upload": "#eb6834",
             "Horizontal": "#1baf7a", "Return": "#eda100"}

_TASK_RE = re.compile(r"(task_\d+)")


def node_class(name):
    for cls in ("host", "mec", "cloud"):
        if name.startswith(f"mn.{cls}"):
            return cls
    return "?"


def leg_of(src, dst):
    a, b = node_class(src), node_class(dst)
    if a == "host":
        return "Upload"
    if b == "host":
        return "Return"
    return "Horizontal"          # mec->mec (cloud never talks to a mec)


def parse_log(path):
    rows = []
    with open(path) as f:
        for lineno, line in enumerate(f, 1):
            parts = line.strip().split(",")
            if len(parts) < 6 or not parts[0]:
                continue
            m = _TASK_RE.match(parts[4])
            if not m:
                print(f"  (line {lineno}: unparseable id {parts[4]!r}, skipped)")
                continue
            rows.append({"src": parts[1], "dst": parts[2], "task": m.group(1),
                         "sec": float(parts[5]) if parts[5] else None})
    return rows


def split_segments(rows):
    """One segment per strategy pass: within a pass every task is served in
    one contiguous block, so a block whose task id was already seen in the
    current segment starts the next strategy."""
    segments, seg, seen, cur = [], [], set(), None
    for r in rows:
        if r["task"] != cur:
            if r["task"] in seen:
                segments.append(seg)
                seg, seen = [], set()
            seen.add(r["task"])
            cur = r["task"]
        seg.append(r)
    if seg:
        segments.append(seg)
    return segments


def pick_group(segments, labels, want_n):
    """Group consecutive segments len(labels) at a time (one group per task
    count in a sweep log) and pick the group for want_n (else the last)."""
    if len(segments) % len(labels) != 0:
        for i, s in enumerate(segments):
            tasks = {r["task"] for r in s}
            print(f"  segment {i}: {len(s)} transfers, {len(tasks)} tasks, "
                  f"max index {max(int(t.split('_')[1]) for t in tasks)}")
        raise SystemExit(
            f"{len(segments)} segments is not a multiple of {len(labels)} "
            f"labels ({','.join(labels)}): pass --labels matching what the "
            "logged invocation actually ran (see the segment summary above)")
    groups = [segments[i:i + len(labels)]
              for i in range(0, len(segments), len(labels))]
    picked = groups[-1]
    if want_n is not None:
        by_n = {max(int(t.split("_")[1]) for s in g for r in s
                    for t in [r["task"]]): g for g in groups}
        if want_n not in by_n:
            raise SystemExit(f"no segment group with max task index {want_n}; "
                             f"log holds {sorted(by_n)}")
        picked = by_n[want_n]
    n = max(int(r["task"].split("_")[1]) for s in picked for r in s)
    return picked, n


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--log", default=os.environ.get("SPOT_XFER_LOG"),
                    help="SPOT_XFER_LOG capture (ts,src,dst,size,subtask,reported)")
    ap.add_argument("--run", default=None,
                    help="results run the log belongs to (for the completion "
                         "means; default: newest timestamped run)")
    ap.add_argument("--n", type=int, default=None,
                    help="task count / segment group to use (default: last group)")
    ap.add_argument("--labels", default=",".join(DEFAULT_LABELS),
                    help="strategy order of the logged invocation "
                         f"(default {','.join(DEFAULT_LABELS)}; LE logs nothing)")
    args = ap.parse_args(argv)

    if not args.log or not os.path.exists(args.log):
        raise SystemExit(
            "no transfer log. Capture one on the Linux box with\n"
            "    SPOT_XFER_LOG=results/$RUN/xfer.log python network_graph.py\n"
            "then rerun with --log results/{RUN}/xfer.log")

    run = args.run or fc.latest_run()
    labels = args.labels.split(",")
    segments = split_segments(parse_log(args.log))
    group, n = pick_group(segments, labels, args.n)

    # mean seconds per surviving task, per leg; transfers of tasks that were
    # later deadline-discarded are excluded so the split matches the CSV mean
    strategies = [s for s in fc.STRATEGIES if s in labels or s == "LE"]
    decomp, notes = {}, []
    for strat in strategies:
        times = fc.load_times(run, strat, n)
        if times is None:
            print(f"  (skipping {strat}: no {strat}_{n}.csv in results/{run})")
            continue
        mean_completion = np.mean(list(times.values()))
        legs = dict.fromkeys(LEGS, 0.0)
        if strat != "LE":
            seg = group[labels.index(strat)]
            for r in seg:
                if r["sec"] is not None and r["task"] in times:
                    legs[leg_of(r["src"], r["dst"])] += r["sec"] / len(times)
        transfer_sum = sum(legs.values())
        legs["Compute"] = mean_completion - transfer_sum
        if legs["Compute"] < 0:
            notes.append(f"{strat}: summed transfer legs exceed the mean "
                         f"completion by {-legs['Compute']:.3f}s (parallel "
                         "branches overlap transfers); compute floored at 0")
            legs["Compute"] = 0.0
        decomp[strat] = legs

    for note in notes:
        print("  note:", note)

    fc.apply_style()
    fig, (ax1, ax2) = plt.subplots(
        1, 2, figsize=(11.0, 4.6), width_ratios=[1.35, 1], sharey=True)
    order = [s for s in fc.STRATEGIES if s in decomp][::-1]  # POB on top
    ys = np.arange(len(order))

    def stacked(ax, legs, scale=1.0):
        left = np.zeros(len(order))
        for leg in legs:
            vals = np.array([decomp[s][leg] * scale for s in order])
            ax.barh(ys, vals, 0.62, left=left, color=LEG_COLOR[leg],
                    label=leg, edgecolor="white", linewidth=1.5)
            left += vals
        span = left.max()
        run = np.zeros(len(order))          # label segments wide enough
        for leg in legs:
            vals = np.array([decomp[s][leg] * scale for s in order])
            for y, v, x0 in zip(ys, vals, run):
                if v > 0.05 * span:
                    ax.text(x0 + v / 2, y, f"{v:.2f}" if scale == 1 else f"{v:.1f}",
                            ha="center", va="center", fontsize=8.5,
                            color="white", fontweight="bold")
            run += vals
        ax.grid(axis="x")
        ax.grid(axis="y", visible=False)

    # (a) the full split; compute dominates, which is itself the finding
    stacked(ax1, LEGS)
    ax1.set_yticks(ys, order)
    ax1.set_xlabel("Mean completion time per task (s)")
    ax1.set_title("(a) Full decomposition")
    ax1.legend(ncols=2, loc="lower right")

    # (b) transfers only, in ms, where the vertical/horizontal contrast shows
    stacked(ax2, LEGS[1:], scale=1000.0)
    ax2.set_xlabel("Mean transfer time per task (ms)")
    ax2.set_title("(b) Transfer legs only")

    fig.suptitle(f"Transfer-leg decomposition of mean completion  (n = {n})",
                 y=1.02)
    fig.text(0.01, -0.02,
             "compute = residual (mean completion − summed transfer legs); "
             "survivors only; LE has no transfers by construction",
             fontsize=8.5, color=fc.MUTED)
    fig.tight_layout()
    fc.save(fig, f"transfer_legs_{run}_n{n}",
            note=f"run={run} n={n} log={args.log} labels={','.join(labels)}")


if __name__ == "__main__":
    main()
