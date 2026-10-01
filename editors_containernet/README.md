# editors_containernet

EDITORS run as **another strategy inside the SPOT Containernet emulation**, so it
is measured under identical conditions to SPOT's POB / POR / BO / CO / LE and to
[../jpofh_containernet/](../jpofh_containernet/).

Reference paper: Baker et al., *"EDITORS: Energy-aware Dynamic Task Offloading
using Deep Reinforcement Transfer Learning in SDN-enabled Edge Nodes"*, Internet
of Things 25 (2024) 101118. The offline (analytical) reference implementation
lives in [../editors/](../editors/); this directory is the networked variant.

## Decision vs. measurement

| Part | How | Faithful to |
|---|---|---|
| **Decision** | (1) feedback trust management (Algo 1) filters MEC servers into a trusted set; (2) energy-aware per-subtask assignment (Algo 2) using the DRTL cost/Q-value greedy (Algo 3). No LSTM/DRL training; the learned targets are these closed forms. | the paper |
| **Measurement** | the resulting fine-grained schedule is realised exactly as SPOT realises POB: offloaded bytes are actually sent host→MEC and MEC→MEC between dependent subtasks, and the final result is returned to the host; compute time is `comp / processing-speed`; completion is the DAG critical path with the same semantics as `network_graph.task_exec_times`. | SPOT harness |

Only the **decision** differs from SPOT's POB; the topology, workload
(`tasks.json`), Docker containers, transfer measurement, capacity maps and
deadline-discard rule are shared. The only difference is trust filtering and
energy-aware node choice in place of POB's earliest-finish-time placement.

## Files

| File | Role |
|---|---|
| [editors_strategy.py](editors_strategy.py) | The strategy: trust set, energy-aware assignment, iperf realisation, critical-path completion time. Reuses `../editors/trust.py` (Algorithm 1). |
| [run_editors.py](run_editors.py) | Linux/Docker entry point; reuses `network_graph.py` helpers and writes `results/<run-id>/EDITORS_<n>.csv`. |

## Running

**Main run.** EDITORS is one of the seven strategies in `network_graph.py`'s
`__main__`, so a normal experiment run (`python network_graph.py`, prerequisites
in [../README.md](../README.md)) produces `EDITORS_<n>.csv` alongside
POB / POR / BO / CO / LE / JPOFH.

**Re-run a single strategy.** To redo just EDITORS (e.g. after a crash) without
a full seven-strategy sweep, run the standalone runner **from the repository
root**:

```bash
python editors_containernet/run_editors.py            # default 120 tasks
python editors_containernet/run_editors.py 200        # override task count
```

Output goes to a fresh `results/<run-id>/` directory (set `SPOT_RUN_ID` to
reuse an existing run's directory):

```
results/<run-id>/EDITORS_<n>.csv                 # Task, Time
results/<run-id>/discarded_task_EDITORS_<n>.csv
```

## Docker-free self-test

The strategy logic can be validated on any machine (Windows included) without
Docker/Mininet; a tiny fake topology and a deterministic `fake_measure` stand
in for the real network:

```bash
python editors_containernet/editors_strategy.py
```

## Node trust profiles

Each MEC server is given a static (energy, latency, memory) profile from the
paper's Table 4 (`NODE_PROFILES` in `editors_strategy.py`), assigned in name
order. In a real deployment these would come from live SDN telemetry; swap
`build_node_metrics` to feed measured values into `compute_trust`.

To reproduce the paper's fixed trust weights instead of entropy-derived ones,
pass `weights=(0.5120, 0.1935, 0.3010), threshold=0.6984` through
`editors_thread(...)`.
