"""Figure: load balance across FIVE strategies.

Companion to fig_load_balance.py (POB vs POR only): the same two panels,
(a) assigned Mcycles per MEC at one task count and (b) Jain's fairness
index vs n, with BO, JPOFH and EDITORS added. POB/POR come from the run's measured
balanced_/random_{n}.csv; BO/JPOFH/EDITORS are replayed offline with the
production decision code (fig_replay.py), which reproduces the routed work
exactly because those decisions consume no measurements. CO and LE route
no work to any MEC (all-cloud / all-host) and are omitted.

    python figscripts/fig_load_balance_all.py [--run RUN] [--n N]
"""
import argparse

import numpy as np
import matplotlib.pyplot as plt

import fig_common as fc
import fig_replay as fr
import network_graph as ng
from fig_load_balance import jain

STRATS = ["POB", "POR", "BO", "JPOFH", "EDITORS"]
CSV_KIND = {"POB": "balanced", "POR": "random"}


def assigned_work(strat, run, n, tasks):
    """{mec: Mcycles} routed by `strat` over the first n tasks: measured
    CSV for POB/POR, offline replay for the deterministic baselines. Counts
    every placed task (deadline discards happen later), matching
    save_assigned_work's accounting."""
    if strat in CSV_KIND:
        data = fc.load_assigned(run, CSV_KIND[strat], n)
        return None if data is None else {m: v[0] for m, v in data.items()}
    work = dict.fromkeys(fr.MECS, 0.0)
    if strat == "BO":
        for task, node in fr.replay_bo(tasks[:n]):
            if node in work:
                work[node] += sum(st.comp for st in task.subtasks)
    elif strat == "JPOFH":
        for task, mec, x in fr.replay_jpofh(tasks[:n])[0]:
            if mec is not None:
                work[mec] += x * sum(st.comp for st in task.subtasks)
    elif strat == "EDITORS":
        for task, placement in fr.replay_editors(tasks[:n])[0]:
            for st in task.subtasks:
                if placement[st.subtask_id] in work:
                    work[placement[st.subtask_id]] += st.comp
    return work


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
    tasks = fr.read_tasks(max(ns + [n]))

    per_strat = {s: assigned_work(s, run, n, tasks) for s in STRATS}
    for s, work in per_strat.items():
        if work is None:
            raise SystemExit(f"missing {CSV_KIND[s]}_{n}.csv in results/{run}")

    fc.apply_style()
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.5, 4.4),
                                   width_ratios=[1.25, 1])

    # --- (a) assigned work per MEC at n -------------------------------------
    servers = sorted(fr.MECS)
    xs = np.arange(len(servers))
    width = 0.8 / len(STRATS)
    rates = {m: ng.node_rate(m, ng.MEC_CAPACITY[1]) for m in servers}
    for i, s in enumerate(STRATS):
        vals = [per_strat[s][m] for m in servers]
        bars = ax1.bar(xs + (i - (len(STRATS) - 1) / 2) * width, vals,
                       width * 0.9, color=fc.COLOR[s], label=s,
                       edgecolor="white", linewidth=1.2)
        for b in bars:   # rotated so five labels per group don't collide
            ax1.annotate(f"{b.get_height() / 1000:.0f}k",
                         (b.get_x() + b.get_width() / 2, b.get_height()),
                         xytext=(0, 3), textcoords="offset points",
                         ha="center", va="bottom", rotation=90,
                         fontsize=7.5, color=fc.INK)
    ax1.set_xticks(xs, [f"{m.replace('mn.', '')}\n{rates[m]:.0f} MHz"
                        for m in servers])
    ax1.set_ylim(0, max(v for w in per_strat.values() for v in w.values()) * 1.22)
    ax1.set_ylabel("Assigned work (Mcycles)")
    ax1.set_title(f"(a) Work routed per MEC  (n = {n})")
    ax1.legend(ncols=len(STRATS), loc="upper right", columnspacing=1.0,
               handlelength=1.2)

    # --- (b) Jain's fairness index vs n -------------------------------------
    # the series pair off (BO~POR near 1.0, JPOFH~EDITORS ~0.64): draw one of
    # each pair first with a bigger marker/thicker line so both stay visible
    # where they coincide
    size = {"POB": 7, "POR": 7, "BO": 10.5, "JPOFH": 10.5, "EDITORS": 7}
    handles = {}
    for s in sorted(STRATS, key=lambda s: -size[s]):
        xs_n, ys = [], []
        for m in ns:
            work = assigned_work(s, run, m, tasks) if m != n else per_strat[s]
            if work:
                xs_n.append(m)
                ys.append(jain(list(work.values())))
        handles[s], = ax2.plot(
            xs_n, ys, color=fc.COLOR[s], marker=fc.MARKER[s],
            markersize=size[s], markeredgecolor="white", markeredgewidth=1.2,
            linewidth=3 if size[s] > 7 else 2, label=s)
    ax2.axhline(1.0, color=fc.BASELINE, linewidth=1, linestyle=(0, (4, 3)))
    ax2.text(ns[0], 1.0, "even split", ha="left", va="bottom",
             fontsize=8.5, color=fc.MUTED)
    ax2.set_ylim(0, 1.08)
    ax2.set_xticks(ns)
    ax2.set_xlabel("Number of tasks")
    ax2.set_ylabel("Jain's fairness index")
    ax2.set_title("(b) Fairness of the distribution vs n")
    ax2.legend([handles[s] for s in STRATS], STRATS,
               loc="lower right", ncols=2)

    fig.suptitle("Load balance: assigned Mcycles per MEC, "
                 "five strategies", y=1.02)
    fig.text(0.01, -0.02,
             "POB/POR from the run's assigned-work CSVs; BO/JPOFH/EDITORS "
             "replayed offline (deterministic decisions); CO and LE route "
             "no work to MECs and are omitted",
             fontsize=8.5, color=fc.MUTED)
    fig.tight_layout()
    fc.save(fig, f"load_balance_all_{run}_n{n}",
            note=f"run={run} n={n} ns={ns} strategies={','.join(STRATS)}")


if __name__ == "__main__":
    main()
