"""EDITORS -- Energy-aware Dynamic Task Offloading (heuristic reproduction).

Reference: Baker et al., "EDITORS: Energy-aware Dynamic Task Offloading using
Deep Reinforcement Transfer Learning in SDN-enabled Edge Nodes",
Internet of Things 25 (2024) 101118.

The paper's decision logic (trust score, subtask division, and the DRTL
Q-value) reduces to closed-form weighted sums, so we implement it directly
instead of training an LSTM + DRL agent. It is deterministic, needs no dataset, and runs offline
(no Docker / Floodlight / Containernet), which mirrors how the paper itself
evaluated EDITORS analytically on Colab + Ryu/Mininet.

What is faithfully reproduced:
  * Algorithm 1 -- feedback trust management  (see trust.py)
  * Algorithm 2 -- energy-aware subtask division & assignment to edge nodes
  * Algorithm 3 -- offloading-policy selection via the Q-value / cost function
                   (as a deterministic greedy; the RL episode loop and transfer
                    learning only change *how fast* the same weights are found)
  * Eq. (1)     -- cost = w_e * energy + beta * max_latency

Omitted (none of these change the final policy):
  * LSTM trust predictor  -> its target is the Eq. 5 formula we compute exactly
  * DRL training episodes  -> converge to the greedy argmin we compute directly
  * Transfer learning      -> only affects training time, not the final policy

Workloads are loaded from the parent project's tasks.json so results line up
with SPOT's POB / POR / BO / LE strategies.
"""

import csv
import math
import os
import sys
from collections import deque

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
from Task import Task, Subtask  # noqa: E402  (reuse the project's task model)

from trust import compute_trust  # noqa: E402

TASKS_JSON = os.path.join(BASE, "tasks.json")
RESULTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")

# --- Tunable model constants --------------------------------------------------
HOST_PROC_RATE = 1000          # host compute rate (matches create_host_map)
SERVER_PROC_RATE = 1_000_000   # edge-node compute rate (matches create_server_map)
BANDWIDTH = 100_000            # link rate for analytical transfer time (mem-units/ms)
BETA = 1.0                     # delay-constraint weight in the cost function (Eq. 1)

# Per-node capacity available to the *concurrent* subtasks of a single task.
# Capacity is subtracted while a task is being placed and released afterwards,
# mirroring partial_offloading_thread in the SPOT simulation.
NODE_MEMORY_CAP = 5_000
NODE_CPU_CAP = 500_000
NODE_ENERGY_BUDGET = 5_000_000

# Static trust profiles (energy J, latency ms, memory MB), from the paper's
# Table 4. The first ``num_nodes`` entries are used, cycling if more are needed.
NODE_PROFILES = [
    ("mec1", 7040, 80, 50),
    ("mec2", 7040, 100, 60),
    ("mec3", 11000, 120, 55),
    ("mec4", 7040, 90, 70),
    ("mec5", 11000, 110, 65),
    ("mec6", 7040, 200, 200),
]


# ---------------------------------------------------------------------------
# Edge-node model
# ---------------------------------------------------------------------------
def build_edge_nodes(num_nodes):
    """Create ``num_nodes`` edge nodes with capacity + trust metrics."""
    nodes = {}
    for i in range(num_nodes):
        name, energy, latency, memory = NODE_PROFILES[i % len(NODE_PROFILES)]
        name = name if i < len(NODE_PROFILES) else f"mec{i + 1}"
        nodes[name] = {
            # trust metrics (static profile)
            "energy": energy, "latency": latency, "memory": memory,
            # runtime energy coefficient: energy per unit of subtask compute
            "energy_coeff": energy / 10_000.0,
            # compute / capacity
            "proc_rate": SERVER_PROC_RATE,
            "bandwidth": BANDWIDTH,
            "mem_cap": NODE_MEMORY_CAP,
            "cpu_cap": NODE_CPU_CAP,
            "energy_cap": NODE_ENERGY_BUDGET,
        }
    return nodes


def _reset_capacity(nodes):
    for n in nodes.values():
        n["mem_free"] = n["mem_cap"]
        n["cpu_free"] = n["cpu_cap"]
        n["energy_free"] = n["energy_cap"]


# ---------------------------------------------------------------------------
# Cost / energy / latency of running one subtask on one edge node
# ---------------------------------------------------------------------------
def subtask_energy(subtask, node):
    """Runtime energy to execute a subtask on ``node`` (drives Eq. 1)."""
    return node["energy_coeff"] * subtask.comp


