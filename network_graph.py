import atexit
import copy
import csv
import json
import os
import random
import re
import time
import requests
import networkx as nx
import matplotlib.pyplot as plt
import subprocess

from datetime import datetime

from Task import save_tasks_to_json, create_random_task, Task, Subtask
from exec_pool import ExecPool, XferPool
# Baseline strategies ported to this harness; pure Python + networkx at import
# time (no Docker needed), so importing them is safe on the Windows dev box too
from jpofh_containernet.jpofh_strategy import jpofh_thread
from editors_containernet.editors_strategy import editors_thread

# Each run writes into its own timestamped directory, so runs never overwrite
# each other. Stamped once at import, so every strategy of one invocation (and
# the standalone runners, which import this module) lands in the same folder.
RUN_ID = os.environ.get("SPOT_RUN_ID") or datetime.now().strftime("%Y%m%d-%H%M%S")
RESULTS_DIR = os.path.join("results", RUN_ID)

# Capacity per node class: (memory KB, rate range). The per-node queue list
# is built in create_node_map. Units: 1 rate unit = 1 MHz (1e6 cycles/s),
# 1 comp unit = 1 Mcycle, so T = comp / rate is in seconds. Each node draws
# its own CPU frequency from the range (node_rate below): 2.5-4.5 GHz edge
# servers and 160-240 MHz ESP32-class IoT devices, as in the paper. The cloud
# is fixed at 5x the mean MEC rate but pays a WAN transfer cost.
#
# Sequential service: tasks are served one at a time (each request is
# admitted, placed, measured and released before the next), so capacity
# never accumulates across requests. Memory is the only admission check and
# never binds in practice; a task is discarded when its completion time
# exceeds its deadline (discard_by_deadline).
MEC_CAPACITY   = (16 * 1024**2, (2500, 4500))
CLOUD_CAPACITY = (1024 * 1024**2, (17_500, 17_500))
HOST_CAPACITY  = (1024, (160, 240))


def node_rate(name, rate_range):
    """Per-node CPU frequency draw in MHz-units, seeded by the node's name
    (string seeding is deterministic across processes), so rates are
    reproducible run-to-run and independent of the task-generation stream."""
    lo, hi = rate_range
    if lo == hi:
        return float(lo)
    return round(random.Random(f"spot-rate-{name}").uniform(lo, hi), 1)


# Inputs to POB's placement rule: only what the SDN controller can observe
# (node rates, the task DAG, and per-pair hop counts from the topology
# graph). Transfer costs are not estimated, so there is nothing to calibrate.
EST_HOPS = {}   # (node_a, node_b) -> link hops; filled by init_est_hops


def init_est_hops(topo_graph, hosts_list, mec_servers_list):
    """Hop counts (links on the shortest path) for every host<->MEC and
    MEC<->MEC pair, from the SDN controller's topology graph. Used only as
    an ordinal tie-break in the placement (fewer hops preferred), never
    converted to seconds."""
    for a in hosts_list + mec_servers_list:
        for b in mec_servers_list:
            if a != b:
                hops = nx.shortest_path_length(topo_graph, a, b)
                EST_HOPS[(a, b)] = EST_HOPS[(b, a)] = hops

# to get switch information
def get_switch_info():
    url = 'http://localhost:8080/wm/core/controller/switches/json'
    response = requests.get(url)
    # print(f"Switch {response.json()}")
    return response.json() if response.status_code == 200 else []

# to get host information
def get_host_info():
    url = 'http://localhost:8080/wm/device/'
    response = requests.get(url)
    # print(f"Host {response.json()}")
    return response.json() if response.status_code == 200 else []

# to get link information
def get_link_info():
    url = 'http://localhost:8080/wm/topology/links/json'
    response = requests.get(url)
    # print(f"Link {response.json()}")
    return response.json() if response.status_code == 200 else []


# to create graph of a topology
def create_topology_graph():
    G = nx.Graph()
    hosts_list = []
    mec_servers_list = []
    cloud_list = []

    # Add switches
    switches = get_switch_info()
    for switch in switches:
        dpid = switch.get('switchDPID')
        G.add_node(dpid, type='switch')

    # Add hosts and MEC servers with names
    hosts = get_host_info()['devices']
    for host in hosts:
        if len(host['attachmentPoint']) == 0:
            continue
        if not host.get('ipv4'):  # stale IPv6-only ND entry in Floodlight's device table
            continue
        host_ipv4 = host.get('ipv4')[0]
        last_digit = host_ipv4.split('.')[-1]  # Extract the last part of the IP
        attachment_point = host.get('attachmentPoint')[0]
        switch_dpid = attachment_point.get('switch')
        switch_port = attachment_point.get('port')

        if re.match(r"10\.0\.0\.\d+", host_ipv4):  # MEC server
            node_name = f"mn.mec{last_digit}"
            mec_servers_list.append(node_name)
            G.add_node(node_name, type='mec-server')
        elif re.match(r"10\.0\.1\.\d+", host_ipv4):  # Host
            node_name = f"mn.host{last_digit}"
            hosts_list.append(node_name)
            G.add_node(node_name, type='host')
        elif re.match(r"10\.0\.2\.\d+", host_ipv4):  # Cloud
            node_name = f"mn.cloud{last_digit}"
            cloud_list.append(node_name)
            G.add_node(node_name, type='cloud')

        # Add edge with the new name
        G.add_edge(node_name, switch_dpid, port=switch_port, type='host-link')

    # Add links between switches
    links = get_link_info()
    for link in links:
        src_switch = link.get('src-switch')
        src_port = link.get('src-port')
        dst_switch = link.get('dst-switch')
        dst_port = link.get('dst-port')

        G.add_edge(src_switch, dst_switch, src_port=src_port, dst_port=dst_port, type='switch-link')

    return G, hosts_list, mec_servers_list, cloud_list

