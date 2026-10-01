# EDITORS (heuristic reproduction)

A self-contained implementation of the offloading logic from:

> Baker, Al Aghbari, Khedr, Ahmed, Girija.
> **"EDITORS: Energy-aware Dynamic Task Offloading using Deep Reinforcement
> Transfer Learning in SDN-enabled Edge Nodes."** *Internet of Things* 25 (2024) 101118.

It reproduces EDITORS' *decisions* with the paper's own closed-form equations
instead of training the ML models.

## Why no ML training is needed

The paper describes two "learned" components, but both reduce to formulas:

| Paper component | What it claims | What the equations actually are |
|---|---|---|
| LSTM trust predictor (Algo 1) | learns trust scores | trust = Shannon-entropy-weighted sum of energy/latency/memory, thresholded (Eqs. 3–6). The LSTM only regresses onto this formula. |
| DRTL / Q-learning (Algo 3) | learns offloading policy | the "Q-value" update is `w_e·E + w_l·L + w_ts·TS`, a weighted sum, not a Bellman update. Training converges to the greedy argmin we compute directly. |
| Subtask division (Algo 2) | part of "DRTL" | plain energy-aware greedy assignment, no learning. |
| Transfer learning | adapt new nodes | affects **training time only**, not the final policy. |

So training an LSTM + DRL agent would only reproduce quantities we can evaluate
in closed form. This runs offline, deterministically, with no dataset. That also
matches how the paper evaluated EDITORS (analytically on Colab + Ryu/Mininet).

## Files

| File | Role |
|---|---|
| [trust.py](trust.py) | Algorithm 1: Shannon-entropy weights, trust score, trusted/untrusted threshold. |
| [editors.py](editors.py) | Algorithms 2-3: edge-node model, energy-aware subtask assignment, DRTL policy selection, cost/latency/energy metrics, and the runner. |
| `results/` | Output CSVs (`EDITORS_{n}.csv`, `discarded_task_EDITORS_{n}.csv`). |

Workloads are loaded from the parent project's [../tasks.json](../tasks.json)
via the existing `Task`/`Subtask` model, so results are directly comparable to
SPOT's POB / POR / BO / LE strategies.

## Running

```bash
cd editors
python editors.py
```

No Docker, Floodlight, or Containernet required; it runs on Windows or Linux
with just Python + the parent repo's `Task.py` and `tasks.json`.

Knobs at the bottom of `editors.py` (`__main__`): `NUM_TASKS`, `NUM_NODES`.
Model constants (`SERVER_PROC_RATE`, `BANDWIDTH`, `BETA`, capacities, and the
Table-4 node trust profiles) are near the top of `editors.py`.

### Reproducing the paper's trust experiments

`run_editors(...)` accepts the paper's fixed weights and threshold:

```python
from editors import run_editors
run_editors(120, num_nodes=5, weights=(0.5120, 0.1935, 0.3010), threshold=0.6984)
```

By default the weights are derived from the node data via Shannon entropy and
the threshold defaults to the mean trust score.

## Mapping to the paper

- **Algorithm 1**: `trust.compute_trust` (Eqs. 3–6).
- **Algorithm 2**: `editors.assign_task` (energy-aware placement on trusted nodes).
- **Algorithm 3**: `editors._select_node` (Q-value / cost greedy). The paper's
  literal `Q = w_e·E + w_l·L + w_ts·TS` is sign-inconsistent (higher energy and
  latency are *worse*, higher trust is *better*); we minimise a normalised cost
  where all three terms point the same way, realising the stated objective of
  minimising energy and latency on trusted nodes.
- **Eq. (1)** cost function: `cost = w_e·energy + BETA·max_latency` per task.

## Limitations

- The Table-4 node energy/latency/memory values are illustrative profiles from
  the paper; real deployments would feed live SDN telemetry into `node_metrics`.
- Because `tasks.json` subtasks are small relative to `SERVER_PROC_RATE`, total
  latency is tiny and deadlines rarely bind; tune `SERVER_PROC_RATE`,
  `BANDWIDTH`, and the capacity constants to stress the deadline/discard path.
- This reproduces the *policy*, not the training dynamics: the paper's
  "60–70% training-time reduction from transfer learning" is a property of the
  learning procedure, which is not reproduced here.
