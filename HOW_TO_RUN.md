# HOW TO RUN THE SPOT SIMULATION

Checklist for running the offloading experiments end-to-end on a Linux host.
All seven strategies (POB, POR, BO, CO, LE, JPOFH, EDITORS) run in a single
`network_graph.py` invocation. All commands are run from the repository root.

---

## 0. One-time environment check

| Requirement | Check | Expected |
|---|---|---|
| Docker (usable without `sudo`) | `docker ps` | table, no permission error |
| Containernet / Mininet | `python3 -c "from mininet.net import Containernet; print('ok')"` | `ok` |
| Open vSwitch running | `systemctl is-active openvswitch-switch` | `active` |
| Python deps | `python3 -c "import networkx, requests, matplotlib"` | no error |
| Floodlight image | `docker images \| grep floodlight` | `piyushk2001/floodlight-controller` |
| Root access | `sudo -v` | Containernet **must** run as root |

If `docker ps` needs `sudo`, add yourself to the docker group once:
`sudo usermod -aG docker $USER`, then re-login.

---

## 1. Clean slate (always do this first)

Leftover `mn.*` containers or stale OVS state will make the run fail or, worse,
silently produce wrong numbers.

```bash
docker rm -f $(docker ps -aq --filter "name=^mn\.") 2>/dev/null
sudo mn -c
docker rm -f floodlight 2>/dev/null
```

---

## 2. Start the Floodlight SDN controller

REST API on `:8080`, OpenFlow on `:6653`. `network_graph.py` reads the topology
**only** through this REST API, so Floodlight must be up *before* the topology.

```bash
docker run -d --name floodlight -p 8080:8080 -p 6653:6653 piyushk2001/floodlight-controller
```

Verify (give it ~5–10 s to boot):

```bash
curl -s http://localhost:8080/wm/core/health/json                 # -> {"healthy":true}
curl -s http://localhost:8080/wm/core/controller/switches/json    # -> [] (no topology yet)
```

---

## 3. Build the container image for the MEC / IoT / cloud nodes

Rebuild whenever `app.py`, `requirements.txt` or `Dockerfile.server` changes,
otherwise the containers run stale code.

```bash
docker build -f Dockerfile.server -t test_server:latest .
```

---

## 4. Bring up the Containernet topology (needs root)

4 MEC servers (`10.0.0.x`), 8 IoT hosts (`10.0.1.x`), 1 cloud node (`10.0.2.1`),
4 OpenFlow switches, all pointed at the remote Floodlight controller.

**Interactive (normal use):** drops into the Mininet CLI; *leave this shell open*:

```bash
sudo python3 topology.py
```

Wait for `*** Results:` from `pingAll` (should be `0% dropped`) and the
`mininet>` prompt. The network exists only while this process is alive.

**Headless (scripted / background runs):** pipe `sleep infinity` into it so the
Mininet CLI blocks on stdin instead of getting EOF and tearing the network down:

```bash
sleep infinity | sudo python3 topology.py > /tmp/topology.log 2>&1 &
```

---

## 5. Verify the controller actually sees the topology

Do this *before* running the experiment. If Floodlight returns `[]` for switches,
the run builds an empty graph and the results are garbage.

```bash
curl -s http://localhost:8080/wm/core/controller/switches/json | grep -o switchDPID | wc -l   # expect 4
curl -s http://localhost:8080/wm/topology/links/json | head -c 200                            # expect links
docker ps --filter "name=^mn\." --format '{{.Names}}' | wc -l                                 # expect 13
```

13 containers = mec1–4 + host1–8 + cloud1.

If hosts are missing from `/wm/device/`, Floodlight has not learned them yet:
run `pingall` in the Mininet CLI to generate traffic, then re-check.

---

## 6. Run the experiment

In a **second shell** (the topology shell stays busy with the Mininet CLI):

```bash
python3 network_graph.py 2>&1 | tee run_$(date +%F_%H%M).log
```

No `sudo` needed here, it only talks to the Floodlight REST API and
`docker exec ... iperf`.

