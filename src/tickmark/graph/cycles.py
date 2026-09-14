"""Circular references (check 5).

Excel will tell you a workbook contains a circular reference. It is markedly less
helpful about *which* cells form the loop, and on a large model people give up
looking — which is how a workbook ends up shipped with iterative calculation
switched on and nobody able to say what it is iterating.

Tarjan's algorithm, iteratively rather than recursively: a dependency chain in a
real financial model can run thousands of cells deep, and Python's recursion
limit would turn a finding into a crash.
"""

from __future__ import annotations

from tickmark.graph.dependency import CellKey, DependencyGraph

__all__ = ["find_cycles"]


def find_cycles(graph: DependencyGraph) -> list[list[CellKey]]:
    """Every cycle in the workbook, as lists of cells.

    A cell that reads its own range is returned as a one-cell cycle — it is a
    genuine circular reference, and the commonest one.
    """
    index: dict[CellKey, int] = {}
    low: dict[CellKey, int] = {}
    on_stack: set[CellKey] = set()
    stack: list[CellKey] = []
    counter = 0
    cycles: list[list[CellKey]] = []

    for root in sorted(graph.cells, key=lambda k: (k.sheet, k.column, k.row)):
        if root in index:
            continue

        # (node, iterator over its precedents) — an explicit stack standing in
        # for the recursion Tarjan is usually written with.
        work: list[tuple[CellKey, list[CellKey]]] = [
            (root, sorted(graph.precedents(root), key=lambda k: (k.sheet, k.column, k.row)))
        ]
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)

        while work:
            node, pending = work[-1]
            if pending:
                successor = pending.pop()
                if successor not in index:
                    index[successor] = low[successor] = counter
                    counter += 1
                    stack.append(successor)
                    on_stack.add(successor)
                    work.append(
                        (
                            successor,
                            sorted(
                                graph.precedents(successor),
                                key=lambda k: (k.sheet, k.column, k.row),
                            ),
                        )
                    )
                elif successor in on_stack:
                    low[node] = min(low[node], index[successor])
                continue

            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])

            if low[node] == index[node]:
                component: list[CellKey] = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                # A component of one is only a cycle if the cell reads itself;
                # otherwise it is just an ordinary cell with no loop.
                if len(component) > 1 or graph.reads_itself(node):
                    cycles.append(sorted(component, key=lambda k: (k.sheet, k.column, k.row)))

    return cycles