def visualize_topology(G):
    plt.figure(figsize=(12, 10))
    pos = nx.spring_layout(G)

    # Categorize nodes
    switch_nodes = [n for n, d in G.nodes(data=True) if d['type'] == 'switch']
    host_nodes = [n for n, d in G.nodes(data=True) if d['type'] == 'host']
    mec_nodes = [n for n, d in G.nodes(data=True) if d['type'] == 'mec-server']
    cloud_nodes = [n for n, d in G.nodes(data=True) if d['type'] == 'cloud']

    # Draw nodes with different colors
    nx.draw_networkx_nodes(G, pos, nodelist=switch_nodes, node_size=700, node_color='lightblue', label="Switches")
    nx.draw_networkx_nodes(G, pos, nodelist=host_nodes, node_size=500, node_color='lightgreen', label="Hosts")
    nx.draw_networkx_nodes(G, pos, nodelist=mec_nodes, node_size=500, node_color='orange', label="MEC Servers")
    nx.draw_networkx_nodes(G, pos, nodelist=cloud_nodes, node_size=700, node_color='violet', label="Cloud")

    # Draw labels for nodes
    node_labels = {node: node for node in G.nodes()}
    nx.draw_networkx_labels(G, pos, labels=node_labels, font_size=10, font_color="black")

    # Draw edges
    nx.draw_networkx_edges(G, pos)

    # Label edges
    host_edge_labels = {(u, v): f"port:{d['port']}" for u, v, d in G.edges(data=True) if d['type'] == 'host-link'}
    nx.draw_networkx_edge_labels(G, pos, edge_labels=host_edge_labels)

    switch_edge_labels = {(u, v): f"src:{d['src_port']} dst:{d['dst_port']}" for u, v, d in G.edges(data=True) if
                          d['type'] == 'switch-link'}
    nx.draw_networkx_edge_labels(G, pos, edge_labels=switch_edge_labels)

    # Title and legend
    plt.title("Network Topology with Port Information and Node Labels")
    plt.legend()
    plt.axis('off')
    plt.show()

def create_tasks_lists(num, hosts_list, max_memory, num_subtask):
    tasks = list()

    for i in range(1, num + 1):
        task = create_random_task(f"task_{i}", max_memory, hosts_list)
        task.divide_into_subtasks(num_subtask)
        # Deadline: uniform [5, 10] s, as in the paper's setup
        task.deadline = round(random.uniform(5, 10), 3)
        tasks.append(task)

    save_tasks_to_json(tasks, "tasks.json")
    return tasks

def get_docker_details():
    """Returns a detailed mapping of container names to IDs and IP addresses."""
    docker_cmd = "docker inspect $(docker ps -q)"
    result = subprocess.run(docker_cmd, shell=True, capture_output=True, text=True)

    containers = json.loads(result.stdout)
    mapping = {}

    for container in containers:
        name = container["Name"].strip("/")  # Docker names have a leading "/"
        container_id = container["Id"]

        # ip_address = container["NetworkSettings"]["IPAddress"]  # IP inside Docker network (global?)
        ip_address = ""
        if "mn.host" in name:
            ip_address = f"10.0.1.{name.split('mn.host')[1]}"
        elif "mn.mec" in name:
            ip_address = f"10.0.0.{name.split('mn.mec')[1]}"
        elif "mn.cloud" in name:
            ip_address = f"10.0.2.{name.split('mn.cloud')[1]}"

        mapping[name] = {"id": container_id, "ip": ip_address}

    return mapping

def init_server_listener(docker_mapping, mec_servers):
    # Two receivers per container: the warm-socket transfer server (used for
    # measurements) and an iperf server (for the SPOT_XFER=iperf fallback).
    for node in mec_servers:
        if node not in docker_mapping:
            continue
        container_id = docker_mapping[node]["id"]
        subprocess.run(["docker", "exec", "-d", container_id,
                        "python", "/app/xfer_server.py"])
        subprocess.run(["docker", "exec", container_id, "iperf", "-s", "-D"])

# One persistent shell per container instead of one `docker exec` process per
# transfer: spawning exec processes dominated the wall clock and stole CPU
# from OVS forwarding mid-measurement, inflating recorded times ~2x. Closed
# via atexit so it covers __main__ and the standalone runners.
exec_pool = ExecPool()
atexit.register(exec_pool.close)

# One persistent xfer_send.py per container: transfers use warm per-pair TCP
# sockets, so recorded times are payload wire time without the
# per-connection setup cost of one-shot iperf.
xfer_pool = XferPool()
atexit.register(xfer_pool.close)

def size_to_bytes(size):
    """'300K' -> 307200; same K=1024 iperf used, keeping payload continuity."""
    size = str(size).strip()
    if size[-1] in "Kk":
        return int(size[:-1]) * 1024
    if size[-1] in "Mm":
        return int(size[:-1]) * 1024 ** 2
    return int(size)

