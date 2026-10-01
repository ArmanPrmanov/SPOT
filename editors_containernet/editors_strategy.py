"""EDITORS offloading strategy, ported to the SPOT Containernet harness.

Runs the EDITORS heuristic (Baker et al., Internet of Things 25 (2024) 101118 -
see ../editors/ for the offline reference implementation) as *another strategy*
inside the same emulation used by SPOT, so it can be compared to POB / POR /
BO / LE on the identical topology, workload (tasks.json), Docker containers and
iperf-based measurement.

Design (why this is a fair comparison):
  * DECISION    - made analytically with EDITORS' own equations:
                  (1) feedback trust management (Algo 1, Eqs. 3-6) filters the
                      MEC servers into a trusted set;
                  (2) energy-aware, per-subtask assignment onto trusted nodes
                      (Algo 2) using the DRTL cost/Q-value greedy (Algo 3).
                  No LSTM/DRL training: the learned targets are these closed
                  forms (see ../editors/README.md).
  * MEASUREMENT - the resulting fine-grained schedule is realised exactly as
                  SPOT realises POB: the offloaded bytes are actually sent with
                  `iperf` (docker_send_to) host->MEC and MEC->MEC between
                  dependent subtasks, and computation time is comp (cycles) /
                  node rate (cycles/s).  Completion time is the DAG critical path,
                  computed with the *same* semantics as network_graph.task_exec_times, so
                  EDITORS numbers are apples-to-apples with POB.

Model mapping (SPOT task/subtask  ->  EDITORS):
  edge nodes (trust candidates) <- the MEC servers (mn.mecN)
  trust metrics (E, L, U)       <- static per-node profile (paper Table 4)
  comp time on node             <- subtask.comp / capacity_map[node][-1]
                                   (work in cycles / node rate in cycles/s)
  offloaded input data          <- subtask.memory [KB]   (iperf'd host->MEC,
                                   charged as a serialized upload)
  intermediate result           <- subtask.result [KB]   (iperf'd MEC->MEC)
  final result return           <- last subtask.result [KB] (iperf'd MEC->host)
"""

from __future__ import annotations

import os
import sys
import threading
from typing import Callable, Dict, List, Optional

import networkx as nx

# Reuse the offline EDITORS trust module (Algorithm 1) without duplication.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_EDITORS_DIR = os.path.join(_REPO_ROOT, "editors")
if _EDITORS_DIR not in sys.path:
    sys.path.insert(0, _EDITORS_DIR)
from trust import compute_trust  # noqa: E402

# Nominal host-link bandwidth (bits/s), matching topology.py (host links = 200
# Mbps).  Used *only* by the decision's latency estimate; realised transfer
# times come from iperf.
NOMINAL_BW = 200e6

# Static trust profiles (energy J, latency ms, memory MB) from the paper's
# Table 4, assigned to MEC servers in order (cycled if there are more servers).
NODE_PROFILES = [
    (7040, 80, 50),
    (7040, 100, 60),
    (11000, 120, 55),
    (7040, 90, 70),
    (11000, 110, 65),
    (7040, 200, 200),
]

lock = threading.Lock()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _proc_rate(node: str, server_map: dict, host_map: dict) -> float:
    m = server_map if node in server_map else host_map
    return float(m[node][-1])


def build_node_metrics(mec_servers: List[str]) -> Dict[str, dict]:
    """Map each MEC server to its (energy, latency, memory) trust profile."""
    metrics = {}
    for i, mec in enumerate(sorted(mec_servers)):
        energy, latency, memory = NODE_PROFILES[i % len(NODE_PROFILES)]
        metrics[mec] = {"energy": energy, "latency": latency, "memory": memory}
    return metrics


def _norm(values: List[float]) -> List[float]:
    lo, hi = min(values), max(values)
    if hi == lo:
        return [0.0 for _ in values]
    return [(v - lo) / (hi - lo) for v in values]


# ---------------------------------------------------------------------------
# EDITORS decision for one subtask (Algorithm 3 policy: energy-aware, trusted)
# ---------------------------------------------------------------------------
def _select_node(subtask, candidates, node_energy_coeff, trust, topo_graph,
                 server_map, host_map, w_e, w_l, w_ts) -> str:
    """Pick the trusted, feasible MEC minimising a normalised energy+latency
    cost while preferring higher trust (the paper's objective; its literal
    Q = w_e*E + w_l*L + w_ts*TS is sign-inconsistent, so we minimise a cost in
    which every term points the same way)."""
    energies, latencies = [], []
    for n in candidates:
        energies.append(node_energy_coeff[n] * subtask.comp)
        try:
            hops = len(nx.shortest_path(topo_graph, subtask.host, n)) - 1
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            hops = 1
        trans = hops * (subtask.memory * 8_000.0) / NOMINAL_BW
        # work (cycles) / rate, as in _comp_time
        comp = subtask.comp / _proc_rate(n, server_map, host_map)
        latencies.append(trans + comp)

    ne, nl = _norm(energies), _norm(latencies)
    best, best_score = candidates[0], float("inf")
    for i, n in enumerate(candidates):
        score = w_e * ne[i] + w_l * nl[i] + w_ts * (1.0 - trust[n])
        if score < best_score:
            best, best_score = n, score
    return best