def subtask_latency(subtask, node):
    """Upload + compute time for a subtask offloaded to ``node`` (ms)."""
    upload = subtask.memory / node["bandwidth"]
    compute = subtask.memory / node["proc_rate"]
    return upload + compute


# ---------------------------------------------------------------------------
# Algorithm 2 + 3: energy-aware subtask assignment among trusted edge nodes
# ---------------------------------------------------------------------------
def assign_task(task, nodes, trust, trusted, w_e, w_l, w_ts):
    """Assign every subtask of ``task`` to a trusted edge node.

    Returns ``{subtask_id: node_name}`` or ``None`` if the task cannot be placed
    (no trusted node has the required memory / CPU / energy for some subtask),
    in which case any partial reservation is rolled back.
    """
    placement = {}
    reserved = []  # (node_name, memory, cpu, energy) for rollback

    for subtask in task.subtasks:
        candidates = [
            n for n in nodes
            if n in trusted
            and nodes[n]["mem_free"] >= subtask.memory
            and nodes[n]["cpu_free"] >= subtask.comp
            and nodes[n]["energy_free"] >= subtask_energy(subtask, nodes[n])
        ]
        if not candidates:
            _rollback(nodes, reserved)
            return None

        chosen = _select_node(subtask, candidates, nodes, trust, w_e, w_l, w_ts)

        energy = subtask_energy(subtask, nodes[chosen])
        nodes[chosen]["mem_free"] -= subtask.memory
        nodes[chosen]["cpu_free"] -= subtask.comp
        nodes[chosen]["energy_free"] -= energy
        reserved.append((chosen, subtask.memory, subtask.comp, energy))
        placement[subtask.subtask_id] = chosen

    return placement


def _select_node(subtask, candidates, nodes, trust, w_e, w_l, w_ts):
    """DRTL policy (Algorithm 3): pick the trusted node that minimises the
    weighted energy + latency cost while preferring higher trust.

    The paper's literal Q-value ``w_e*E + w_l*L + w_ts*TS`` is sign-inconsistent
    (higher energy/latency is worse, higher trust is better), so we minimise a
    normalised cost in which every term points the same way -- this realises the
    paper's stated objective of minimising energy and latency on trusted nodes.
    """
    energies = [subtask_energy(subtask, nodes[n]) for n in candidates]
    latencies = [subtask_latency(subtask, nodes[n]) for n in candidates]
    ne = _norm(energies)
    nl = _norm(latencies)

    best, best_score = None, math.inf
    for i, n in enumerate(candidates):
        score = w_e * ne[i] + w_l * nl[i] + w_ts * (1.0 - trust[n])
        if score < best_score:
            best, best_score = n, score
    return best


def _norm(values):
    lo, hi = min(values), max(values)
    if hi == lo:
        return [0.0 for _ in values]
    return [(v - lo) / (hi - lo) for v in values]


def _rollback(nodes, reserved):
    for name, mem, cpu, energy in reserved:
        nodes[name]["mem_free"] += mem
        nodes[name]["cpu_free"] += cpu
        nodes[name]["energy_free"] += energy


# ---------------------------------------------------------------------------
# Metrics: critical-path latency and total energy of a placed task
# ---------------------------------------------------------------------------
def _subtask_by_id(task, subtask_id):
    for s in task.subtasks:
        if s.subtask_id == subtask_id:
            return s
    return None


def task_latency(task, placement, nodes):
    """Longest-path (critical path) completion time through the subtask DAG."""
    node_time = {}
    for s in task.subtasks:
        node_time[s.subtask_id] = subtask_latency(s, nodes[placement[s.subtask_id]])

    start = task.subtasks[0].subtask_id
    queue = deque([(start, node_time[start])])
    longest = 0.0
    while queue:
        sid, acc = queue.popleft()
        longest = max(longest, acc)
        subtask = _subtask_by_id(task, sid)
        for succ in subtask.successors:
            # transmission of intermediate result if predecessor/successor differ
            if placement[sid] == placement[succ]:
                trans = 0.0
            else:
                trans = subtask.result / nodes[placement[sid]]["bandwidth"]
            queue.append((succ, acc + trans + node_time[succ]))

    # return final result from the last subtask's node back to the host
    last = task.subtasks[-1]
    longest += last.result / nodes[placement[last.subtask_id]]["bandwidth"]
    return longest


