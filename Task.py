import random
import networkx as nx
import json
from matplotlib import pyplot as plt


class Task:
    def __init__(self, task_id, memory, comp, host, deadline=0):
        self.task_id = task_id
        self.memory = memory
        self.comp = comp
        self.subtasks = []
        self.host = host
        self.deadline = deadline

    def divide_into_subtasks(self, num_subtasks):
        remaining_memory = self.memory
        remaining_comp = self.comp
        delta = 0.06  # 6% variability

        # Memory and comp are split as budgets: slices sum exactly to
        # task.memory / task.comp (the last subtask takes the remainder), so
        # task.comp is the task's total CPU demand.
        for i in range(num_subtasks - 1):
            subtask_memory = int(remaining_memory / (num_subtasks - i) * (1 + random.uniform(-delta, delta)))
            subtask_comp = max(1, int(remaining_comp / (num_subtasks - i) * (1 + random.uniform(-delta, delta))))
            subtask_result = random.randint(1, subtask_memory)
            subtask = Subtask(f"{self.task_id}_subtask_{i + 1}", subtask_memory, subtask_comp, subtask_result,
                              self.task_id, self.host)
            self.subtasks.append(subtask)
            remaining_memory -= subtask_memory
            remaining_comp -= subtask_comp

        subtask_result = random.randint(1, remaining_memory)
        subtask = Subtask(f"{self.task_id}_subtask_{num_subtasks}", remaining_memory, max(1, remaining_comp),
                          subtask_result, self.task_id, self.host)
        self.subtasks.append(subtask)

        # Automatically generate dependencies
        self.generate_random_dependencies()

    def add_dependency(self, pred, succ):
        if succ not in pred.successors and pred not in succ.predecessors:
            pred.successors.append(succ.subtask_id)
            succ.predecessors.append(pred.subtask_id)

    # DAG edge probability. Lower = wider/more parallel DAGs (p=1 would be a
    # total order, p=0 a pure fan-out star): 0.15 gives a median parallel
    # width of 4 and depth of 4 at 7 subtasks.
    EDGE_PROB = 0.15

    def generate_random_dependencies(self):
        if len(self.subtasks) < 2:
            return  # No dependencies needed for a single subtask

        G = nx.DiGraph()
        G.add_nodes_from(range(len(self.subtasks)))

        # Ensure a single start node (0) and a single end node (num_subtasks - 1)
        for i in range(1, len(self.subtasks) - 1):
            if random.random() < self.EDGE_PROB:
                G.add_edge(0, i)

        for i in range(1, len(self.subtasks) - 1):
            for j in range(i + 1, len(self.subtasks) - 1):
                if random.random() < self.EDGE_PROB:
                    G.add_edge(i, j)

        for i in range(1, len(self.subtasks) - 1):
            if random.random() < self.EDGE_PROB:
                G.add_edge(i, len(self.subtasks) - 1)

        # Ensure all nodes have at least one parent except root
        for node in range(1, len(self.subtasks) - 1):
            if G.in_degree(node) == 0:
                G.add_edge(0, node)

        # Ensure all nodes have at least one child except end node
        for node in range(1, len(self.subtasks) - 1):
            if G.out_degree(node) == 0:
                G.add_edge(node, len(self.subtasks) - 1)

        # Apply dependencies to subtasks
        for u, v in G.edges():
            self.add_dependency(self.subtasks[u], self.subtasks[v])

    def visualize_graph(self):
        """Visualizes task dependency graph using NetworkX."""
        G = self.create_subtask_graph()  # Get the directed graph

        # Draw the graph
        plt.figure(figsize=(8, 8))
        pos = nx.spring_layout(G)  # Position nodes for visualization
        nx.draw(G, pos, with_labels=True, node_color='lightblue', edge_color='gray', node_size=2000, font_size=10)
        plt.title(f"Task {self.task_id} Dependency Graph")
        plt.show()

    def create_subtask_graph(self):
        """Creates and returns a directed graph of subtask relations."""
        G = nx.DiGraph()

        # Add edges based on predecessor-successor relationships
        for subtask in self.subtasks:
            for successor_id in subtask.successors:
                G.add_edge(subtask.subtask_id, successor_id)

        return G

    def find_longest_path(self):
        """Finds the longest path in the subtask graph based on path length and memory usage."""
        G = self.create_subtask_graph()  # Create the directed graph

        # Ensure it's a Directed Acyclic Graph (DAG)
        if not nx.is_directed_acyclic_graph(G):
            raise ValueError("Graph contains cycles! Longest path is undefined.")

        # Find the longest path
        longest_path = nx.dag_longest_path(G)

        # Compute total memory for the longest path
        max_memory = sum(subtask.memory for subtask in self.subtasks if subtask.subtask_id in longest_path)

        return longest_path, max_memory

    def __repr__(self):
        return (f"Task({self.task_id}, Memory: {self.memory}, CPU: {self.comp}, Host: {self.host}, "
                f"Subtasks: {self.subtasks}, Deadline: {self.deadline})")

    def to_dict(self):
        return {
            "task_id": self.task_id,
            "memory": self.memory,
            "cpu": self.comp,
            "host": self.host,
            "subtasks": [subtask.to_dict() for subtask in self.subtasks],
            "deadline": self.deadline
        }


class Subtask:
    def __init__(self, subtask_id, memory, comp, result, task_id, host):
        self.subtask_id = subtask_id
        self.memory = memory
        self.comp = comp
        self.result = result
        self.predecessors = []
        self.successors = []
        self.task = task_id
        self.host = host

    def add_predecessor(self, predecessor):
        if predecessor not in self.predecessors:
            self.predecessors.append(predecessor)

    def add_successor(self, successor):
        if successor not in self.successors:
            self.successors.append(successor)

    def __repr__(self):
        return (f"Subtask({self.subtask_id}, Memory: {self.memory}, CPU: {self.comp}, Result: {self.result}, "
                f"Predecessors: {[p for p in self.predecessors]}, "
                f"Successors: {[s for s in self.successors]})")

    def to_dict(self):
        return {
            "subtask_id": self.subtask_id,
            "memory": self.memory,
            "cpu": self.comp,
            "predecessors": [p for p in self.predecessors],
            "successors": [s for s in self.successors],
            "result": self.result
        }


# Computation intensity: cycles per bit of task data. 500 c/bit is the
# intensity used by EDITORS (Baker et al. 2024).
# 1 c/bit = 8*1024/1e6 Mcycles per KB.
CYCLES_PER_BIT = 500
_MCYC_PER_KB = CYCLES_PER_BIT * 8 * 1024 / 1e6


# Generate a random task. Units: memory in KB, comp in Mcycles (1 comp unit
# = 1e6 cycles, so comp / a node rate in MHz gives seconds). comp is the
# task's total work, split across its subtasks.
def create_random_task(task_id, memory_max, hosts):
    memory = random.randint(50, memory_max)
    comp = max(7, round(memory * _MCYC_PER_KB))
    host = random.choice(hosts)
    return Task(task_id, memory, comp, host)


def save_tasks_to_json(tasks, filename):
    with open(filename, "w") as f:
        json.dump([task.to_dict() for task in tasks], f, indent=4)
