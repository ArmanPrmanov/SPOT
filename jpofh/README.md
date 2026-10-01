# JPOFH: Joint Partial Offloading and Flow scheduling Heuristic

A self-contained Python implementation of **Algorithm 1 (JPOFH)** from

> Y. Sahni, J. Cao, L. Yang, and Y. Ji,
> **"Multi-Hop Multi-Task Partial Computation Offloading in Collaborative Edge Computing,"**
> *IEEE Transactions on Parallel and Distributed Systems*, vol. 32, no. 5,
> pp. 1133-1145, May 2021.

This is a **pure discrete-event simulation** (no Docker, Containernet or SDN
controller, unlike the SPOT code in the repo root). It only needs `numpy` and
`networkx`.

## What the algorithm does

`N` independent tasks are generated at different devices of a multi-hop edge
mesh. Each task is **partially offloaded**: split into a *local* component (kept
on its origin device `z_i`) and an *offloaded* component (sent to a remote
device `j` over one of `K` routing paths). JPOFH jointly decides, per task:

- the remote device `j*` and routing path `k*`,
- the **partial offloading ratio** `x` (fraction sent to the remote device),
- the **start time of the input-data flow** `S`, respecting the constraint that
  *no two flows use the same link at the same time*,

so as to minimise the average task completion time (Eq. 17).

## Files

| File | Contents |
|------|----------|
| [models.py](models.py) | Network model `G=(V,E)` (random connected mesh, K-shortest paths via Yen's algorithm) and application model (task generation). Sections 3.1 and 5.1. |
| [jpofh.py](jpofh.py) | The JPOFH algorithm: Algorithm 1 and Eqs. (18)-(24). |
| [benchmarks.py](benchmarks.py) | Benchmark solutions LE, RE, SOFS (Section 5.2). |
| [simulate.py](simulate.py) | Experiment driver reproducing Section 5.3 (default comparison + parameter sweeps). |

## Running

```bash
cd jpofh
python simulate.py                 # default-parameter comparison (Tables 3/4)
python simulate.py --sweep tasks   # effect of number of tasks   (Fig. 2)
python simulate.py --sweep devices # effect of number of devices (Fig. 4)
python simulate.py --sweep paths   # effect of number of routing paths (Fig. 6)
python simulate.py --sweep data    # effect of amount of input data (Fig. 8)

# knobs
python simulate.py --tasks 5 --devices 10 --paths 3 --iters 30 --seed 1
python simulate.py --random-device # random-device task placement (Table 4)
```

Completion times are reported in **milliseconds**; the paper reports the same
values in seconds (≈ 0.010 s ≡ 10 ms).

### Example (default parameters, 30 iterations)

```
solver        completion (ms)       runtime (ms)    vs JPOFH
LE        12.83 +/- 0.89           0.06            +20.3%
RE        13.76 +/- 0.50           0.28            +25.7%
SOFS       9.75 +/- 0.37           0.35             -4.8%
JPOFH     10.22 +/- 0.40           0.47
```

JPOFH's absolute completion time (~10.2 ms) matches the paper's Table 3
(0.0103 s). See "Reproduction notes" below on SOFS.

## Mapping to the paper's equations

Units are SI throughout (`models.py`): processing speed in cycles/s, load in
cycles, data in bits, bandwidth in bits/s, time in seconds.

Let `CT_ij = CL_i / PS_j` be the computation time of task `i` on device `j`,
`Tbusy_j` the device-free-at time, and `H`, `R` the hops and bandwidth of a path.

| Paper | Location | Meaning |
|-------|----------|---------|
| **Eq. 24** `rank_i = Trel_i + CL_i / PS̄` | `jpofh.rank_metric` | priority key; tasks scheduled in increasing rank |
| **Eq. 23** `S_ijk` | `jpofh._flow_start` | flow start ≥ `Trel_i`, and after any earlier flow sharing a link |
| **Eq. 22** `x_ijk` | `jpofh.jpofh` (inner loop) | ratio balancing Eq. 19 against the Eq. 21 upper bound |
| **Eq. 19** `T_ijk_loc = (1−x)·CT_izi + max{Tbusy_zi, Trel_i}` | `jpofh.jpofh` | local finish time |
| **Eq. 20** `T_ijk_off = x·CT_ij + max{Tbusy_j, Trel_i, S + x·H·D/R}` | `jpofh.jpofh` | offloaded finish time |
| **Eq. 18** `Tft_i = min_{j,k} …` | `jpofh.jpofh` (lines 18–19) | device/path selection & finish time |
| **Algorithm 1** | `jpofh.jpofh` | the full loop, incl. the "first task runs locally" case (lines 12–15) |
| **Eq. 33/34** (LE) | `benchmarks.local_execution` | run every task on its origin device |
| **Eq. 35** (SOFS ratio) | `benchmarks.sofs` | offloading ratio decoupled from waiting/flow start |

### Faithfulness notes / assumptions

- **Selection metric.** Algorithm 1 line 18–19 selects `j*,k*` and sets the
  finish time by `min_{j,k} T_ijk_loc`. Because `x` (Eq. 22) is chosen to make
  `T_loc` equal to the *upper bound* of `T_off`, minimising `T_loc` minimises the
  balanced completion time. The code follows the pseudocode literally.
- **Ratio clamping.** Eq. 22 can yield `x ∉ [0,1]` (e.g. offloading strongly
  (dis)advantageous); `x` is clamped to `[0,1]`. The paper does not state this
  explicitly but it is required for a physical ratio.
- **First task.** Per lines 12–15, the first task in priority order is forced to
  run purely locally (`x = 0`, `T_off = ∞`), since no prior flow exists.
- **K-shortest paths** are ranked by hop count (Yen's algorithm via
  `networkx.shortest_simple_paths`); path bandwidth `R` is the minimum link
  bandwidth on the path, hops `H = len(path) − 1`.
- **Network generation** follows Section 5.1: devices placed uniformly in an
  `M×M` area, connected if closer than `2M/5`; the area scales with `M` to keep a
  constant density; regenerated until the graph is connected.

## Benchmarks

- **LE (Local Execution)** (Eq. 33/34): each task runs on its origin device.
- **RE (Remote Execution)**: full offload (`x = 1`) to the remote device with the
  earliest finish time; flows scheduled greedily in priority order.
- **SOFS (Separate Offloading & Flow Scheduling)**: offloading ratio from Eq. 35,
  tasks handled first-come-first-serve, and the device/path is
  chosen *without* flow-conflict knowledge (flow optimistically assumed to start
  at `Trel`); the real conflict-based start time is realised only afterwards.

  > **Reproduction note.** SOFS here is *simplified*: it uses greedy per-task
  > selection rather than the full PDQ/EDF flow scheduler of the paper. In the
  > low-conflict default regime (devices ≥ tasks) it is competitive with JPOFH,
  > which is consistent with the paper's own observation that "the cost of waiting
  > time at devices and start time of network flows … is not significant when the
  > number of devices is large compared to the number of tasks" (Section 5.3.1).
  > As load grows (`--sweep tasks`, tasks ≳ devices) JPOFH's joint decision pulls
  > ahead of SOFS, reproducing the paper's central result.

- **LB (Lower Bound)** (the McCormick-envelope LP relaxation of Section 4.2,
  solved with CVX/Mosek) is **not** implemented here; it requires a convex solver
  and yields an infeasible loose bound only.
```
