"""DAG-based task orchestration with redirect on failure."""
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Awaitable

type NodeAction = Callable[..., Awaitable[Any]]


class NodeStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class DAGNode:
    """Single step in a task DAG."""
    id: str
    action: str  # description for the agent
    depends_on: list[str] = field(default_factory=list)
    status: NodeStatus = NodeStatus.PENDING
    result: Any = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class DAGOrchestrator:
    """
    Orchestrates multi-step tasks as a DAG.
    On node failure: Doctor Mode kicks in, tries alternative strategies.
    """

    def __init__(self):
        self.nodes: dict[str, DAGNode] = {}
        self.execution_order: list[str] = []

    def add_node(self, node_id: str, action: str, depends_on: list[str] | None = None):
        self.nodes[node_id] = DAGNode(id=node_id, action=action, depends_on=depends_on or [])

    def build_order(self) -> list[str]:
        """Topological sort for execution order."""
        order = []
        visited = set()

        def visit(nid: str):
            if nid in visited:
                return
            visited.add(nid)
            for dep in self.nodes.get(nid, DAGNode("", "")).depends_on:
                visit(dep)
            order.append(nid)

        for nid in self.nodes:
            visit(nid)
        self.execution_order = order
        return order

    def ready_nodes(self) -> list[str]:
        """Nodes whose dependencies are all done."""
        done = {nid for nid, n in self.nodes.items() if n.status == NodeStatus.DONE}
        return [
            nid for nid in self.execution_order
            if self.nodes[nid].status == NodeStatus.PENDING
            and all(d in done for d in self.nodes[nid].depends_on)
        ]

    def get_next_node(self) -> str | None:
        ready = self.ready_nodes()
        return ready[0] if ready else None

    def mark_done(self, node_id: str, result: Any = None):
        if node_id in self.nodes:
            self.nodes[node_id].status = NodeStatus.DONE
            self.nodes[node_id].result = result

    def mark_failed(self, node_id: str, error: str):
        if node_id in self.nodes:
            self.nodes[node_id].status = NodeStatus.FAILED
            self.nodes[node_id].error = error
