# SPOT: SDN-based Dependency-Aware Partial Offloading in IoT Edge Networks

Simulation code for SPOT, a partial offloading scheme for Multi-access Edge
Computing (MEC). IoT applications are modelled as DAGs of dependent subtasks.
An SDN controller's global view of the network is used to place each subtask
individually, either on an MEC server (vertical offloading) or moved between
MEC servers (horizontal offloading), and to schedule the flows that carry
intermediate results between subtasks.

The evaluation runs on a real emulated network: Docker containers act as IoT
hosts, MEC servers and a cloud node inside a Containernet topology, a
Floodlight controller provides the topology view, and every data transfer
between nodes is actually sent over the emulated links and timed.

## Strategies

All strategies run in one invocation of `network_graph.py`, in this order, on
the same pre-generated workload (`tasks.json`):

| Name | Description |
|---|---|
| **POB** | SPOT, balanced partial offloading. Each subtask goes to the feasible MEC server with the earliest finish time, `max(predecessor finish, node queue) + comp / rate`, with hop count as a tie-break. The IoT host is used only if no server fits. |
| **POR** | Partial offloading to a random feasible MEC server (dependency-unaware ablation of POB). |
| **BO** | Binary offloading: the whole task goes to the server with the least assigned work, or stays on the origin host if nothing fits. |
| **CO** | Cloud offloading: the whole task goes to the cloud node (slower WAN link, faster CPU). |
| **LE** | Local execution on the IoT host. |
| **JPOFH** | Joint partial offloading and flow scheduling heuristic, Sahni et al., IEEE TPDS 2021. |
| **EDITORS** | Trust-filtered, energy-aware fine-grained offloading, Baker et al., Internet of Things 25, 2024. |

Between strategies, `assert_capacity_restored` checks that the previous
strategy released all the capacity it reserved, so the order does not bias the
results.

## Repository layout

| Path | Contents |
|---|---|
| `network_graph.py` | Main experiment runner: reads the topology from Floodlight, builds the graph, runs every strategy, writes results. |
| `topology.py` | Containernet topology: 4 MEC servers (`10.0.0.x`), 8 IoT hosts (`10.0.1.x`), 1 cloud node (`10.0.2.1`), 4 OpenFlow switches. |
| `Task.py` | Task / subtask model and random DAG generation. |
| `tasks.json` | Pre-generated workload used for all runs. |
| `app.py`, `Dockerfile.server` | Flask app and image (`test_server:latest`) running inside every node container. |
| `exec_pool.py`, `xfer_*.py` | Persistent `docker exec` workers and the TCP transfer client/server used to time data transfers (`SPOT_XFER=iperf` switches to one-shot `iperf` transfers). |
| `jpofh_containernet/`, `editors_containernet/` | JPOFH and EDITORS baselines ported to this harness. |
| `jpofh/`, `editors/` | Standalone reference implementations of the two baselines (no Docker). |
| `figscripts/`, `plot_results.py` | Figure generation from result CSVs. |
| `results/20260719-153332/` | Results of the sweep reported in the paper (20 to 120 tasks). |
| `figures/` | Figures generated from that run. |

## Requirements

- Linux with Docker and root access
- [Containernet](https://github.com/containernet/containernet) and Open vSwitch
- Python 3 with `networkx`, `requests`, `matplotlib`
- Floodlight controller image: `piyushk2001/floodlight-controller`

## Quick start

```bash
# 1. SDN controller (REST on 8080, OpenFlow on 6653)
docker run -d --name floodlight -p 8080:8080 -p 6653:6653 piyushk2001/floodlight-controller

# 2. Node image
docker build -f Dockerfile.server -t test_server:latest .

# 3. Topology (opens the Mininet CLI, keep it running)
sudo python3 topology.py

# 4. In another shell, run the experiment
python3 network_graph.py
```

Results are written to `results/{YYYYmmdd-HHMMSS}/`, one CSV per strategy and
task count (`POB_120.csv`, ...) plus `discarded_task_{STRATEGY}_{n}.csv` for
tasks that missed their deadline. Experiment parameters (`num_tasks`,
`num_subtask`, deadlines, load factor) are set at the bottom of
`network_graph.py`; node capacities are the `MEC_CAPACITY`, `CLOUD_CAPACITY`
and `HOST_CAPACITY` constants at the top of the same file.

See [HOW_TO_RUN.md](HOW_TO_RUN.md) for the full checklist, verification steps
and troubleshooting.

The placement logic can be checked without Docker:

```bash
python placement_selftest.py
python jpofh_containernet/jpofh_strategy.py
python editors_containernet/editors_strategy.py
```

To regenerate the figures from the included results:

```bash
python plot_results.py
python figscripts/fig_main.py --run 20260719-153332
```

## Authors

Arman Prmanov and Dina Almassova, Nazarbayev University
