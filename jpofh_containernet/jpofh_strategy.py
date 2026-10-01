"""JPOFH offloading strategy, ported to the SPOT Containernet harness.

This runs the JPOFH heuristic (Sahni et al., IEEE TPDS 2021 - see
../jpofh/ for the reference pure-simulation implementation) as *another
strategy* inside the same emulation used by SPOT, so it can be compared to
POB / POR / BO / LE on the identical topology, workload (tasks.json), Docker
containers and iperf-based measurement.

Design (why this is a fair comparison):
  * DECISION  - made analytically with JPOFH's own equations (rank metric,
                partial-offloading ratio x, flow start time S).  This is what
                JPOFH "is".
  * MEASUREMENT - the resulting schedule is realised in the *same* way SPOT
                realises its schedules: the offloaded bytes are actually sent
                host -> MEC with `iperf` (docker_send_to), and computation time
                is work (cycles) / node rate (cycles/s), exactly as SPOT
                computes it.
  Only the decision differs between JPOFH and SPOT; the measurement pipeline,
  capacity maps and discard rules are shared.

Model mapping (SPOT task  ->  JPOFH divisible job):
  CL_i  (computation load) <- total comp [cycles] = sum(subtask.comp)
                              (comp time = CL / PS)
  D_i   (input data)       <- task.memory  [KB]  (bytes actually iperf'd)
  PS_j  (processing speed) <- capacity_map[node][-1]  (node rate, cycles/s)
  z_i   (origin device)    <- task.host  (an IoT host)
  Trel_i(release time)     <- sequential service: each task is released
                                  only after the previous one completes, so
                                  max(Tbusy, Trel) = Trel, the waiting terms
                                  cancel out of Eqs. 19-22, and Eq. 23's flow
                                  constraint never binds across tasks.
                                  Implemented as fresh Tbusy/flows per task
                                  with trel = 0. Rank (Eq. 24) still orders
                                  the tasks by CL / PS-bar as in Algorithm 1,
                                  although without cross-task coupling the
                                  order does not change results.
  remote devices           <- the MEC servers (vertical host -> MEC offloading)

JPOFH splits a task by a ratio x: (1-x) stays on the origin host, x is sent to
one MEC server.  The paper itself does not send the offloaded result back
(independent tasks, no aggregation); this port deviates and DOES measure a
MEC -> host return transfer of the final subtask's `result` KB, so that every
strategy in the comparison charges the same three transfer legs (upload,
inter-subtask edges, result return).

Sensitivity: JPOFH is evaluated with sequential releases, consistent with its
own paper's staggered-release evaluation (mean release spacing of about one
task service time). Under batch release (trel = 0 for all tasks with
persistent Tbusy/flows) its completion times are roughly 4-5x worse.
"""

from __future__ import annotations

import threading
from typing import Callable, Dict, List, Tuple

import networkx as nx

# Nominal per-path bandwidth (bits/s) used *only* by JPOFH's decision to
# estimate transfer time H*D/R.  The realised transfer time comes from iperf.
NOMINAL_BW = 200e6          # ~ the host-link bw in topology.py (200 Mbps)

lock = threading.Lock()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _processing_speed(node: str, server_map: dict, host_map: dict) -> float:
    m = server_map if node in server_map else host_map
    return float(m[node][-1])


def _avg_ps(server_map: dict, host_map: dict) -> float:
    vals = [v[-1] for v in server_map.values()] + [v[-1] for v in host_map.values()]
    return float(sum(vals) / len(vals))


def _total_comp(task) -> float:
    """CL_i: the task's computation load in cycles, summed over subtasks
    (equal to task.comp)."""
    return float(sum(st.comp for st in task.subtasks))


def _path_edges(path_nodes: List[str]) -> frozenset:
    """Undirected link set of a routing path (list of node names/DPIDs)."""
    return frozenset(
        frozenset((path_nodes[i], path_nodes[i + 1]))
        for i in range(len(path_nodes) - 1)
    )


def rank_metric(task, avg_ps: float, trel: float = 0.0) -> float:
    """Eq. 24:  rank_i = Trel_i + CL_i / PS-bar   (CL_i = total comp)."""
    return trel + _total_comp(task) / avg_ps


def _flow_start(trel: float, cand_edges: frozenset,
                flows: List[Tuple[frozenset, float]]) -> float:
    """Eq. 23: flow can start after Trel and after any earlier flow that shares
    a link on the routing path has finished (no two flows on a link at once)."""
    S = trel
    for edges, finish in flows:
        if not cand_edges.isdisjoint(edges) and finish > S:
            S = finish
    return S