def docker_send_to(src, dst, size, docker_mapping, subtask_id = ""):
    # print(f"{subtask_id} src: {src} ---- {size} ---- dst: {dst}")
    # Diagnostic knobs (all off unless the env var is set):
    #   SPOT_PACE      - sleep this many seconds before each transfer
    #   SPOT_XFER_LOG  - append "ts,src,dst,size,subtask,reported" per transfer
    #   SPOT_XFER      - "iperf" uses one-shot iperf instead of warm sockets
    #                    (includes per-connection setup), for comparison
    pace = os.environ.get("SPOT_PACE")
    if pace:
        time.sleep(float(pace))
    if os.environ.get("SPOT_XFER") == "iperf":
        output = exec_pool.run(
            docker_mapping[src]["id"],
            f"iperf -c {docker_mapping[dst]['ip']} -n {size} 2>&1",
        )
        if output is None:  # hung/broken channel -> treated as a failed transfer
            result = None
        else:
            # Extract transfer time using regex
            match = re.search(r"([\d\.]+)\s*sec", output)
            result = float(match.group(1)) if match else None
    else:
        result = xfer_pool.send(
            docker_mapping[src]["id"],
            docker_mapping[dst]["ip"],
            size_to_bytes(size),
        )

    xfer_log = os.environ.get("SPOT_XFER_LOG")
    if xfer_log:
        with open(xfer_log, "a") as f:
            f.write(f"{time.time()},{src},{dst},{size},{subtask_id},"
                    f"{'' if result is None else result}\n")
    return result

def get_docker_stats(container_id):
    result = subprocess.run(
        ["docker", "stats", container_id, "--no-stream", "--format", "{{.Name}} {{.CPUPerc}} {{.MemUsage}}"],
        capture_output=True, text=True
    )
    return result.stdout.strip()

def create_node_map(name_filter, capacity):
    # Get the running test_server containers of this node class
    result = subprocess.run(
        ["docker", "ps", "--filter", "ancestor=test_server:latest", "--filter", f"name={name_filter}", "-q"],
        capture_output=True, text=True
    )

    # Extract the list of container IDs running "test_server:latest"
    containers = result.stdout.splitlines()

    # Create map with container name as the key and [memory, queue, rate] as the value
    docker_info_map = {}

    for container in containers:
        # docker stats resolves the container ID to its mn.* name and skips
        # containers that don't respond
        stats = get_docker_stats(container)

        if stats:
            try:
                name, cpu_usage, mem_usage = stats.split(" ", 2)
                # cpu_usage / mem_usage are not used: compute times come from
                # the model (T = comp / rate), not container telemetry. The
                # stats call only serves as a liveness probe.
                mem_usage = mem_usage.split(" / ")[1]
                memory, rate_range = capacity
                docker_info_map[name] = [memory, [], node_rate(name, rate_range)]
            except ValueError as e:
                print(f"Error processing stats for container {container}: {e}")

    return docker_info_map

def create_server_map():
    return create_node_map("mn.mec", MEC_CAPACITY)

def create_cloud_map():
    return create_node_map("mn.cloud", CLOUD_CAPACITY)

def create_host_map():
    return create_node_map("mn.host", HOST_CAPACITY)

def assign_subtasks_to_servers_or_hosts(task, server_map, host_map, random_assign):
    """Place one task's subtasks; returns {subtask_id: node}.

    POB (random_assign=0): each subtask, in topological order, goes to the
    feasible MEC minimising the estimated finish time

        EFT(v, n) = max(max_p est_finish[p], est_node_free[n]) + comp/rate_n

    i.e. dependency ready time, the node's queue of already-placed subtasks,
    and compute at that node's rate. Transfer costs are not priced; ties
    break toward fewer hops from the data's current location (predecessors'
    nodes, else the origin host). Small parallel branches may therefore be
    spread even when the transfer outweighs the compute gain.

    POR (random_assign=1): a random feasible MEC (dependency-blind ablation).

    In both modes the origin host is a last resort, and a task with a
    subtask that fits nowhere is discarded (all -1) with its reservations
    rolled back.
    """
    subtask_server_map = {}
    # est_node_free starts empty per task (load-blind across tasks), so EFT
    # optimizes each task's own finish time: fastest feasible MECs, with
    # parallel branches spread out.
    est_finish, est_node_free = {}, {}
    discarded_subtask = None

    for subtask in task.subtasks:
        feasible = [s for s in server_map
                    if server_map[s][0] >= subtask.memory]

        def _finish_on(n):
            rate = host_map[n][-1] if n == subtask.host else server_map[n][-1]
            ready = max((est_finish[p] for p in subtask.predecessors),
                        default=0.0)
            return max(ready, est_node_free.get(n, 0.0)) + subtask.comp / rate

        def _hops_from_data(n):
            """Ordinal tie-break: links between the data's current location
            and candidate n (0 when collocated with a predecessor)."""
            preds = [subtask_server_map[p] for p in subtask.predecessors]
            if preds:
                return min(0 if p == n else EST_HOPS.get((p, n), 9)
                           for p in preds)
            return EST_HOPS.get((subtask.host, n), 9)

        if feasible and random_assign:
            node = random.choice(feasible)
        elif feasible:
            node = min(feasible, key=lambda n: (_finish_on(n),
                                                _hops_from_data(n)))
        elif host_map[subtask.host][0] >= subtask.memory:
            node = subtask.host      # last resort: no MEC fits
        else:
            discarded_subtask = subtask.subtask_id
            break

        tmap = host_map if node == subtask.host else server_map
        tmap[node][0] -= subtask.memory
        tmap[node][1].append(subtask.subtask_id)
        subtask_server_map[subtask.subtask_id] = node

        # bookkeeping the next subtasks' EFT reads (kept for POR too, so the
        # two modes stay one code path)
        est_finish[subtask.subtask_id] = _finish_on(node)
        est_node_free[node] = est_finish[subtask.subtask_id]

    if discarded_subtask is not None:
        # roll back every reservation made before the infeasible subtask
        for st_id, node in subtask_server_map.items():
            st = get_subtask_by_id(task, st_id)
            tmap = host_map if node == st.host else server_map
            tmap[node][0] += st.memory
            tmap[node][1].remove(st_id)
        return {st.subtask_id: -1 for st in task.subtasks}
    return subtask_server_map

