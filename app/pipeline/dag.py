from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any, Callable


NodeFn = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass
class Node:
    name: str
    fn: NodeFn
    depends_on: list[str]


class DAG:
    def __init__(self, nodes: list[Node]) -> None:
        self._nodes = {n.name: n for n in nodes}

    def run(self, seed: dict[str, Any]) -> dict[str, Any]:
        order = self._topological_order()
        context = dict(seed)
        outputs: dict[str, dict[str, Any]] = {}

        for name in order:
            node = self._nodes[name]
            merged = dict(context)
            for dep in node.depends_on:
                merged[dep] = outputs[dep]
            out = node.fn(merged)
            outputs[name] = out
            context[name] = out

        context["node_outputs"] = outputs
        context["execution_order"] = order
        return context

    def _topological_order(self) -> list[str]:
        indegree = {name: 0 for name in self._nodes}
        adj: dict[str, list[str]] = defaultdict(list)

        for node in self._nodes.values():
            for dep in node.depends_on:
                if dep not in self._nodes:
                    raise ValueError(f"Node '{node.name}' depends on unknown node '{dep}'")
                indegree[node.name] += 1
                adj[dep].append(node.name)

        q = deque([name for name, deg in indegree.items() if deg == 0])
        order: list[str] = []

        while q:
            cur = q.popleft()
            order.append(cur)
            for nxt in adj[cur]:
                indegree[nxt] -= 1
                if indegree[nxt] == 0:
                    q.append(nxt)

        if len(order) != len(self._nodes):
            raise ValueError("DAG has cycle")
        return order
