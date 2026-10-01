"""Figure: load balance, POB vs POR.

Two panels from the balanced_/random_{n}.csv files (cumulative assigned
Mcycles per MEC over the run). Each node serves its assigned subtasks one
at a time and capacity is restored after every task, so a peak-reservation
snapshot shows no load; cumulative assigned work does:

  (a) assigned Mcycles per MEC at one task count, POB vs POR. POB
      concentrates work on the fastest MECs (its placement ignores load
      from other tasks), POR spreads it uniformly;
  (b) Jain's fairness index of that distribution vs n, one line each.

    python figscripts/fig_load_balance.py [--run RUN] [--n N]
"""
import argparse

import numpy as np
import matplotlib.pyplot as plt

import fig_common as fc

KIND = {"POB": "balanced", "POR": "random"}


def jain(values):
    x = np.asarray(values, dtype=float)
    return float(x.sum() ** 2 / (len(x) * (x ** 2).sum()))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run", default=None,
                    help="results run id (default: newest timestamped run)")
    ap.add_argument("--n", type=int, default=None,
                    help="task count for the per-MEC panel (default: largest)")
    args = ap.parse_args(argv)

    run = args.run or fc.latest_run()
    ns = fc.available_ns(run, prefix="balanced")
    n = args.n or ns[-1]

    fc.apply_style()
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10.5, 4.2))

    # --- (a) assigned work per MEC at n -------------------------------------
    per_mec = {s: fc.load_assigned(run, KIND[s], n) for s in KIND}
    for s, data in per_mec.items():
        if data is None:
            raise SystemExit(f"missing {KIND[s]}_{n}.csv in results/{run}")
    servers = sorted(per_mec["POB"])
    xs = np.arange(len(servers))
    width = 0.38
    for i, s in enumerate(KIND):
        vals = [per_mec[s][srv][0] for srv in servers]
        bars = ax1.bar(xs + (i - 0.5) * width, vals, width * 0.94,
                       color=fc.COLOR[s], label=s,
                       edgecolor="white", linewidth=1.5)
        fc.bar_value_labels(ax1, bars, fmt="{:,.0f}")
    # tick label carries each node's rate, which explains POB's concentration
    ax1.set_xticks(xs, [f"{srv.replace('mn.', '')}\n{per_mec['POB'][srv][1]:.0f} MHz"
                        for srv in servers])
    ax1.set_ylabel("Assigned work (Mcycles)")
    ax1.set_title(f"(a) Work routed per MEC  (n = {n})")
    ax1.legend()

    # --- (b) Jain's fairness index vs n -------------------------------------
    for s in KIND:
        ys, xs_n = [], []
        for m in ns:
            data = fc.load_assigned(run, KIND[s], m)
            if data:
                xs_n.append(m)
                ys.append(jain([v[0] for v in data.values()]))
        ax2.plot(xs_n, ys, color=fc.COLOR[s], marker=fc.MARKER[s],
                 markersize=7, markeredgecolor="white", markeredgewidth=1.2,
                 linewidth=2, label=s)
    ax2.axhline(1.0, color=fc.BASELINE, linewidth=1, linestyle=(0, (4, 3)))
    ax2.text(ns[0], 1.0, "even split", ha="left", va="bottom",
             fontsize=8.5, color=fc.MUTED)
    ax2.set_ylim(0, 1.08)
    ax2.set_xticks(ns)
    ax2.set_xlabel("Number of tasks")
    ax2.set_ylabel("Jain's fairness index")
    ax2.set_title("(b) Fairness of the distribution vs n")
    ax2.legend(loc="lower right")

    fig.suptitle("Load balance: cumulative assigned Mcycles per MEC",
                 y=1.02)
    fig.tight_layout()
    fc.save(fig, f"load_balance_ssp_{run}_n{n}",
            note=f"run={run} n={n} ns={ns} (balanced_/random_*.csv)")


if __name__ == "__main__":
    main()
