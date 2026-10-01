"""Offline replay of the strategies' placement decisions (no Docker/Floodlight).

Placement decisions consume no measurements: POB/POR/EDITORS see capacity
restored after every task (each node serves its subtasks one at a time),
JPOFH resets its busy clocks per task, and BO's counter/reservation phase
runs before any transfer. Replaying the production decision code on Windows reproduces exactly what the Linux runs
decided (POR is random, hence seeded by the caller). Capacity maps and
rates use the production constants (node_rate is name-seeded) and hop
counts come from the topology.py layout.

Shared by fig_parallelism.py and fig_load_balance_all.py.
"""
import os
import sys

import networkx as nx

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

import network_graph as ng
from jpofh_containernet import jpofh_strategy as jp
from editors_containernet import editors_strategy as ed

MECS = [f"mn.mec{i}" for i in range(1, 5)]
HOSTS = [f"mn.host{i}" for i in range(1, 9)]


def offline_topology():
    """The topology.py layout as a graph (switch names don't matter for hop
    counts, only the structure does): mec{i} on s{i}, hosts 1-4/5-8 on
    s1-s4, cloud1 on s1, switch mesh s1-s2, s1-s3, s1-s4, s2-s3, s3-s4."""
    G = nx.Graph()
    G.add_edges_from([("s1", "s2"), ("s1", "s3"), ("s1", "s4"),
                      ("s2", "s3"), ("s3", "s4")])
    for i in range(1, 5):
        G.add_edge(f"mn.mec{i}", f"s{i}")
    for i, h in enumerate(HOSTS):
        G.add_edge(h, f"s{(i % 4) + 1}")
    G.add_edge("mn.cloud1", "s1")
    return G


TOPO = offline_topology()
ng.EST_HOPS.clear()
ng.init_est_hops(TOPO, HOSTS, MECS)


def read_tasks(n):
    """network_graph.read_tasks_file opens tasks.json relative to the cwd;
    pin it to the repo root so the scripts work from anywhere."""
    cwd = os.getcwd()
    os.chdir(REPO)
    try:
        return ng.read_tasks_file(n)
    finally:
        os.chdir(cwd)


def fresh_maps():
    server_map = {m: [ng.MEC_CAPACITY[0], [], ng.node_rate(m, ng.MEC_CAPACITY[1])]
                  for m in MECS}
    host_map = {h: [ng.HOST_CAPACITY[0], [], ng.node_rate(h, ng.HOST_CAPACITY[1])]
                for h in HOSTS}
    return server_map, host_map


def _release(placement, task, server_map, host_map):
    for st in task.subtasks:
        node = placement.get(st.subtask_id, -1)
        if node == -1:
            continue
        tmap = host_map if node == st.host else server_map
        tmap[node][0] += st.memory
        tmap[node][1].remove(st.subtask_id)


def replay_pob(tasks, random_assign=0):
    """POB/POR: [(task, {subtask_id: node}), ...] for placed tasks + the
    number of discarded (all -1) tasks. Capacity is restored after each task."""
    server_map, host_map = fresh_maps()
    placements, discards = [], 0
    for task in tasks:
        placement = ng.assign_subtasks_to_servers_or_hosts(
            task, server_map, host_map, random_assign)
        if all(node == -1 for node in placement.values()):
            discards += 1
            continue
        placements.append((task, placement))
        _release(placement, task, server_map, host_map)
    return placements, discards


def replay_bo(tasks):
    """BO: [(task, node-or--1), ...] via the production decision (least
    cumulative BO_ASSIGNED work). As in binary_offloading_thread, every
    decision is made before any release, with reservations accumulating."""
    server_map, host_map = fresh_maps()
    ng.BO_ASSIGNED.clear()
    task_server_map = {}
    for task in tasks:
        ng.binary_offloading_decision(task, server_map, host_map,
                                      task_server_map)
    ng.BO_ASSIGNED.clear()
    return [(task, task_server_map[task.task_id]) for task in tasks]


def replay_jpofh(tasks):
    """JPOFH: [(task, mec-or-None, x), ...] in rank order (fresh busy
    clocks/flows per task) + the number of discarded tasks."""
    server_map, host_map = fresh_maps()
    avg_ps = jp._avg_ps(server_map, host_map)
    order = sorted(tasks, key=lambda t: jp.rank_metric(t, avg_ps))
    decisions, discards = [], 0
    for idx, task in enumerate(order):
        tbusy = {node: 0.0 for node in list(server_map) + list(host_map)}
        mec, _path, x, _s = jp._decide_task(
            task, idx == 0, TOPO, server_map, host_map, MECS, tbusy, [],
            avg_ps)
        if mec is None and host_map[task.host][0] < task.memory:
            discards += 1
            continue
        decisions.append((task, mec, x))
    return decisions, discards


def replay_editors(tasks):
    """EDITORS: [(task, {subtask_id: node}), ...] with its production trust
    setup (entropy weights, default threshold) + discarded-task count."""
    server_map, host_map = fresh_maps()
    metrics = ed.build_node_metrics(MECS)
    trust, trusted, info = ed.compute_trust(metrics, None, None)
    w_e, w_l, _w_u = info["weights"]
    coeff = {m: metrics[m]["energy"] / 10_000.0 for m in metrics}
    placements, discards = [], 0
    for task in tasks:
        placement = ed.assign_subtasks(task, trusted, coeff, trust, TOPO,
                                       server_map, host_map, w_e, w_l, 0.3)
        if placement is None:
            discards += 1
            continue
        placements.append((task, placement))
        ed._release(placement, server_map, host_map, task)
    return placements, discards