def find_shortest_paths(G, host, server):
    shortest_paths = {}
    path = nx.shortest_path(G, source=host, target=server, weight='weight')
    shortest_paths[(host, server)] = path

    return shortest_paths

def get_subtask_by_id(task, subtask_id):
    for subtask in task.subtasks:
        if subtask.subtask_id == subtask_id:
            return subtask
    return None


def read_tasks_file(task_num):
    with open("tasks.json", "r") as file:
        tasks_data = json.load(file)

    tasks_list = []
    for task in tasks_data[:task_num]:
        task_obj = Task(task['task_id'], task['memory'], task['cpu'], task['host'], task['deadline'])
        subtasks_list = []
        for subtask in task['subtasks']:
            subtask_obj = Subtask(subtask['subtask_id'], subtask['memory'], subtask['cpu'], subtask['result'], task['task_id'], task['host'])
            subtask_obj.predecessors = list(subtask['predecessors'])
            subtask_obj.successors = list(subtask['successors'])
            subtasks_list.append(subtask_obj)

        task_obj.subtasks = subtasks_list
        tasks_list.append(task_obj)

    return tasks_list

# Compute model (paper eqs. 9-10): T = C / f. subtask.comp is work in
# Mcycles and a node's rate slot ([-1]) is its CPU frequency in MHz, so T is
# in seconds.
def calculate_comp_time(subtask, host_map, server_map, subtask_server_map):
    if subtask_server_map[subtask.subtask_id] == -1:
        return 0.0
    time = 0.0
    if subtask_server_map[subtask.subtask_id] == subtask.host:
        time = subtask.comp / host_map[subtask.host][-1]
    else:
        time = subtask.comp / server_map[subtask_server_map[subtask.subtask_id]][-1]
    return float(time)

def calculate_comp_task_time(task, host_map, server_map, task_server_map):
    if task_server_map[task.task_id] == -1:
        return 0.0
    # The task's work is the sum over its subtasks (equal to task.comp)
    total_comp = sum(st.comp for st in task.subtasks)
    if task_server_map[task.task_id] == task.host:
        time = total_comp / host_map[task.host][-1]
    else:
        time = total_comp / server_map[task_server_map[task.task_id]][-1]
    return float(time)

def results_path(filename):
    """Path inside this run's timestamped results directory, creating it lazily."""
    os.makedirs(RESULTS_DIR, exist_ok=True)
    return os.path.join(RESULTS_DIR, filename)

def save_assigned_work(assigned_work, server_map, task_num, random_assign):
    """Data for the load-balancing figure (Fig. 8): cumulative Mcycles
    routed to each MEC over the run."""
    if random_assign:
        method_name = "random"
    else:
        method_name = "balanced"
    filename = results_path(f"{method_name}_{task_num}.csv")

    with open(filename, mode="w", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["Server", "AssignedMcycles", "Rate"])  # CSV Header

        keys = list(assigned_work.keys())
        keys.sort()
        for key in keys:
            writer.writerow([key, assigned_work[key], server_map[key][-1]])


