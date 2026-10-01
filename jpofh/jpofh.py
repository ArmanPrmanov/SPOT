"""Joint Partial Offloading and Flow scheduling Heuristic (JPOFH).

Faithful implementation of Algorithm 1 and Equations (18)-(24) of

    Y. Sahni, J. Cao, L. Yang and Y. Ji, "Multi-Hop Multi-Task Partial
    Computation Offloading in Collaborative Edge Computing," IEEE TPDS,
    vol. 32, no. 5, pp. 1133-1145, May 2021.

The heuristic schedules N independent tasks generated at different devices of a
multi-hop edge network.  Each task is partitioned into a local component (kept
on its origin device z_i) and an offloaded component (sent to a remote device j
over one of K routing paths).  The offloading ratio x_ijk is chosen to balance
the local finish time against the offloaded finish time, while the start time of
each input-data flow S_ijk respects the "no two flows share a link at the same
time" constraint.

Notation (Table 2 of the paper):
    CT_ij   computation time of task i on device j   = CL_i / PS_j
    Tbusy_j waiting time (device free-at time) of device j
    H,R     hops and bandwidth of the chosen routing path
    S_ijk   start time of the input-data flow (Eq. 23)
    x_ijk   partial offloading ratio (Eq. 22)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, FrozenSet, List, Optional, Tuple

import numpy as np

from models import NetworkModel, Task


@dataclass
class TaskResult:
    """Per-task schedule produced by JPOFH (the "return" of Algorithm 1)."""
    task_id: int
    local_device: int          # z_i
    remote_device: int         # j*_i  (-1 if executed purely locally)
    path_index: int            # k*_i
    x: float                   # x_{i,j*,k*}  partial offloading ratio
    S: float                   # S_{i,j*,k*}  input-data flow start time
    Tft: float                 # total finish time of the task
    flow_finish: float         # time the input-data flow clears the network


@dataclass
class _Flow:
    """A scheduled input-data flow, used to enforce the shared-link constraint."""
    edges: FrozenSet[FrozenSet[int]]
    finish: float


@dataclass
class Schedule:
    results: Dict[int, TaskResult]
    order: List[int]           # task ids in priority (rank) order

    @property
    def avg_completion_time(self) -> float:
        """Objective (Eq. 17): average completion time (1/N) * sum Tft_i."""
        return float(np.mean([r.Tft for r in self.results.values()]))


# ---------------------------------------------------------------------------
# Equation (24): rank metric and priority list
# ---------------------------------------------------------------------------
def rank_metric(task: Task, avg_ps: float) -> float:
    """rank_i = Trel_i + CL_i / PS-bar   (Eq. 24)."""
    return task.Trel + task.CL / avg_ps


def priority_order(tasks: List[Task], avg_ps: float) -> List[int]:
    """Index I of tasks in *increasing* order of rank metric (line 2)."""
    return sorted((t.id for t in tasks),
                  key=lambda i: rank_metric(tasks[i], avg_ps))


# ---------------------------------------------------------------------------
# Equation (23): start time of the input-data flow
# ---------------------------------------------------------------------------
def _flow_start(trel_i: float, candidate_edges: FrozenSet[FrozenSet[int]],
                flows: List[_Flow]) -> float:
    """S_ijk (Eq. 23).

    The flow can start no earlier than the task's release time; and if it shares
    any link with a previously scheduled flow it must wait for that flow to
    finish (no two flows on a link at the same time).
    """
    S = trel_i
    for f in flows:
        if not candidate_edges.isdisjoint(f.edges) and f.finish > S:
            S = f.finish
    return S


# ---------------------------------------------------------------------------
# Algorithm 1
# ---------------------------------------------------------------------------
def jpofh(net: NetworkModel, tasks: List[Task]) -> Schedule:
    M = net.num_devices
    K = net.num_paths
    avg_ps = net.avg_ps

    Tbusy = np.zeros(M)                       # waiting time at each device
    flows: List[_Flow] = []                   # scheduled input-data flows
    results: Dict[int, TaskResult] = {}

    order = priority_order(tasks, avg_ps)     # line 2

    for t, i in enumerate(order):             # line 3 (t is 0-based here)
        task = tasks[i]
        zi = task.z
        CT_local = task.CL / net.PS[zi]       # CT_{i, z_i}
        first_task = (t == 0)                 # "t >= 2" in the paper (1-based)

        # best candidate so far: (T_loc, remote_j, k, x, S, flow_finish)
        best: Optional[Tuple[float, int, int, float, float, float]] = None

        for j in range(M):                    # line 5
            if j == zi:
                continue                      # local device is not a remote target
            CT_off = task.CL / net.PS[j]      # CT_{i, j}
            for k in range(K):                # line 6
                if not net.W(zi, j, k):       # line 7: W_ijk != 0
                    continue
                path = net.get_path(zi, j, k)
                H, R = path.H, path.R
                trans_full = H * task.D / R    # transfer time at x = 1

                if not first_task:            # line 8: t >= 2
                    # Eq. 23
                    S = _flow_start(task.Trel, path.edges, flows)
                    # Eq. 22 (offloading ratio that balances Eq. 19 and Eq. 21)
                    denom = CT_local + CT_off + trans_full
                    x = (CT_local + max(Tbusy[zi], task.Trel)
                         - max(Tbusy[j], task.Trel, S)) / denom
                    x = min(1.0, max(0.0, x))          # clamp to [0, 1]
                    # Eq. 19 and Eq. 20
                    T_loc = (1.0 - x) * CT_local + max(Tbusy[zi], task.Trel)
                    T_off = x * CT_off + max(Tbusy[j], task.Trel, S + x * trans_full)
                else:                          # lines 12-15: first task -> local
                    S = task.Trel
                    x = 0.0
                    T_loc = CT_local + max(Tbusy[zi], task.Trel)
                    T_off = float("inf")

                # line 18: selection is by min T_ijk_loc
                if best is None or T_loc < best[0]:
                    flow_finish = S + x * trans_full
                    best = (T_loc, j, k, x, S, flow_finish)

        # ---- select device / path (lines 18-22) --------------------------
        T_loc, j_star, k_star, x_star, S_star, flow_finish = best
        Tft = T_loc                            # line 19: Tft_i = min T_loc
        Tbusy[zi] = Tft                         # line 20
        if x_star > 0.0:
            Tbusy[j_star] = Tft                 # line 21 (offloaded device busy)
            flows.append(_Flow(edges=net.get_path(zi, j_star, k_star).edges,
                               finish=flow_finish))
            remote = j_star
        else:
            remote = -1                         # purely local execution

        results[i] = TaskResult(
            task_id=i, local_device=zi, remote_device=remote,
            path_index=k_star, x=x_star, S=S_star, Tft=Tft,
            flow_finish=flow_finish if x_star > 0 else task.Trel,
        )

    return Schedule(results=results, order=order)