def assign_subtasks(task, trusted, node_energy_coeff, trust, topo_graph,
                    server_map, host_map, w_e, w_l, w_ts) -> Optional[dict]:
    """Assign every subtask to a trusted MEC (Algo 2), falling back to the
    origin host if no trusted node fits.  Returns {subtask_id: node} or None
    (rolling back reservations) if some subtask cannot be placed anywhere.
    """
    placement: Dict[str, str] = {}
    reserved = []  # (target_map, node, subtask_id, memory)

    with lock:
        for subtask in task.subtasks:
            candidates = [
                n for n in trusted
                if server_map[n][0] >= subtask.memory
            ]
            if candidates:
                node = _select_node(
                    subtask, candidates, node_energy_coeff, trust, topo_graph,
                    server_map, host_map, w_e, w_l, w_ts)
                tmap = server_map
            elif host_map[subtask.host][0] >= subtask.memory:
                node = subtask.host
                tmap = host_map
            else:
                for m, n, sid, mem in reserved:  # rollback
                    m[n][0] += mem
                    m[n][1].remove(sid)
                return None

            tmap[node][0] -= subtask.memory
            tmap[node][1].append(subtask.subtask_id)
            reserved.append((tmap, node, subtask.subtask_id, subtask.memory))
            placement[subtask.subtask_id] = node
    return placement


def _release(placement, server_map, host_map, task):
    with lock:
        for subtask in task.subtasks:
            node = placement.get(subtask.subtask_id)
            if node is None:
                continue
            tmap = server_map if node in server_map else host_map
            tmap[node][0] += subtask.memory
            if subtask.subtask_id in tmap[node][1]:
                tmap[node][1].remove(subtask.subtask_id)


# ---------------------------------------------------------------------------
# critical-path completion time (same semantics as network_graph.task_exec_times)
# ---------------------------------------------------------------------------
def _comp_time(subtask, node, server_map, host_map) -> float:
    # work (cycles) / node rate (cycles/s)
    return subtask.comp / _proc_rate(node, server_map, host_map)


def _critical_path(task, placement, edge_map, server_map, host_map) -> float:
    # Uploads serialize on the host's single physical uplink, in subtask
    # order (locally-placed subtasks store 0, so they don't advance the
    # clock), as in network_graph.task_exec_times.
    upload_done, t_up = {}, 0.0
    for st in task.subtasks:
        t_up += float(edge_map.get((st.host, st.subtask_id), 0.0) or 0.0)
        upload_done[st.subtask_id] = t_up

    # One subtask at a time per node (single CPU per container): list-
    # schedule in task.subtasks order (topological, deterministic tie-break),
    # as in network_graph.task_exec_times.
    node_free, finish = {}, {}
    for st in task.subtasks:
        node = placement[st.subtask_id]
        ready = upload_done[st.subtask_id]
        for pred_id in st.predecessors:
            ready = max(ready, finish[pred_id]
                        + float(edge_map.get((pred_id, st.subtask_id), 0.0) or 0.0))
        start = max(ready, node_free.get(node, 0.0))
        finish[st.subtask_id] = start + _comp_time(st, node, server_map, host_map)
        node_free[node] = finish[st.subtask_id]

    # result return: the final subtask's result back to the origin host
    total = max(finish.values())
    total += float(edge_map.get(
        (task.subtasks[-1].subtask_id, task.host), 0.0) or 0.0)
    return total