def partial_offloading_thread(tasks_lists, server_map, host_map, docker_mapping, topo_graph, discarded_task_set, random_assign):
    subtask_server_map = {}
    task_edge_map = {}
    # Fig-8 data: cumulative Mcycles routed to each MEC (see save_assigned_work)
    assigned_work = {s: 0.0 for s in server_map}

    # One task at a time: place, measure, release, then serve the next, so
    # admission never sees another task's reservations. Transfers also run
    # sequentially so measurements are not distorted by bandwidth contention.
    for task in tasks_lists:
        print(f"Start assign subtask to server/map for {task.task_id}")
        placement = assign_subtasks_to_servers_or_hosts(task, server_map, host_map, random_assign)
        subtask_server_map.update(placement)
        if set(placement.values()) == {-1}:
            # infeasible everywhere: nothing was reserved, nothing to measure
            discarded_task_set.add(task.task_id)
            continue

        for st in task.subtasks:
            if placement[st.subtask_id] in assigned_work:
                assigned_work[placement[st.subtask_id]] += st.comp

        # Find the shortest paths if a subtask is offloaded to a server
        subtask_exec_path = {}
        print(f"Start find the shortest path for {task.task_id}")
        for subtask in task.subtasks:
            if isinstance(placement[subtask.subtask_id], str):
                subtask_exec_path[subtask] = find_shortest_paths(
                    topo_graph, subtask.host, placement[subtask.subtask_id]
                )

        # Handle this task's offloading and transmission
        task_edge_map[task.task_id] = {}
        print(f"Start send to from host and between for {task.task_id}")
        for subtask in task.subtasks:
            if subtask_server_map[subtask.subtask_id] != subtask.host:  # Offload to server
                transfer_time = docker_send_to(
                    subtask.host,
                    subtask_server_map[subtask.subtask_id],
                    f"{subtask.memory}K",
                    docker_mapping,
                    subtask.subtask_id
                )
                if transfer_time is None:
                    discarded_task_set.add(subtask.task)
                task_edge_map[task.task_id][(subtask.host, subtask.subtask_id)] = transfer_time
            else:
                task_edge_map[task.task_id][(subtask.host, subtask.subtask_id)] = 0

            # Do transmission if subtask has successors
            for successor in subtask.successors:
                if subtask_server_map[subtask.subtask_id] == subtask_server_map[successor]:
                    task_edge_map[task.task_id][(subtask.subtask_id, successor)] = 0
                else:
                    transfer_time = docker_send_to(
                        subtask_server_map[subtask.subtask_id],
                        subtask_server_map[successor],
                        f"{subtask.result}K",
                        docker_mapping,
                        subtask.subtask_id
                    )
                    if transfer_time is None:
                        discarded_task_set.add(subtask.task)
                    task_edge_map[task.task_id][(subtask.subtask_id, successor)] = transfer_time

            # Restore capacity now that the subtask's transfers are measured
            if subtask_server_map[subtask.subtask_id] == subtask.host:
                host_map[subtask.host][0] += subtask.memory
                host_map[subtask.host][1].remove(subtask.subtask_id)
            else:
                server_map[subtask_server_map[subtask.subtask_id]][0] += subtask.memory
                server_map[subtask_server_map[subtask.subtask_id]][1].remove(subtask.subtask_id)

        if task.task_id not in discarded_task_set:
            print(f"Start send to host for {task.task_id}")
            # time from last server to host
            last_subtask = task.subtasks[-1]
            if subtask_server_map[last_subtask.subtask_id] != task.host:
                transfer_time = docker_send_to(
                    subtask_server_map[last_subtask.subtask_id],
                    task.host,
                    f"{last_subtask.result}K",
                    docker_mapping,
                    last_subtask.subtask_id
                )
                if transfer_time is None:
                    discarded_task_set.add(task.task_id)
                task_edge_map[task.task_id][(last_subtask.subtask_id, task.host)] = transfer_time
            else:
                task_edge_map[task.task_id][(last_subtask.subtask_id, task.host)] = 0

        if task.task_id in discarded_task_set:
            # a discarded task must not leave a partial edge map for the DP
            # (all-(-1) tasks never got an entry, hence the None default)
            task_edge_map.pop(task.task_id, None)

    save_assigned_work(assigned_work, server_map, len(tasks_lists), random_assign)
    return subtask_server_map, task_edge_map

# Completion time of each task (critical path through the DAG).
# Returns {task_id: time}, the same shape every other strategy uses.
def task_exec_times(tasks_lists, subtask_server_map, task_edge_map, discarded_task_set):
    exec_times = {}

    for task in tasks_lists:
        print(f"Start exec time calc for {task.task_id}")
        if task.task_id not in task_edge_map:
            continue
        if task.task_id in discarded_task_set:
            continue

        edges = task_edge_map[task.task_id]

        # Uploads serialize on the host's single physical uplink, in subtask
        # order: the i-th offloaded subtask's input is available on its server
        # at the cumulative sum of upload times 1..i (locally-placed subtasks
        # store 0 for their upload key, so they don't advance the clock).
        upload_done, t_up = {}, 0.0
        for st in task.subtasks:
            t_up += float(edges.get((st.host, st.subtask_id), 0.0) or 0.0)
            upload_done[st.subtask_id] = t_up

        # One subtask at a time per node (single CPU per container): list-
        # schedule in task.subtasks order, which is topological (dependencies
        # only point from lower to higher index). A subtask starts when its
        # input has landed, its predecessors' results have arrived, and its
        # node is free.
        node_free, finish = {}, {}
        for st in task.subtasks:
            node = subtask_server_map[st.subtask_id]
            ready = upload_done[st.subtask_id]
            for pred_id in st.predecessors:
                ready = max(ready, finish[pred_id]
                            + float(edges.get((pred_id, st.subtask_id), 0.0) or 0.0))
            start = max(ready, node_free.get(node, 0.0))
            finish[st.subtask_id] = start + calculate_comp_time(
                st, host_map, server_map, subtask_server_map)
            node_free[node] = finish[st.subtask_id]

        # result return: the final subtask's result back to the origin host
        # (0 if the final subtask ran on the host)
        total = max(finish.values())
        total += float(edges.get(
            (task.subtasks[-1].subtask_id, task.host), 0.0) or 0.0)
        exec_times[task.task_id] = total

    return exec_times


# BO's selection state: cumulative Mcycles routed to each server in this
# strategy run (used for selection only, not as an admission cap). Picking
# the server with the least assigned work is SDBlockEdge's selection policy
# without its token economy. Reset at strategy start; CO reuses the function
# with a one-node cloud map, where the rule has no effect.
BO_ASSIGNED = {}


