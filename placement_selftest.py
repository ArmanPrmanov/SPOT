"""Self-test for the POB/POR placement in network_graph.

Runs on any machine, no Docker/Floodlight: drives
network_graph.assign_subtasks_to_servers_or_hosts with stub capacity maps
and checks the placement decisions scenario by scenario.

    python placement_selftest.py
"""
import copy
import random

import network_graph as ng

HOST = "mn.host1"


class _St:
    def __init__(self, sid, memory, comp, result, predecessors):
        self.subtask_id, self.memory, self.comp = sid, memory, comp
        self.result, self.host = result, HOST
        self.predecessors = predecessors
        self.successors = []          # placement reads predecessors only


class _T:
    def __init__(self, tid, subtasks):
        self.task_id, self.host, self.subtasks = tid, HOST, subtasks


def maps():
    server_map = {"mn.mec1": [16 * 1024**2, [], 4000.0],
                  "mn.mec2": [16 * 1024**2, [], 3500.0],
                  "mn.mec3": [16 * 1024**2, [], 3000.0],
                  "mn.mec4": [16 * 1024**2, [], 2500.0]}
    host_map = {HOST: [1024, [], 200.0]}   # mid ESP32-class draw
    return server_map, host_map


MECS = ["mn.mec1", "mn.mec2", "mn.mec3", "mn.mec4"]


def set_hops(near_mec=None):
    """Stub the controller-topology hop counts: uniform 3 hops everywhere,
    optionally one MEC on the host's own switch (2 hops)."""
    ng.EST_HOPS = {}
    for a in MECS:
        for b in MECS:
            if a != b:
                ng.EST_HOPS[(a, b)] = 3
    for m in MECS:
        ng.EST_HOPS[(HOST, m)] = ng.EST_HOPS[(m, HOST)] = 3
    if near_mec:
        ng.EST_HOPS[(HOST, near_mec)] = ng.EST_HOPS[(near_mec, HOST)] = 2


set_hops()


def release(placement, task, server_map, host_map):
    for st in task.subtasks:
        node = placement[st.subtask_id]
        if node == -1:
            continue
        tmap = host_map if node == st.host else server_map
        tmap[node][0] += st.memory
        tmap[node][1].remove(st.subtask_id)


# 1. Chain a->b->c, substantial compute: EFT must collocate the whole chain
#    on the fastest MEC (no parallelism to buy, edges pure cost).
chain = _T("chain", [_St("a", 50, 200, 4, []),
                     _St("b", 50, 200, 4, ["a"]),
                     _St("c", 50, 200, 4, ["b"])])
sm, hm = maps()
p = ng.assign_subtasks_to_servers_or_hosts(chain, sm, hm, 0)
assert set(p.values()) == {"mn.mec1"}, p
release(p, chain, sm, hm)
print("chain collocates on fastest MEC: OK")

# 2. Fan-out a->(b,c), big compute (200 Mcycles = 50 ms on mec1): b
#    collocates (queue empty behind a), c spreads (queueing behind b on
#    mec1 would cost 50 ms).
fan = _T("fan", [_St("a", 50, 200, 4, []),
                 _St("b", 50, 200, 4, ["a"]),
                 _St("c", 50, 200, 4, ["a"])])
sm, hm = maps()
p = ng.assign_subtasks_to_servers_or_hosts(fan, sm, hm, 0)
assert p["b"] == p["a"] == "mn.mec1", p
assert p["c"] != p["a"], p
release(p, fan, sm, hm)
print(f"fan-out big compute spreads the parallel branch: OK ({p['c']})")

# 3. Same fan-out, tiny compute. Edges are unpriced, so even a tiny
#    parallel branch spreads to another MEC (queueing on mec1 always looks
#    worse than a free edge). Known limitation of the rule; asserted so a
#    change in behaviour is noticed.
tiny = _T("tiny", [_St("a", 50, 5, 4, []),
                   _St("b", 50, 5, 4, ["a"]),
                   _St("c", 50, 5, 4, ["a"])])
