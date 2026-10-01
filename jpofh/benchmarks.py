"""Benchmark solutions used for comparison in Section 5.2 of the paper.

  * LE   - Local Execution: every task runs on its origin device (Eq. 33, 34).
  * RE   - Remote Execution: every task is fully offloaded (x = 1) to the best
           remote device, flows scheduled greedily in priority order.
  * SOFS - Separate Offloading and Flow Scheduling: the offloading ratio is
           computed *without* regard to waiting time or flow start (Eq. 35),
           and tasks are processed first-come-first-serve (by release time).

These reuse the network / task models and the shared-link flow rule (Eq. 23)
from `models.py` / `jpofh.py`.  SOFS is implemented in a simplified form (greedy
per-task selection instead of the full PDQ/EDF flow scheduler); this is noted in
the README.
"""

from __future__ import annotations

from typing import Dict, List

import numpy as np

from models import NetworkModel, Task
from jpofh import Schedule, TaskResult, _Flow, _flow_start, priority_order


def local_execution(net: NetworkModel, tasks: List[Task]) -> Schedule:
    """LE (Eq. 33, 34): run every task on its origin device."""
    Tbusy = np.zeros(net.num_devices)
    results: Dict[int, TaskResult] = {}
    order = priority_order(tasks, net.avg_ps)
    for i in order:
        task = tasks[i]
        zi = task.z
        Tft = task.CL / net.PS[zi] + max(Tbusy[zi], task.Trel)   # Eq. 33
        Tbusy[zi] = Tft                                          # Eq. 34
        results[i] = TaskResult(task_id=i, local_device=zi, remote_device=-1,
                                path_index=0, x=0.0, S=task.Trel, Tft=Tft,
                                flow_finish=task.Trel)
    return Schedule(results=results, order=order)


def remote_execution(net: NetworkModel, tasks: List[Task]) -> Schedule:
    """RE: full offload (x = 1) to the remote device with earliest finish time.

    Flow scheduling follows the same priority order and shared-link rule as
    JPOFH; only the offloading ratio differs (fixed to 1).
    """
    M, K = net.num_devices, net.num_paths
    Tbusy = np.zeros(M)
    flows: List[_Flow] = []
    results: Dict[int, TaskResult] = {}
    order = priority_order(tasks, net.avg_ps)

    for i in order:
        task = tasks[i]
        zi = task.z
        best = None  # (T_off, j, k, S, flow_finish)
        for j in range(M):
            if j == zi:
                continue
            CT_off = task.CL / net.PS[j]
            for k in range(K):
                if not net.W(zi, j, k):
                    continue
                path = net.get_path(zi, j, k)
                trans = path.H * task.D / path.R          # x = 1
                S = _flow_start(task.Trel, path.edges, flows)
                T_off = CT_off + max(Tbusy[j], task.Trel, S + trans)
                if best is None or T_off < best[0]:
                    best = (T_off, j, k, S, S + trans)
        if best is None:
            # isolated origin (no path): fall back to local execution
            Tft = task.CL / net.PS[zi] + max(Tbusy[zi], task.Trel)
            Tbusy[zi] = Tft
            results[i] = TaskResult(i, zi, -1, 0, 0.0, task.Trel, Tft, task.Trel)
            continue
        Tft, j_star, k_star, S_star, flow_finish = best
        Tbusy[j_star] = Tft
        flows.append(_Flow(edges=net.get_path(zi, j_star, k_star).edges,
                           finish=flow_finish))
        results[i] = TaskResult(task_id=i, local_device=zi, remote_device=j_star,
                                path_index=k_star, x=1.0, S=S_star, Tft=Tft,
                                flow_finish=flow_finish)
    return Schedule(results=results, order=order)


def sofs(net: NetworkModel, tasks: List[Task]) -> Schedule:
    """SOFS: separate offloading (Eq. 35) and flow scheduling, FCFS order.

    The offloading ratio ignores waiting time and flow start time:
        x_ijk = CT_izi / (CT_izi + CT_ij + H*D/R)          (Eq. 35)
    Tasks are handled first-come-first-serve (increasing release time).  The two
    decisions are *separated*: the device/path is chosen without any knowledge of
    flow conflicts (the flow is optimistically assumed to start at Trel_i); only
    afterwards is the flow start time realised against the shared-link rule.  The
    conflict delay that this decoupling misses is what makes SOFS's realised
    completion time worse than JPOFH's joint decision.
    """
    M, K = net.num_devices, net.num_paths
    Tbusy = np.zeros(M)
    flows: List[_Flow] = []
    results: Dict[int, TaskResult] = {}
    order = sorted((t.id for t in tasks), key=lambda i: tasks[i].Trel)  # FCFS

    for i in order:
        task = tasks[i]
        zi = task.z
        CT_local = task.CL / net.PS[zi]
        # ---- offloading decision (flow-scheduling-agnostic) --------------
        best = None  # (completion_estimate, j, k, x)
        for j in range(M):
            if j == zi:
                continue
            CT_off = task.CL / net.PS[j]
            for k in range(K):
                if not net.W(zi, j, k):
                    continue
                path = net.get_path(zi, j, k)
                trans_full = path.H * task.D / path.R
                # Eq. 35: ratio decoupled from waiting / flow start
                x = CT_local / (CT_local + CT_off + trans_full)
                # Optimistic estimate: flow assumed to start at Trel (no conflict)
                T_loc = (1.0 - x) * CT_local + max(Tbusy[zi], task.Trel)
                T_off = x * CT_off + max(Tbusy[j], task.Trel,
                                         task.Trel + x * trans_full)
                completion = max(T_loc, T_off)
                if best is None or completion < best[0]:
                    best = (completion, j, k, x)
        # ---- flow scheduling (realised separately) -----------------------
        _, j_star, k_star, x_star = best
        path = net.get_path(zi, j_star, k_star)
        trans_full = path.H * task.D / path.R
        S_star = _flow_start(task.Trel, path.edges, flows)   # real conflict delay
        flow_finish = S_star + x_star * trans_full
        T_loc = (1.0 - x_star) * CT_local + max(Tbusy[zi], task.Trel)
        T_off = x_star * (task.CL / net.PS[j_star]) + max(
            Tbusy[j_star], task.Trel, flow_finish)
        Tft = max(T_loc, T_off)
        Tbusy[zi] = Tft
        Tbusy[j_star] = Tft
        flows.append(_Flow(edges=net.get_path(zi, j_star, k_star).edges,
                           finish=flow_finish))
        results[i] = TaskResult(task_id=i, local_device=zi, remote_device=j_star,
                                path_index=k_star, x=x_star, S=S_star, Tft=Tft,
                                flow_finish=flow_finish)
    return Schedule(results=results, order=order)