# ---------------------------------------------------------------------------
# JPOFH decision for a single task  (inner body of Algorithm 1)
# ---------------------------------------------------------------------------
def _decide_task(task, first_task, topo_graph, server_map, host_map,
                 mec_servers, Tbusy, flows, avg_ps):
    """Return (remote_mec or None, path_nodes, x, S) for one task.

    Chooses the MEC device / offloading ratio that minimises T_ijk_loc (Eq. 19),
    where x comes from Eq. 22 and S from Eq. 23.  Honours the capacity maps: the
    offloaded part must fit on the MEC and the local part on the host, otherwise
    the candidate (or the whole task) falls back to local execution.
    """
    zi = task.host
    trel = 0.0
    CL = _total_comp(task)                    # work in cycles
    D_bits = task.memory * 8_000.0            # KB -> bits, for the H*D/R estimate
    CT_local = CL / _processing_speed(zi, server_map, host_map)

    best = None  # (T_loc, mec, path_nodes, x, S)

    if not first_task:
        for mec in mec_servers:
            if mec not in server_map:
                continue
            PS_j = _processing_speed(mec, server_map, host_map)
            CT_off = CL / PS_j
            try:
                path_nodes = nx.shortest_path(topo_graph, zi, mec)
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                continue
            H = len(path_nodes) - 1
            trans_full = H * D_bits / NOMINAL_BW          # transfer at x = 1
            edges = _path_edges(path_nodes)

            S = _flow_start(trel, edges, flows)           # Eq. 23
            denom = CT_local + CT_off + trans_full
            x = (CT_local + max(Tbusy[zi], trel)
                 - max(Tbusy[mec], trel, S)) / denom       # Eq. 22
            x = min(1.0, max(0.0, x))

            # capacity feasibility (shared with SPOT's reservation semantics)
            if x > 0 and server_map[mec][0] < x * task.memory:
                continue
            if host_map[zi][0] < (1 - x) * task.memory:
                continue

            T_loc = (1 - x) * CT_local + max(Tbusy[zi], trel)   # Eq. 19
            if best is None or T_loc < best[0]:
                best = (T_loc, mec, path_nodes, x, S)

    if best is None:
        # first task, or no feasible offload -> pure local execution (x = 0)
        return None, [zi], 0.0, 0.0
    _, mec, path_nodes, x, S = best
    if x <= 0.0:
        return None, [zi], 0.0, S
    return mec, path_nodes, x, S


# ---------------------------------------------------------------------------
# full strategy: decide + realise (measure) every task
# ---------------------------------------------------------------------------
def jpofh_thread(tasks_lists, server_map, host_map, docker_mapping, topo_graph,
                 discarded_task_set, mec_servers,
                 measure_fn: Callable) -> Dict[str, float]:
    """Run JPOFH over all tasks in rank order, measuring each with `measure_fn`.

    `measure_fn(src, dst, sizeK, docker_mapping, id)` must return the transfer
    time in seconds (this is network_graph.docker_send_to in production).

    Returns {task_id: completion_time}.  Tasks that cannot be placed are added
    to `discarded_task_set` (same convention as SPOT).
    """
    avg_ps = _avg_ps(server_map, host_map)
    order = sorted(tasks_lists, key=lambda t: rank_metric(t, avg_ps))

    task_time: Dict[str, float] = {}

    for idx, task in enumerate(order):
        # Sequential releases: each task is released after the previous one
        # completes, so busy clocks and scheduled flows never carry across
        # tasks. Fresh state per task is equivalent.
        Tbusy: Dict[str, float] = {n: 0.0 for n in list(server_map) + list(host_map)}
        flows: List[Tuple[frozenset, float]] = []
        zi = task.host
        first_task = (idx == 0)

        mec, path_nodes, x, S = _decide_task(
            task, first_task, topo_graph, server_map, host_map,
            mec_servers, Tbusy, flows, avg_ps)

        # ---- discard if even the local placement does not fit --------------
        if mec is None and host_map[zi][0] < task.memory:
            discarded_task_set.add(task.task_id)
            continue

        host_ps = _processing_speed(zi, server_map, host_map)
        local_comp = (1.0 - x) * _total_comp(task) / host_ps

        if mec is None:
            # pure local execution
            with lock:
                host_map[zi][1].append(task.task_id)
            Tft = local_comp + max(Tbusy[zi], 0.0)
            Tbusy[zi] = Tft
            task_time[task.task_id] = float(Tft)
            with lock:
                host_map[zi][1].remove(task.task_id)
            continue

        # ---- reserve capacity for the offloaded / local components ---------
        with lock:
            server_map[mec][0] -= x * task.memory
            server_map[mec][1].append(task.task_id)

        # ---- MEASURE the input-data flow with iperf (host -> MEC) ----------
        offload_kb = max(1, int(round(x * task.memory)))
        transfer = measure_fn(zi, mec, f"{offload_kb}K", docker_mapping, task.task_id)
        if transfer == -1 or transfer is None:
            discarded_task_set.add(task.task_id)
            with lock:
                server_map[mec][0] += x * task.memory
                server_map[mec][1].remove(task.task_id)
            continue
        transfer = float(transfer)

        mec_ps = _processing_speed(mec, server_map, host_map)
        remote_comp = x * _total_comp(task) / mec_ps

        # ---- MEASURE the result-return flow (MEC -> host) -------------------
        # Deviation from Sahni et al. (no-aggregation assumption): in this
        # workload the host needs the final result, so every offloading
        # strategy charges the same return leg.
        return_kb = max(1, int(round(task.subtasks[-1].result)))
        return_transfer = measure_fn(mec, zi, f"{return_kb}K", docker_mapping,
                                     task.task_id)
        if return_transfer == -1 or return_transfer is None:
            discarded_task_set.add(task.task_id)
            with lock:
                server_map[mec][0] += x * task.memory
                server_map[mec][1].remove(task.task_id)
            continue
        return_transfer = float(return_transfer)

        # completion = max(local branch, offloaded branch)  (Eq. 18 / 20)
        wait = max(Tbusy[zi], Tbusy[mec], S)
        local_branch = local_comp + max(Tbusy[zi], 0.0)
        offload_branch = wait + transfer + remote_comp + return_transfer
        Tft = max(local_branch, offload_branch)

        task_time[task.task_id] = float(Tft)
        Tbusy[zi] = Tft
        Tbusy[mec] = Tft

        # record the flow for later tasks' Eq. 23 (estimate used by the decision)
        H = len(path_nodes) - 1
        trans_est = H * (task.memory * 8_000.0) / NOMINAL_BW * x
        flows.append((_path_edges(path_nodes), S + trans_est))

        # ---- release capacity (as SPOT does after measuring) ---------------
        with lock:
            server_map[mec][0] += x * task.memory
            server_map[mec][1].remove(task.task_id)

    return task_time


