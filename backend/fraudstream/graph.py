"""Finds suspected mule rings among recently alerted payments.

Every alerted payment is an edge payer -> payee in a directed graph. A mule
ring shows up as a connected cluster where money fans IN to a few accounts
and then moves on. We keep only the last N alerted edges so the graph stays
small and the query is fast.
"""
from __future__ import annotations

from collections import deque

import networkx as nx


class RingTracker:
    def __init__(self, max_edges: int = 400, min_size: int = 4):
        self._edges: deque[tuple[str, str, float]] = deque(maxlen=max_edges)
        self._min_size = min_size

    def add(self, payer: str, payee: str, amount: float) -> None:
        self._edges.append((payer, payee, amount))

    def rings(self, max_rings: int = 6) -> list[dict]:
        graph = nx.DiGraph()
        for payer, payee, amount in self._edges:
            previous = graph.get_edge_data(payer, payee, {"amount": 0.0, "count": 0})
            graph.add_edge(payer, payee, amount=previous["amount"] + amount,
                           count=previous["count"] + 1)

        rings = []
        # "Weakly" connected = connected if we ignore arrow direction.
        for nodes in nx.weakly_connected_components(graph):
            sub = graph.subgraph(nodes)
            # WHY require fan-in: a velocity burst is one payer -> many payees
            # (a star, not a ring). A ring needs an account that COLLECTS.
            if len(nodes) < self._min_size or max(d for _, d in sub.in_degree()) < 2:
                continue
            rings.append({
                "nodes": [{"id": n, "role": self._role(sub, n),
                           "in_degree": sub.in_degree(n), "out_degree": sub.out_degree(n)}
                          for n in sub.nodes],
                "edges": [{"source": u, "target": v, "amount": round(d["amount"], 2),
                           "count": d["count"]} for u, v, d in sub.edges(data=True)],
                "total_amount": round(sum(d["amount"] for _, _, d in sub.edges(data=True)), 2),
            })
        rings.sort(key=lambda ring: -len(ring["nodes"]))
        return rings[:max_rings]

    @staticmethod
    def _role(graph: nx.DiGraph, node: str) -> str:
        fan_in, fan_out = graph.in_degree(node), graph.out_degree(node)
        if fan_in >= 1 and fan_out >= 1:
            return "mule"        # receives and forwards
        if fan_in >= 2:
            return "collector"   # where the money ends up
        if fan_in == 0:
            return "sender"      # likely a victim
        return "receiver"