def binary_offloading_decision(task, server_map, host_map, task_server_map):
    # Offload-first: the device is weak, so BO tries the servers before local
    # execution. The task's work (sum of its subtasks) feeds BO_ASSIGNED; the
    # server must hold the whole task, so its memory is checked and reserved.
    task_total_comp = sum(subtask.comp for subtask in task.subtasks)
    sorted_servers = sorted(server_map.items(),
                            key=lambda s: BO_ASSIGNED.get(s[0], 0.0))
    dst = -1
    for server_id, (server_memory, server_queue, server_rate) in sorted_servers:
        if server_memory >= task.memory:
            task_server_map[task.task_id] = server_id
            BO_ASSIGNED[server_id] = BO_ASSIGNED.get(server_id, 0.0) + task_total_comp

            server_map[server_id][0] -= task.memory
            server_map[server_id][1].append(task.task_id)
            dst = server_id
            break
    if dst == -1 and host_map[task.host][0] >= task.memory:
        # Local fallback (the x=0 case of binary offloading): when no server
        # can hold the whole task, it runs on the origin device, with the
        # same admission check as LE's local_decision.
        host_map[task.host][0] -= task.memory
        host_map[task.host][1].append(task.task_id)
        dst = task.host
    return {task.task_id: dst}

def binary_offloading_thread(tasks_lists, server_map, host_map, discarded_task_set):
    task_total_time = {}
    task_server_map = {}
    BO_ASSIGNED.clear()   # fresh counter per strategy run (BO, then CO)

    # First step: assign tasks to servers
    for task in tasks_lists:
        task_total_time[task.task_id] = 0
        task_server_map.update(binary_offloading_decision(task, server_map, host_map, task_server_map))

    # Find the shortest paths if task is offloaded to a server
    task_exec_path = {}

    for task in tasks_lists:
        # print(task.task_id)
        if isinstance(task_server_map[task.task_id], str):
            task_exec_path[task] = find_shortest_paths(
                topo_graph, task.host, task_server_map[task.task_id]
            )

        # Find the time to offload task
        task_total_time[task.task_id] = 0

        if task_server_map[task.task_id] == -1:
            discarded_task_set.add(task.task_id)
            continue

        if task_server_map[task.task_id] != task.host:  # Offload to server
            # Task upload to the server (full input), then the final result
            # back to the host (the same return payload every strategy
            # charges); measured one after the other so they don't contend
            for src, dst, size in (
                    (task.host, task_server_map[task.task_id],
                     f"{task.memory}K"),
                    (task_server_map[task.task_id], task.host,
                     f"{task.subtasks[-1].result}K")):
                transfer_time = docker_send_to(src, dst, size, docker_mapping, task.task_id)
                if transfer_time is None:
                    discarded_task_set.add(task.task_id)
                    break       # task is discarded; skip the other leg
                task_total_time[task.task_id] += transfer_time

        # Restore capacity now that the task's transfers are measured
        # (restore mirrors the reservation exactly: the task's memory)
        if task_server_map[task.task_id] == task.host:
            host_map[task.host][0] += task.memory
            host_map[task.host][1].remove(task.task_id)
        else:
            server_map[task_server_map[task.task_id]][0] += task.memory
            server_map[task_server_map[task.task_id]][1].remove(task.task_id)

    for task in tasks_lists:
        if task.task_id in discarded_task_set:
            # a discarded task must not leave a 0.0/partial row in the
            # results CSV (save_results writes the dict unfiltered)
            task_total_time.pop(task.task_id, None)
            continue
        comp_time = calculate_comp_task_time(task, host_map, server_map, task_server_map)

        task_total_time[task.task_id] += comp_time

    return task_total_time

def local_decision(task, host_map):
    # Whole-task admission on the device (same memory check as BO's local
    # fallback)
    if host_map[task.host][0] >= task.memory:
        host_map[task.host][0] -= task.memory
        host_map[task.host][1].append(task.task_id)
        dst = task.host
    else:
        dst = -1
    return {task.task_id: dst}

def local_thread(tasks_lists, host_map, discarded_task_set):
    task_host_map = {}
    task_total_time = {}

    # One task at a time per device: reserve, account, release. An IoT host
    # runs its queue sequentially, so it never holds more than one task's
    # memory at once.
    for task in tasks_lists:
        task_host_map.update(local_decision(task, host_map))
        if task_host_map[task.task_id] == -1:
            discarded_task_set.add(task.task_id)
            continue

        task_total_time[task.task_id] = calculate_comp_task_time(
            task, host_map, {}, task_host_map)

        host_map[task.host][0] += task.memory
        host_map[task.host][1].remove(task.task_id)

    return task_total_time

def save_results(task_time, task_num, method_name):
    filename = results_path(f"{method_name}_{task_num}.csv")

    with open(filename, mode="w", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["Task", "Time"])  # CSV Header
        if isinstance(task_time, list):
            for task, time in task_time:
                writer.writerow([task, time])
        else:
            keys = list(task_time.keys())
            keys.sort()
            for key in keys:
                value = task_time[key]  # extract memory and cpu
                writer.writerow([key, value])

def discard_by_deadline(task_time_map, tasks_list, discarded_task_set):
    for task in tasks_list:
        if task.task_id not in task_time_map:
            continue
        if task_time_map[task.task_id] > task.deadline:
            discarded_task_set.add(task.task_id)
            task_time_map.pop(task.task_id)

