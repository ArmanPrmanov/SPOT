# JPOFH: Containernet port (SPOT harness)

This directory runs **JPOFH** (Sahni et al., IEEE TPDS 2021) as another
offloading strategy inside the SPOT emulation, so it can be compared to SPOT's
POB / POR / BO / CO / LE on the **same** topology, workload, containers and
transfer measurement.

- The reference pure-simulation implementation of JPOFH is in
  [`../jpofh/`](../jpofh/) (no Docker; reproduces the paper's numbers).
- This port drops the analytical network model and measures real transfers
  through the SPOT harness, so SPOT and JPOFH are measured identically and only
  the decision differs.

## Why a port was needed

A Containernet-measured SPOT number cannot be compared with an analytically
computed JPOFH number: the difference would come from the measurement pipeline,
not the algorithm. Environment, workload and metric are therefore held
identical, and the port reuses SPOT's own functions for everything except the
decision.

| Stage | Source |
|-------|--------|
| Topology / Docker / capacity maps | `network_graph.setup_experiment` |
| Workload | `network_graph.read_tasks_file` (the same `tasks.json`) |
| **Decision** | **`jpofh_strategy` (this dir)**: JPOFH Eqs. 22/23/24 |
| Transfer measurement | `network_graph.docker_send_to` |
| Result / discard output | `network_graph.save_results`, `discard_by_deadline`, `save_discard_task` |

## Files

| File | Role |
|------|------|
| [jpofh_strategy.py](jpofh_strategy.py) | JPOFH decision + measurement loop. Pure Python + `networkx`; has a self-test that runs **without Docker/Mininet**. |
| [run_jpofh.py](run_jpofh.py) | Linux/Docker entry point; wires SPOT's helpers and writes `results/<run-id>/JPOFH_<n>.csv`. |

## Model mapping (SPOT task to JPOFH job)

JPOFH assumes **independent, arbitrarily divisible** tasks; SPOT tasks are DAGs
of subtasks. Each SPOT task is treated as one divisible JPOFH job, which makes
JPOFH the dependency-unaware partial-offloading baseline.

| JPOFH symbol | Mapped from | Notes |
|--------------|-------------|-------|
| `CL_i` computation load | sum of `subtask.comp` (cycles) | comp time = `CL / PS` |
| `D_i` input data | `task.memory` (KB) | bytes actually sent |
| `PS_j` processing speed | `capacity_map[node][-1]` (cycles/s) | MEC >> host |
| `z_i` origin device | `task.host` | an IoT host |
| remote devices | the MEC servers | vertical host-to-MEC offloading |
| `Trel_i` release time | sequential service | each task is released after the previous one completes, so no waiting or flow conflicts carry across tasks |
| routing path | `nx.shortest_path` on the Floodlight graph | single path (K=1) |

The paper does not return the offloaded result to the origin. This port does
measure a MEC-to-host return of the final subtask's result, so that every
strategy in the comparison pays the same transfer legs.

## Decision vs measurement

- **Decision (JPOFH's equations).** In rank order (Eq. 24), for each task pick
  the MEC and ratio `x` that minimise the local finish time (Eq. 19), with `x`
  from Eq. 22 and the flow start `S` from Eq. 23 (shared-link constraint).
  `NOMINAL_BW` (in `jpofh_strategy.py`) is used **only** for the decision's
  `H·D/R` transfer estimate.
- **Measurement.** The chosen `x·memory` KB are sent host to MEC and the result
  is returned MEC to host; completion time = `max(local_branch, wait + transfer
  + remote_comp + return_transfer)` (Eq. 18/20), with the same `comp/PS`
  computation-time model as SPOT. Capacity is reserved and released as in SPOT,
  and the same memory-fit and deadline rules decide discards.

## Running

**Main run.** JPOFH is one of the seven strategies in `network_graph.py`'s
`__main__`, so a normal experiment run (`python network_graph.py`, prerequisites
in [../README.md](../README.md)) produces `JPOFH_<n>.csv` alongside
POB / POR / BO / CO / LE / EDITORS.

**Re-run a single strategy.** To redo just JPOFH (e.g. after a crash), run the
standalone runner **from the repository root** (so `network_graph`,
`tasks.json` and `results/` resolve):

```bash
python jpofh_containernet/run_jpofh.py        # default task count (120)
python jpofh_containernet/run_jpofh.py 20     # 20 tasks
```

Set `SPOT_RUN_ID` to write into an existing run directory.

### Validate the logic without Mininet

The decision/measurement logic runs standalone with a fake (no-network)
measurement function:

```bash
python jpofh_containernet/jpofh_strategy.py
```

This prints per-task completion times on a small synthetic topology and checks
them against hand-derived values.

## Caveats

- JPOFH here offloads **host to MEC only** (vertical), matching SPOT's offload
  targets; the paper's arbitrary device-to-device offloading is not modelled.
- Single routing path (K=1), consistent with SPOT's `find_shortest_paths`; the
  paper found K had little effect.
- Under batch release (all tasks released at t=0 with shared busy clocks and
  flows) JPOFH's completion times are roughly 4-5x worse; the paper itself
  evaluates staggered releases.
