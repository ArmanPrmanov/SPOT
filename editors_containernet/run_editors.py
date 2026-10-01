"""Re-run only the EDITORS strategy inside the SPOT Containernet emulation.

EDITORS normally runs as part of the main sweep (`python network_graph.py`,
which runs all seven strategies); this standalone runner exists to re-run the
single EDITORS strategy after a crash without redoing the full sweep.  It uses
network_graph.setup_experiment, so the conditions are identical to the main
run.

Prerequisites (same as SPOT, see ../README.md):
  1. Floodlight controller running (REST on localhost:8080).
  2. `test_server:latest` image built.
  3. `sudo python topology.py` up (Mininet CLI left running).

Run it from the REPOSITORY ROOT so tasks.json and results/ resolve and
network_graph is importable:

    python editors_containernet/run_editors.py            # uses num_tasks below
    python editors_containernet/run_editors.py 120        # override task count

Output (into a fresh timestamped run directory):
  results/<run-id>/EDITORS_<num_tasks>.csv
  results/<run-id>/discarded_task_EDITORS_<num_tasks>.csv

To drop the re-run next to an earlier run's files instead, reuse its run id:

    SPOT_RUN_ID=20260714-153012 python editors_containernet/run_editors.py 120
"""

import os
import sys

# Make the repository root importable (network_graph.py lives there) and used
# as the working directory so tasks.json / results/ match SPOT's paths.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
os.chdir(_REPO_ROOT)

import network_graph as ng                          # noqa: E402  (SPOT harness)
from editors_containernet.editors_strategy import editors_thread  # noqa: E402


def main(num_tasks: int = 120) -> None:
    # identical setup to network_graph.py __main__ (shared helper)
    (topo_graph, hosts_list, mec_servers_list, cloud_list, tasks_lists,
     docker_mapping, server_map, host_map, cloud_map) = ng.setup_experiment(num_tasks)
    print(f"docker_mapping={docker_mapping}")
    print(f"Server map\n {server_map}")
    print(f"host map\n {host_map}")

    # --- EDITORS strategy -------------------------------------------------
    discarded_task_set = set()
    task_total_time = editors_thread(
        tasks_lists, server_map, host_map, docker_mapping, topo_graph,
        discarded_task_set, mec_servers_list, measure_fn=ng.docker_send_to,
    )

    total_time = sum(task_total_time.values())
    print("\n-----EDITORS offloading-----\n")
    print(f"discarded EDITORS set={sorted(discarded_task_set)}")
    print(f"discarded set length {len(discarded_task_set)}")

    # apply the same deadline-based discard rule SPOT uses, then save
    ng.discard_by_deadline(task_total_time, tasks_lists, discarded_task_set)

    print(f"Task total time {task_total_time}")
    print(f"total_time={total_time}")
    print(f"avg completion={total_time / max(1, len(task_total_time)):.6f} s")

    ng.save_results(task_total_time, num_tasks, "EDITORS")
    ng.save_discard_task(sorted(discarded_task_set), num_tasks, "EDITORS")
    print(f"\nWrote {ng.RESULTS_DIR}/EDITORS_{num_tasks}.csv and "
          f"{ng.RESULTS_DIR}/discarded_task_EDITORS_{num_tasks}.csv")


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 120
    main(n)
