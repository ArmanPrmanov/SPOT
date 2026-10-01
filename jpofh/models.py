"""Network and application (task) models for the JPOFH simulation.

Implements the system model of Sahni et al., "Multi-Hop Multi-Task Partial
Computation Offloading in Collaborative Edge Computing," IEEE TPDS 32(5), 2021
(Section 3.1) and the simulation setting of Section 5.1.

Units (kept consistent throughout, SI):
    processing speed  PS   : cycles / second   (mean 50 MCPS  -> 50e6)
    computation load  CL   : cycles            (mean 300 KCC  -> 300e3)
    input data        D    : bits              (mean 50 Kb    -> 50e3)
    link bandwidth    R    : bits / second     (mean 20 Mbps  -> 20e6)
    release time      Trel : seconds           (mean 6 ms     -> 6e-3)

With these units a task's computation time CL/PS ~ 6 ms and a single-hop
transfer D/R ~ 2.5 ms, matching the ~0.01 s completion times in the paper
(Tables 3 and 4).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, FrozenSet, List, Tuple

import networkx as nx
import numpy as np

# ---------------------------------------------------------------------------
# Default simulation parameters (Section 5.1 / 5.3)
# ---------------------------------------------------------------------------
MEAN_PS = 50e6      # 50 MCPS   - mean device processing speed
MEAN_BW = 20e6      # 20 Mbps   - mean link bandwidth
MEAN_CL = 300e3     # 300 KCC   - mean computation load per task
MEAN_D = 50e3       # 50 Kb     - mean input data per task
MEAN_TREL = 6e-3    # 6 ms      - mean release time
CV = 0.30           # coefficient of variation ("variance 30 percent")


def _normal_positive(rng: np.random.Generator, mean: float, size=None) -> np.ndarray:
    """Draw from a normal distribution with std = CV*mean, clipped positive."""
    vals = rng.normal(mean, CV * mean, size=size)
    # Guard against non-positive draws (speed / bandwidth / load must be > 0).
    floor = 0.05 * mean
    return np.maximum(vals, floor)


# ---------------------------------------------------------------------------
# Routing path
# ---------------------------------------------------------------------------
@dataclass
class Path:
    """A single routing path between an ordered pair of devices.

    nodes      : ordered list of device ids on the path
    edges      : frozenset of undirected links (frozenset{a, b}) on the path
    H          : number of hops  (H_jvk)
    R          : bandwidth of the path = min link bandwidth on it  (R_jvk)
    """

    nodes: Tuple[int, ...]
    edges: FrozenSet[FrozenSet[int]]
    H: int
    R: float


# ---------------------------------------------------------------------------
# Network model  G = (V, E)
# ---------------------------------------------------------------------------
@dataclass
class NetworkModel:
    num_devices: int
    num_paths: int                       # K
    PS: np.ndarray                       # processing speed per device
    graph: nx.Graph                      # underlying mesh graph, edge attr 'bw'
    paths: Dict[Tuple[int, int], List[Path]]  # (src, dst) -> up to K paths

    @property
    def avg_ps(self) -> float:
        """Average processing speed  PS-bar  (used in the rank metric, Eq. 24)."""
        return float(np.mean(self.PS))

    def W(self, src: int, dst: int, k: int) -> bool:
        """W_{src,dst,k}: is there a k-th routing path between src and dst?"""
        return src != dst and k < len(self.paths.get((src, dst), []))

    def get_path(self, src: int, dst: int, k: int) -> Path:
        return self.paths[(src, dst)][k]


def build_network(num_devices: int, num_paths: int,
                  rng: np.random.Generator, max_tries: int = 200) -> NetworkModel:
    """Generate a random connected mesh network (Section 5.1).

    Devices are placed uniformly at random in an M x M area; any two devices
    closer than 2*M/5 units are connected.  The area scales with the number of
    devices to keep a similar network density.  Regenerated until connected.
    """
    M = num_devices
    area = float(M)                 # M x M square units
    radius = 2.0 * M / 5.0          # connection radius

    for _ in range(max_tries):
        pos = rng.uniform(0.0, area, size=(M, 2))
        G = nx.Graph()
        G.add_nodes_from(range(M))
        for a in range(M):
            for b in range(a + 1, M):
                d = np.hypot(*(pos[a] - pos[b]))
                if d < radius:
                    bw = float(_normal_positive(rng, MEAN_BW))
                    G.add_edge(a, b, bw=bw)
        if M == 1 or nx.is_connected(G):
            break
    else:
        raise RuntimeError("could not generate a connected network; "
                           "increase area or device count")

    PS = _normal_positive(rng, MEAN_PS, size=M)

    paths = _compute_k_paths(G, num_paths)
    return NetworkModel(num_devices=M, num_paths=num_paths, PS=PS,
                        graph=G, paths=paths)


def _compute_k_paths(G: nx.Graph, K: int) -> Dict[Tuple[int, int], List[Path]]:
    """K shortest (by hop count) simple paths for every ordered device pair.

    Yen's algorithm (via nx.shortest_simple_paths); ties broken by hops.  The
    path bandwidth R_jvk is the minimum link bandwidth along the path and the
    number of hops H_jvk is len(path) - 1.
    """
    paths: Dict[Tuple[int, int], List[Path]] = {}
    for s in G.nodes:
        for t in G.nodes:
            if s == t:
                continue
            k_paths: List[Path] = []
            try:
                gen = nx.shortest_simple_paths(G, s, t)  # unweighted -> by hops
                for nodes in gen:
                    edges = frozenset(
                        frozenset((nodes[i], nodes[i + 1]))
                        for i in range(len(nodes) - 1)
                    )
                    R = min(G[u][v]["bw"] for u, v in
                            zip(nodes[:-1], nodes[1:]))
                    k_paths.append(Path(nodes=tuple(nodes), edges=edges,
                                        H=len(nodes) - 1, R=R))
                    if len(k_paths) >= K:
                        break
            except nx.NetworkXNoPath:
                pass
            if k_paths:
                paths[(s, t)] = k_paths
    return paths


# ---------------------------------------------------------------------------
# Application model  A = { i | 1 <= i <= N }
# ---------------------------------------------------------------------------
@dataclass
class Task:
    id: int
    CL: float       # computation load (cycles)
    D: float        # input data (bits)
    Trel: float     # release time (seconds)
    z: int          # device where the task is generated (z_i)


def generate_tasks(num_tasks: int, num_devices: int, rng: np.random.Generator,
                   fixed_device: bool = True, mean_data: float = MEAN_D) -> List[Task]:
    """Generate N tasks (Section 5.1 / 5.3).

    fixed_device : if True, task i is generated on device i (i == j); requires
                   num_tasks <= num_devices.  If False, tasks are placed on a
                   random device (random-device setting).
    mean_data    : mean of the input-data distribution (varied in Section 5.3.4).
    """
    CL = _normal_positive(rng, MEAN_CL, size=num_tasks)
    D = _normal_positive(rng, mean_data, size=num_tasks)
    Trel = _normal_positive(rng, MEAN_TREL, size=num_tasks)

    if fixed_device:
        if num_tasks > num_devices:
            raise ValueError("fixed_device setting needs num_tasks <= num_devices")
        z = np.arange(num_tasks)
    else:
        z = rng.integers(0, num_devices, size=num_tasks)

    return [Task(id=i, CL=float(CL[i]), D=float(D[i]),
                 Trel=float(Trel[i]), z=int(z[i])) for i in range(num_tasks)]