sm, hm = maps()
p = ng.assign_subtasks_to_servers_or_hosts(tiny, sm, hm, 0)
assert p["a"] == "mn.mec1", p
assert p["c"] != p["a"], p
release(p, tiny, sm, hm)
print("fan-out tiny compute spreads: OK")

# 4. No MEC fits (memory) but the host does -> host as last resort.
sm, hm = maps()
for s in sm:
    sm[s][0] = 100                                    # all MECs too small
big = _T("big", [_St("a", 500, 10, 4, [])])
p = ng.assign_subtasks_to_servers_or_hosts(big, sm, hm, 0)
assert p["a"] == HOST, p
release(p, big, sm, hm)
print("host as last resort: OK")

# 5. Nothing fits anywhere -> all -1, maps rolled back bit-identically.
sm, hm = maps()
snap_s, snap_h = copy.deepcopy(sm), copy.deepcopy(hm)
huge = _T("huge", [_St("a", 50, 10, 4, []),
                   _St("b", 32 * 1024**2, 10, 4, ["a"])])  # b fits nowhere
p = ng.assign_subtasks_to_servers_or_hosts(huge, sm, hm, 0)
assert set(p.values()) == {-1}, p
assert sm == snap_s and hm == snap_h, "rollback leaked capacity"
print("infeasible task discarded with full rollback: OK")

# 6. POR ablation: random feasible MEC, never the host, deterministic seed.
random.seed(7)
sm, hm = maps()
p = ng.assign_subtasks_to_servers_or_hosts(fan, sm, hm, 1)
assert all(n in sm for n in p.values()), p
release(p, fan, sm, hm)
random.seed(7)
p2 = ng.assign_subtasks_to_servers_or_hosts(fan, sm, hm, 1)
assert p2 == p, "POR not deterministic under a fixed seed"
release(p2, fan, sm, hm)
print(f"POR random-MEC ablation: OK ({sorted(set(p.values()))})")

# 7. Load-blindness: identical chain tasks arriving in sequence both go to
#    the fastest MEC, since per-task EFT ignores load from earlier tasks.
sm, hm = maps()
t1 = _T("bal1", [_St("a1", 50, 200, 4, []), _St("b1", 50, 200, 4, ["a1"]),
                 _St("c1", 50, 200, 4, ["b1"])])
t2 = _T("bal2", [_St("a2", 50, 200, 4, []), _St("b2", 50, 200, 4, ["a2"]),
                 _St("c2", 50, 200, 4, ["b2"])])
p1 = ng.assign_subtasks_to_servers_or_hosts(t1, sm, hm, 0)
p2 = ng.assign_subtasks_to_servers_or_hosts(t2, sm, hm, 0)
assert set(p1.values()) == {"mn.mec1"}, p1
assert set(p2.values()) == {"mn.mec1"}, p2
release(p1, t1, sm, hm)
release(p2, t2, sm, hm)
print("load-blind per-task EFT: OK (both tasks take the fastest MEC)")

# 8. Ordinal hop tie-break: hops are never converted to seconds, but when
#    two candidates give an identical EFT (equal rates, both idle) the one
#    nearer the data wins. With distinct rates the faster node wins
#    regardless of distance (upload cost is not priced).
set_hops(near_mec="mn.mec4")
sm, hm = maps()
for s in sm:
    sm[s][-1] = 4000.0                       # equal rates -> EFT ties
near = _T("near", [_St("a", 50, 200, 4, [])])
p = ng.assign_subtasks_to_servers_or_hosts(near, sm, hm, 0)
assert p["a"] == "mn.mec4", p                # tie broken by fewer hops
release(p, near, sm, hm)
sm, hm = maps()                              # distinct rates again
far_fast = _T("far_fast", [_St("a", 50, 5, 4, [])])
p = ng.assign_subtasks_to_servers_or_hosts(far_fast, sm, hm, 0)
assert p["a"] == "mn.mec1", p                # fastest wins even far/tiny
release(p, far_fast, sm, hm)
set_hops()
print("ordinal hop tie-break: OK (tie -> near MEC; no tie -> fastest)")

print("placement self-test OK")