def task_energy(task, placement, nodes):
    return sum(subtask_energy(s, nodes[placement[s.subtask_id]]) for s in task.subtasks)


# ---------------------------------------------------------------------------
# Task loading (reuse the project's tasks.json / Task model)
# ---------------------------------------------------------------------------
def read_tasks(num_tasks):
    import json
    with open(TASKS_JSON, "r") as f:
        data = json.load(f)

    tasks = []
    for t in data[:num_tasks]:
        task = Task(t["task_id"], t["memory"], t["cpu"], t["host"], t["deadline"])
        subtasks = []
        for s in t["subtasks"]:
            st = Subtask(s["subtask_id"], s["memory"], s["cpu"], s["result"],
                         t["task_id"], t["host"])
            st.predecessors = list(s["predecessors"])
            st.successors = list(s["successors"])
            subtasks.append(st)
        task.subtasks = subtasks
        tasks.append(task)
    return tasks


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------
def run_editors(num_tasks, num_nodes=5, weights=None, threshold=None,
                trust_weight=0.3, verbose=True):
    """Run EDITORS over ``num_tasks`` tasks and return per-task metrics."""
    nodes = build_edge_nodes(num_nodes)
    node_metrics = {n: {"energy": nodes[n]["energy"],
                        "latency": nodes[n]["latency"],
                        "memory": nodes[n]["memory"]} for n in nodes}

    # --- feedback trust management (Algorithm 1) ---
    trust, trusted, info = compute_trust(node_metrics, weights, threshold)
    w_e, w_l, w_u = info["weights"]
    if verbose:
        print(f"Trust weights (energy, latency, memory) = {info['weights']}")
        print(f"Trust threshold theta = {info['threshold']}")
        print(f"Trust scores = {trust}")
        print(f"Trusted nodes = {sorted(trusted)}\n")

    tasks = read_tasks(num_tasks)
    results = []          # (task_id, latency, energy, cost)
    discarded = set()

    for task in tasks:
        _reset_capacity(nodes)  # per-task capacity, matching SPOT's release model

        placement = assign_task(task, nodes, trust, trusted, w_e, w_l, trust_weight)
        if placement is None:
            discarded.add(task.task_id)             # no trusted node had capacity
            continue

        latency = task_latency(task, placement, nodes)
        if latency > task.deadline:
            discarded.add(task.task_id)             # deadline miss
            continue

        energy = task_energy(task, placement, nodes)
        cost = w_e * energy + BETA * latency        # cost function (Eq. 1)
        results.append((task.task_id, round(latency, 4), round(energy, 4), round(cost, 4)))

    return results, discarded, info


def save_results(results, num_tasks):
    os.makedirs(RESULTS_DIR, exist_ok=True)
    path = os.path.join(RESULTS_DIR, f"EDITORS_{num_tasks}.csv")
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Task", "Latency", "Energy", "Cost"])
        for row in sorted(results):
            w.writerow(row)
    return path


def save_discarded(discarded, num_tasks):
    os.makedirs(RESULTS_DIR, exist_ok=True)
    path = os.path.join(RESULTS_DIR, f"discarded_task_EDITORS_{num_tasks}.csv")
    with open(path, "w", newline="") as f:
        csv.writer(f).writerow(sorted(discarded))
    return path


if __name__ == "__main__":
    NUM_TASKS = 120     # how many tasks to load from tasks.json (matches SPOT)
    NUM_NODES = 5       # number of edge nodes (paper uses 5)

    results, discarded, info = run_editors(NUM_TASKS, NUM_NODES)

    total_latency = sum(r[1] for r in results)
    total_energy = sum(r[2] for r in results)
    total_cost = sum(r[3] for r in results)

    print("----- EDITORS (heuristic reproduction) -----")
    print(f"Tasks completed : {len(results)} / {NUM_TASKS}")
    print(f"Discarded       : {len(discarded)}  {sorted(discarded)}")
    print(f"Total latency   : {round(total_latency, 3)} ms")
    print(f"Total energy    : {round(total_energy, 3)} J")
    print(f"Total cost      : {round(total_cost, 3)} J-ms  (Eq. 1)")

    print("\nSaved:")
    print(" ", save_results(results, NUM_TASKS))
    print(" ", save_discarded(discarded, NUM_TASKS))