# ---------------------------------------------------------------------------
# full strategy: decide + realise (measure) every task
# ---------------------------------------------------------------------------
def editors_thread(tasks_lists, server_map, host_map, docker_mapping, topo_graph,
                   discarded_task_set, mec_servers, measure_fn: Callable,
                   weights=None, threshold=None, trust_weight: float = 0.3,
                   verbose: bool = True) -> Dict[str, float]:
    """Run EDITORS over all tasks, measuring transfers with `measure_fn`.

    `measure_fn(src, dst, sizeK, docker_mapping, id)` returns transfer seconds
    (network_graph.docker_send_to in production).  Returns {task_id: completion}.
    """
    node_metrics = build_node_metrics(mec_servers)
    trust, trusted, info = compute_trust(node_metrics, weights, threshold)
    w_e, w_l, _w_u = info["weights"]
    node_energy_coeff = {n: node_metrics[n]["energy"] / 10_000.0 for n in node_metrics}

    if verbose:
        print(f"EDITORS trust weights (E,L,U) = {info['weights']}")
        print(f"EDITORS trust threshold theta = {info['threshold']}")
        print(f"EDITORS trust scores = {trust}")
        print(f"EDITORS trusted MEC nodes = {sorted(trusted)}\n")

    task_time: Dict[str, float] = {}

    for task in tasks_lists:
        placement = assign_subtasks(
            task, trusted, node_energy_coeff, trust, topo_graph,
            server_map, host_map, w_e, w_l, trust_weight)
        if placement is None:
            discarded_task_set.add(task.task_id)          # nothing fit anywhere
            continue

        # ---- realise the schedule with iperf ------------------------------
        edge_map: Dict[tuple, float] = {}
        failed = False
        for subtask in task.subtasks:
            node = placement[subtask.subtask_id]

            # input data host -> MEC, charged as a serialized upload in the
            # critical path (same keying as network_graph's
            # partial_offloading_thread)
            if node != subtask.host:
                t = measure_fn(subtask.host, node, f"{subtask.memory}K",
                               docker_mapping, subtask.subtask_id)
                if t == -1 or t is None:
                    failed = True
                    break
                edge_map[(subtask.host, subtask.subtask_id)] = float(t)
            else:
                edge_map[(subtask.host, subtask.subtask_id)] = 0.0

            # intermediate results to successors (MEC -> MEC)
            for succ in subtask.successors:
                succ_node = placement[succ]
                if node == succ_node:
                    edge_map[(subtask.subtask_id, succ)] = 0.0
                else:
                    t = measure_fn(node, succ_node, f"{subtask.result}K",
                                   docker_mapping, subtask.subtask_id)
                    if t == -1 or t is None:
                        failed = True
                        break
                    edge_map[(subtask.subtask_id, succ)] = float(t)
            if failed:
                break

        if failed:
            discarded_task_set.add(task.task_id)
            _release(placement, server_map, host_map, task)
            continue

        # result return: final subtask's result back to the origin host
        # (0 if the final subtask ran on the host)
        last = task.subtasks[-1]
        last_node = placement[last.subtask_id]
        if last_node != task.host:
            t = measure_fn(last_node, task.host, f"{last.result}K",
                           docker_mapping, last.subtask_id)
            if t == -1 or t is None:
                discarded_task_set.add(task.task_id)
                _release(placement, server_map, host_map, task)
                continue
            edge_map[(last.subtask_id, task.host)] = float(t)
        else:
            edge_map[(last.subtask_id, task.host)] = 0.0

        task_time[task.task_id] = _critical_path(
            task, placement, edge_map, server_map, host_map)
        _release(placement, server_map, host_map, task)

    return task_time


# ---------------------------------------------------------------------------
# self-test: exercise decision + critical path WITHOUT Docker/Mininet
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    class _S:
        def __init__(self, sid, mem, comp, result, host):
            self.subtask_id, self.memory, self.comp = sid, mem, comp
            self.result, self.host = result, host
            self.predecessors, self.successors = [], []

    class _T:
        def __init__(self, tid, host, subtasks):
            self.task_id, self.host, self.subtasks = tid, host, subtasks

    G = nx.Graph()
    G.add_edges_from([
        ("mn.host1", "s1"), ("mn.host2", "s2"),
        ("s1", "s2"), ("s1", "s3"), ("s2", "s4"), ("s3", "s4"),
        ("mn.mec1", "s1"), ("mn.mec2", "s2"), ("mn.mec3", "s3"), ("mn.mec4", "s4"),
    ])
    server_map = {f"mn.mec{i}": [16 * 1024 ** 2, [], 1_000_000]
                  for i in range(1, 5)}
    host_map = {f"mn.host{i}": [1024, [], 1000] for i in range(1, 3)}
    mecs = list(server_map)

    def _mk(tid, host, n):
        subs = [_S(f"{tid}_subtask_{i+1}", 30 + 10 * i, 2000, 15, host) for i in range(n)]
        for i in range(n - 1):                       # simple chain DAG
            subs[i].successors.append(subs[i + 1].subtask_id)
            subs[i + 1].predecessors.append(subs[i].subtask_id)
        return _T(tid, host, subs)

    tasks = [_mk("task_1", "mn.host1", 3), _mk("task_2", "mn.host2", 4),
             _mk("task_3", "mn.host1", 2)]

    def fake_measure(src, dst, sizeK, mapping, sid):
        return float(sizeK.rstrip("K")) * 8_000.0 / NOMINAL_BW

    discarded = set()
    times = editors_thread(tasks, server_map, host_map, {}, G, discarded,
                           mecs, fake_measure)
    print("EDITORS decisions realised (self-test, no Docker):")
    for tid in sorted(times):
        print(f"  {tid}: completion = {times[tid]*1e3:.4f} ms")
    print(f"  discarded: {sorted(discarded)}")