# ---------------------------------------------------------------------------
# self-test: exercise the decision/measurement logic WITHOUT Docker/Mininet
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # A tiny fake topology + maps so the logic can be validated on any machine.
    class _St:
        def __init__(self, comp, result):
            self.comp = comp
            self.result = result

    class _T:
        def __init__(self, tid, mem, comp, host):
            self.task_id, self.memory, self.comp, self.host = tid, mem, comp, host
            # two fake subtasks whose work sums to comp, so _total_comp works;
            # the last one carries the result size the return leg charges
            self.subtasks = [_St(comp // 2, mem // 4),
                             _St(comp - comp // 2, mem // 4)]

    G = nx.Graph()
    # hosts -- switches -- mecs
    G.add_edges_from([
        ("mn.host1", "s1"), ("mn.host2", "s2"),
        ("s1", "s2"), ("s1", "s3"), ("s2", "s4"), ("s3", "s4"),
        ("mn.mec1", "s1"), ("mn.mec2", "s2"), ("mn.mec3", "s3"), ("mn.mec4", "s4"),
    ])
    server_map = {f"mn.mec{i}": [16 * 1024 ** 2, [], 1_000_000]
                  for i in range(1, 5)}
    host_map = {f"mn.host{i}": [1024, [], 1000] for i in range(1, 3)}
    mecs = list(server_map)

    tasks = [
        _T("task_1", 150, 4000, "mn.host1"),
        _T("task_2", 200, 6000, "mn.host2"),
        _T("task_3", 100, 3000, "mn.host1"),
        _T("task_4", 250, 8000, "mn.host2"),
    ]

    # Fake measurement: transfer time proportional to size, no real network.
    def fake_measure(src, dst, sizeK, mapping, tid):
        kb = float(sizeK.rstrip("K"))
        return kb * 8_000.0 / NOMINAL_BW      # deterministic H=... ignored

    discarded = set()
    times = jpofh_thread(tasks, server_map, host_map, {}, G, discarded,
                         mecs, fake_measure)
    print("JPOFH decisions realised (self-test, no Docker):")
    for tid in sorted(times):
        print(f"  {tid}: completion = {times[tid]*1e3:.4f} ms")
    print(f"  discarded: {sorted(discarded)}")
    print(f"  avg completion = {sum(times.values())/len(times)*1e3:.4f} ms")

    # Hand-derived expectations with sequential releases (fresh Tbusy/flows
    # per task, so S = 0 and every wait term is 0). Exact values:
    #   task_3: first in rank order -> forced local, 3000/1000 s
    #   task_1: mec1 (H=2), x = 4/4.016, Tft = local branch = 0.064/4.016 s
    #   task_2: mec2 (H=2), x = 6/6.022, Tft = local branch = 0.132/6.022 s
    #   task_4: mec2 (H=2), x = 8/8.028, Tft = local branch = 0.224/8.028 s
    expected_s = {
        "task_1": 0.064 / 4.016,
        "task_2": 0.132 / 6.022,
        "task_3": 3000.0 / 1000.0,
        "task_4": 0.224 / 8.028,
    }
    assert not discarded, discarded
    assert set(times) == set(expected_s), (sorted(times), sorted(expected_s))
    for tid, exp in expected_s.items():
        assert abs(times[tid] - exp) < 1e-9, (tid, times[tid], exp)
    print("self-test assertions OK")