def save_discard_task(discarded_task_set, task_num, method_name):
    filename = results_path(f"discarded_task_{method_name}_{task_num}.csv")
    with open(filename, mode="w", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(discarded_task_set)

def setup_experiment(num_tasks):
    """Everything every strategy runner needs, built once.

    Shared by __main__ and the standalone runners
    (jpofh_containernet/run_jpofh.py, editors_containernet/run_editors.py).
    """
    topo_graph, hosts_list, mec_servers_list, cloud_list = create_topology_graph()
    init_est_hops(topo_graph, hosts_list, mec_servers_list)
    tasks_lists = read_tasks_file(num_tasks)
    docker_mapping = get_docker_details()
    init_server_listener(docker_mapping, mec_servers_list)
    init_server_listener(docker_mapping, hosts_list)
    init_server_listener(docker_mapping, cloud_list)
    return (topo_graph, hosts_list, mec_servers_list, cloud_list, tasks_lists,
            docker_mapping, create_server_map(), create_host_map(), create_cloud_map())

def assert_capacity_restored(strategy, snapshots, live_maps):
    """Verify a strategy released everything it reserved.

    Every strategy is supposed to restore capacity as it finishes; a leak would
    make every strategy running after it look worse, so this check keeps the
    strategy order in __main__ from affecting results.
    """
    for map_name, snapshot in snapshots.items():
        current = live_maps[map_name]
        if current != snapshot:
            diff = {
                node: {"expected": snapshot.get(node), "actual": current.get(node)}
                for node in set(snapshot) | set(current)
                if snapshot.get(node) != current.get(node)
            }
            raise AssertionError(f"{strategy} did not restore {map_name}: {diff}")


if __name__ == "__main__":
    random.seed(42)  # fixed seed for reproducible task generation / load params

    show_topology = 0  # plt.show() blocks until the window is closed

    task_needed = 0
    max_memory = 1000  # task size 50 KB - 1 MB, the paper's range
    num_task = 300  # num of generated tasks
    num_subtask = 7 # num of subtasks in each task
    num_tasks = 120 # num of tasks we use from file
    random_assign = 0

    (topo_graph, hosts_list, mec_servers_list, cloud_list, tasks_lists,
     docker_mapping, server_map, host_map, cloud_map) = setup_experiment(num_tasks)

    if show_topology:
        visualize_topology(topo_graph)

    if task_needed:
        create_tasks_lists(num_task, hosts_list, max_memory, num_subtask)
        tasks_lists = read_tasks_file(num_tasks)
    #print(f"{tasks_lists=}")

    print(f"Results of this run go to {RESULTS_DIR}/")
    print(f"{docker_mapping=}")
    print(f"Server map\n {server_map}")
    print(f"host map\n {host_map}")
    print(f"cloud map\n {cloud_map}")

    # Capacity snapshots: every strategy must leave the maps exactly as it
    # found them, otherwise the strategies after it run on leaked capacity
    capacity_snapshots = {
        "server_map": copy.deepcopy(server_map),
        "host_map": copy.deepcopy(host_map),
        "cloud_map": copy.deepcopy(cloud_map),
    }
    live_maps = {"server_map": server_map, "host_map": host_map, "cloud_map": cloud_map}


    # Partial
    discarded_task_set = set(())
    subtask_server_map, task_edge_map = partial_offloading_thread(tasks_lists, server_map, host_map, docker_mapping, topo_graph, discarded_task_set, random_assign)
    # print(f"Subtask server map \n{subtask_server_map}")
    # print(f"Task edge map \n{task_edge_map}")
    task_total_time = task_exec_times(tasks_lists, subtask_server_map, task_edge_map, discarded_task_set)

    total_time = 0
    for task_id, t in task_total_time.items():
        if task_id not in discarded_task_set:
            total_time += t

    print(f"discarded partial set={sorted(discarded_task_set)} ")
    print(f"discarded set length {len(discarded_task_set)} ")

    discard_by_deadline(task_total_time, tasks_lists, discarded_task_set)

    print("\n-----Partial balanced offloading-----\n")
    print(f"discarded partial set={sorted(discarded_task_set)} ")
    print(f"discarded set length {len(discarded_task_set)} ")
    print(f"Task total time {task_total_time}")
    print(f"{total_time=}")
    save_results(task_total_time, num_tasks, "POB")
    save_discard_task(sorted(discarded_task_set), num_tasks, "POB")
    assert_capacity_restored("POB", capacity_snapshots, live_maps)

    discarded_random_task_set = set(())
    subtask_server_map, task_edge_map = partial_offloading_thread(tasks_lists, server_map, host_map, docker_mapping, topo_graph, discarded_random_task_set, 1)
    task_random_total_time = task_exec_times(tasks_lists, subtask_server_map, task_edge_map, discarded_random_task_set)

    total_time = 0
    for task_id, t in task_random_total_time.items():
        if task_id not in discarded_random_task_set:
            total_time += t

    discard_by_deadline(task_random_total_time, tasks_lists, discarded_random_task_set)

    print("\n-----Partial random offloading-----\n")
    print(f"discarded partial set={sorted(discarded_random_task_set)} ")
    print(f"discarded set length {len(discarded_random_task_set)} ")
    print(f"Task total time {task_random_total_time}")
    print(f"{total_time=}")
    save_results(task_random_total_time, num_tasks, "POR")
    save_discard_task(sorted(discarded_random_task_set), num_tasks, "POR")
    assert_capacity_restored("POR", capacity_snapshots, live_maps)
    
    # binary
    binary_total_time = 0
    discarded_binary_task_set = set(())
    binary_task_total_time = binary_offloading_thread(tasks_lists, server_map, host_map, discarded_binary_task_set)
    

    print("\n-----Binary offloading-----\n")
    print(f"discarded binary set: \n{discarded_binary_task_set}")
    print(f"discarded set length {len(discarded_binary_task_set)} ")
    print(f"Task total time {binary_task_total_time}")
    for task in binary_task_total_time:
        binary_total_time += binary_task_total_time[task]

    discard_by_deadline(binary_task_total_time, tasks_lists, discarded_binary_task_set)

    print(f"{binary_total_time=}")
    save_results(binary_task_total_time, num_tasks, "BO")
    save_discard_task(sorted(discarded_binary_task_set), num_tasks, "BO")
    assert_capacity_restored("BO", capacity_snapshots, live_maps)

    # cloud: binary offloading against a server map that only contains the cloud
    # node, so every task goes over the WAN link to the cloud
    cloud_total_time = 0
    discarded_cloud_task_set = set(())
    cloud_task_total_time = binary_offloading_thread(tasks_lists, cloud_map, host_map, discarded_cloud_task_set)

    print("\n-----Cloud offloading-----\n")
    print(f"discarded cloud set: \n{discarded_cloud_task_set}")
    print(f"discarded set length {len(discarded_cloud_task_set)} ")
    print(f"Task total time {cloud_task_total_time}")
    for task in cloud_task_total_time:
        cloud_total_time += cloud_task_total_time[task]

    discard_by_deadline(cloud_task_total_time, tasks_lists, discarded_cloud_task_set)

    print(f"{cloud_total_time=}")
    save_results(cloud_task_total_time, num_tasks, "CO")
    save_discard_task(sorted(discarded_cloud_task_set), num_tasks, "CO")
    assert_capacity_restored("CO", capacity_snapshots, live_maps)

    # local
    local_total_time = 0
    discarded_local_task_set = set(())
    local_task_total_time = local_thread(tasks_lists, host_map, discarded_local_task_set)

    print("\n-----Local offloading-----\n")
    print(f"discarded local set: \n{discarded_local_task_set}")
    print(f"discarded set length {len(discarded_local_task_set)} ")
    print(f"Task total time {local_task_total_time}")
    for task in local_task_total_time:
        local_total_time += local_task_total_time[task]

    discard_by_deadline(local_task_total_time, tasks_lists, discarded_local_task_set)
    print(f"{local_total_time=}")
    save_results(local_task_total_time, num_tasks, "LE")
    save_discard_task(sorted(discarded_local_task_set), num_tasks, "LE")
    assert_capacity_restored("LE", capacity_snapshots, live_maps)

    # JPOFH: dependency-unaware partial offloading baseline (Sahni et al.,
    # IEEE TPDS 2021); host -> MEC only, like its paper and SPOT's targets
    jpofh_total_time = 0
    discarded_jpofh_task_set = set(())
    jpofh_task_total_time = jpofh_thread(
        tasks_lists, server_map, host_map, docker_mapping, topo_graph,
        discarded_jpofh_task_set, mec_servers_list, measure_fn=docker_send_to)

    print("\n-----JPOFH offloading-----\n")
    print(f"discarded JPOFH set: \n{discarded_jpofh_task_set}")
    print(f"discarded set length {len(discarded_jpofh_task_set)} ")
    print(f"Task total time {jpofh_task_total_time}")
    for task in jpofh_task_total_time:
        jpofh_total_time += jpofh_task_total_time[task]

    discard_by_deadline(jpofh_task_total_time, tasks_lists, discarded_jpofh_task_set)

    print(f"{jpofh_total_time=}")
    save_results(jpofh_task_total_time, num_tasks, "JPOFH")
    save_discard_task(sorted(discarded_jpofh_task_set), num_tasks, "JPOFH")
    assert_capacity_restored("JPOFH", capacity_snapshots, live_maps)

    # EDITORS: trust-filtered, energy-aware fine-grained baseline (Baker et
    # al., IoT 25, 2024); entropy-derived trust weights (its code default)
    editors_total_time = 0
    discarded_editors_task_set = set(())
    editors_task_total_time = editors_thread(
        tasks_lists, server_map, host_map, docker_mapping, topo_graph,
        discarded_editors_task_set, mec_servers_list, measure_fn=docker_send_to)

    print("\n-----EDITORS offloading-----\n")
    print(f"discarded EDITORS set: \n{discarded_editors_task_set}")
    print(f"discarded set length {len(discarded_editors_task_set)} ")
    print(f"Task total time {editors_task_total_time}")
    for task in editors_task_total_time:
        editors_total_time += editors_task_total_time[task]

    discard_by_deadline(editors_task_total_time, tasks_lists, discarded_editors_task_set)

    print(f"{editors_total_time=}")
    save_results(editors_task_total_time, num_tasks, "EDITORS")
    save_discard_task(sorted(discarded_editors_task_set), num_tasks, "EDITORS")
    assert_capacity_restored("EDITORS", capacity_snapshots, live_maps)

    print(f"\nAll results written to {RESULTS_DIR}/")

