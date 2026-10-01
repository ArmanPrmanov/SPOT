# figures/

Generated figures for the paper, produced by the `fig_*.py` scripts in
[figscripts/](../figscripts/).

**To regenerate everything at once** (one output directory, transfer-leg
figure skipped unless a log is passed):

```
python figscripts/fig_main.py [--run RUN] [--log XFER_LOG]
```

Each script invocation writes into its own timestamped directory,
`figures/{YYYYmmdd-HHMMSS}/`, stamped once per process, so `fig_main.py`
lands everything in one directory, while separate shell invocations each
get their own. To collect separate invocations' output in ONE directory,
set `SPOT_FIG_ID` (mirrors `SPOT_RUN_ID` for `results/`):

```
SPOT_FIG_ID=paper-v3 python figscripts/fig_mean_completion_vs_size.py
SPOT_FIG_ID=paper-v3 python figscripts/fig_load_balance.py
```

Every figure is saved as `.png` (300 dpi, for quick viewing) and `.pdf`
(vector, for LaTeX `\includegraphics`), and each directory gets a
`sources.txt` recording which script, results run, and parameters produced
each file.

| Script | Figure | Data it needs |
|---|---|---|
| `fig_mean_completion_vs_tasks.py` | mean completion time vs number of tasks; writes a broken-axis `*_all` variant (LE in its own band) and a `*_noLE` variant | `results/{RUN}/{STRAT}_{n}.csv` |
| `fig_total_completion_vs_tasks.py` | total (summed) completion time vs number of tasks, same two variants | same |
| `fig_mean_completion_vs_size.py` | mean completion time vs task size (binned; JPOFH Fig. 8 style) | `results/{RUN}/{STRAT}_{n}.csv` + `tasks.json` |
| `fig_total_completion_vs_size.py` | total (summed) completion time vs task size | same |
| `fig_load_balance.py` | per-MEC assigned Mcycles + Jain's fairness vs n, POB vs POR | `results/{RUN}/balanced_{n}.csv`, `random_{n}.csv` |
| `fig_load_balance_all.py` | same two panels across five strategies (adds BO/JPOFH/EDITORS by offline replay; CO/LE route no MEC work) | same + `tasks.json` |
| `fig_transfer_legs.py` | mean completion split into compute / upload / horizontal / return | a `SPOT_XFER_LOG` capture + `results/{RUN}/{STRAT}_{n}.csv` |
| `fig_parallelism.py` | degree of parallelism (mean compute locations per task) | `tasks.json` only (offline decision replay, no Docker) |

The offline decision replay used by `fig_load_balance_all.py` and
`fig_parallelism.py` lives in `fig_replay.py` (exact for
POB/BO/JPOFH/EDITORS, since their decisions consume no measurements;
POR is random and averaged over seeded reps where used).

All scripts default to the newest `results/{YYYYmmdd-HHMMSS}/` run and the
largest task count available in it; override with `--run` / `--n`. Run any
script with `-h` for the full options.

The transfer-leg figure needs a transfer log; capture one on the Linux box
with

```
SPOT_XFER_LOG=results/$RUN/xfer.log python network_graph.py
```

then point the script (or `fig_main.py`) at it with `--log`.
