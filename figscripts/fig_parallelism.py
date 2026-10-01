"""Figure: degree of parallelism.

Mean number of distinct COMPUTE locations per task, per strategy. Separates
three cases: BO gets neither pipelining nor parallelism, EDITORS gets
pipelining only, POB gets both.

Placements are replayed offline with the production decision code
(fig_replay.py, no Docker needed): POB/POR via
assign_subtasks_to_servers_or_hosts (POR is random -> mean over --reps
seeded replays), JPOFH via _decide_task (host + at most one MEC by
construction), EDITORS via its trust setup + assign_subtasks. BO/CO/LE
place whole tasks on one node: exactly 1 by construction.

    python figscripts/fig_parallelism.py [--n 100] [--reps 5] [--seed 1]
"""
import argparse
import random

import numpy as np
import matplotlib.pyplot as plt

import fig_common as fc
import fig_replay as fr


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--n", type=int, default=100, help="tasks from tasks.json")
    ap.add_argument("--reps", type=int, default=5,
                    help="seeded replays averaged for the random POR")
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args(argv)

    tasks = fr.read_tasks(args.n)
    means = {}

    placements, d = fr.replay_pob(tasks, random_assign=0)
    means["POB"] = np.mean([len(set(p.values())) for _, p in placements])
    print(f"POB     mean {means['POB']:.2f} nodes/task ({d} discards)")

    por_means = []
    for rep in range(args.reps):
        random.seed(args.seed + rep)
        placements, d = fr.replay_pob(tasks, random_assign=1)
        por_means.append(np.mean([len(set(p.values()))
                                  for _, p in placements]))
    means["POR"] = float(np.mean(por_means))
    print(f"POR     mean {means['POR']:.2f} nodes/task "
          f"(mean of {args.reps} seeded reps: "
          f"{', '.join(f'{m:.2f}' for m in por_means)})")

    means["BO"] = means["CO"] = means["LE"] = 1.0

    decisions, d = fr.replay_jpofh(tasks)
    means["JPOFH"] = np.mean([1 if mec is None or x >= 1.0 else 2
                              for _, mec, x in decisions])
    print(f"JPOFH   mean {means['JPOFH']:.2f} nodes/task ({d} discards)")

    placements, d = fr.replay_editors(tasks)
    means["EDITORS"] = np.mean([len(set(p.values())) for _, p in placements])
    print(f"EDITORS mean {means['EDITORS']:.2f} nodes/task ({d} discards)")

    fc.apply_style()
    fig, ax = plt.subplots(figsize=(7.2, 4.2))
    xs = np.arange(len(fc.STRATEGIES))
    bars = ax.bar(xs, [means[s] for s in fc.STRATEGIES],
                  0.62, color=[fc.COLOR[s] for s in fc.STRATEGIES],
                  edgecolor="white", linewidth=1.5)
    fc.bar_value_labels(ax, bars)
    ax.set_xticks(xs, fc.STRATEGIES)
    ax.set_ylim(0, max(means.values()) * 1.22)
    ax.set_ylabel("Mean compute locations per task")
    ax.set_title(f"Degree of parallelism  (n = {args.n})")
    fig.text(0.01, -0.04,
             "offline replay of the production placement decisions; "
             "BO/CO/LE place whole tasks (= 1 by construction), JPOFH splits "
             f"host ↔ one MEC (≤ 2); POR = mean of {args.reps} seeded reps",
             fontsize=8.5, color=fc.MUTED)
    fig.tight_layout()
    fc.save(fig, f"parallelism_n{args.n}",
            note=f"n={args.n} reps={args.reps} seed={args.seed} means="
                 + " ".join(f"{s}:{means[s]:.2f}" for s in fc.STRATEGIES))


if __name__ == "__main__":
    main()