Runtime scales with `num_tasks`. On our test VM this took **~55 min for
`num_tasks=120`** across all seven strategies (POB ~20 min, POR ~12 min, the
rest faster). The run is single-threaded **on purpose**: the `iperf` transfers
are real and must not contend for bandwidth. Do not parallelize it.

Strategies run sequentially: POB → POR → BO → CO → LE → JPOFH → EDITORS.
Between each one, `assert_capacity_restored` verifies the previous strategy
released all the capacity it reserved. An `AssertionError` there means a real
capacity leak, not a flaky run.

Standalone re-run of a single baseline:

```bash
python3 jpofh_containernet/run_jpofh.py
python3 editors_containernet/run_editors.py
```

---

## 7. Knobs (bottom of `network_graph.py`, in `__main__`)

| Knob | Meaning |
|---|---|
| `num_tasks` | how many tasks from `tasks.json` to actually run (drives the output filenames) |
| `task_needed` | `1` = regenerate `tasks.json` instead of reading it (**changes the workload**) |
| `num_task` | how many tasks to generate when `task_needed=1` |
| `num_subtask` | subtasks per task (DAG size) |
| `random_assign` | balanced vs. random partial offloading |
| `max_memory`, `max_cpu`, `network_latency`, `system_load_factor` | workload / deadline params |
| `show_topology` | `1` = plot the topology (blocks until the window is closed) |

`random.seed(42)` is fixed for reproducibility. Keep `task_needed=0` so every
strategy sees the identical pre-generated workload from `tasks.json`.

To sweep task counts, change `num_tasks` (20, 40, 60, … 300) and re-run step 6.
Floodlight and the topology can stay up between runs.

---

## 8. Output

Every run writes into its own timestamped directory, `results/{YYYYmmdd-HHMMSS}/`,
so re-runs never overwrite each other. The directory is printed at the start and
end of the run.

* `results/{run-id}/{STRATEGY}_{num_tasks}.csv`: per-task completion times
  (`POB_120.csv`, `BO_120.csv`, `JPOFH_120.csv`, …)
* `results/{run-id}/discarded_task_{STRATEGY}_{num_tasks}.csv`: tasks that
  missed their deadline

To write into an existing run's directory (e.g. re-running one strategy after a
crash with `run_jpofh.py`), set the run id explicitly:
`SPOT_RUN_ID=20260714-153012 python jpofh_containernet/run_jpofh.py 120`

---

## 9. Teardown

```bash
# stop the topology: type 'exit' at the mininet> prompt, or if headless:
sudo pkill -f topology.py

docker rm -f $(docker ps -aq --filter "name=^mn\.") 2>/dev/null
sudo mn -c
docker rm -f floodlight
```

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `switches/json` returns `[]` | Topology is not up, or it started before Floodlight. Start Floodlight first, then the topology. |
| `pingAll` shows dropped packets | Controller not reachable on `:6653`. Check `docker ps` shows `floodlight` with `6653` published. |
| `KeyError` on `mn.mecN` / `mn.hostN` | Containers are not running, the topology process died. Re-run step 4. |
| Transfer times come back `None` | `iperf` server not listening inside the container. `init_server_listener` starts it (`iperf -s -D`); make sure the image was rebuilt after any `app.py` change. |
| `AssertionError: <STRATEGY> did not restore server_map` | Capacity leak in that strategy (a real bug, not flakiness). |
| `Error creating interface pair`, network wedged after a crash | Stale Mininet state → run the full step 1 cleanup, then start again from step 2. |
| `sudo: a terminal is required to read the password` | You are calling `sudo` from a non-interactive shell. Run it from a real terminal, or cache the credential first with `sudo -v`. |

### Manual poking (checking the plumbing by hand)

```bash
docker inspect mn.mec1 | grep -E '"CpuPeriod"|"CpuQuota"'   # container CPU limits
docker exec mn.mec1 iperf -c 10.0.0.2 -n 12M                # manual transfer mec1 -> mec2
docker exec mn.mec1 curl 10.0.0.2/                          # container HTTP app alive?
```
