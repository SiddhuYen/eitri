"""Orthogonal routing for the domain diagrams: tubes and wires as right-angle paths that enter components at their named ports, go
around component bodies, and never run along another route (they may cross it at a right angle).

    r = Router(obstacles=[(x, y, w, h), ...], bounds=(x0, y0, x1, y1))
    pts = r.route((x, y, "up"), (x, y, "left"))      # port -> port, each with the direction the line leaves it; None if no path

The search runs A* on a grid made of the ports' own coordinates plus lines every `step` px, so every path starts and ends exactly
on its ports. Each routed path is remembered: a later route pays to come close to it and may not share its track.
"""
from __future__ import annotations

import heapq
from collections import defaultdict

STUB = 14.0
DIRS = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}


class Router:
    def __init__(self, obstacles, bounds, step: float = 8.0, clearance: float = 5.0, bend: float = 14.0, near: float = 10.0):
        self.obstacles = [(x - clearance, y - clearance, w + 2 * clearance, h + 2 * clearance) for x, y, w, h in obstacles]
        self.bounds = bounds
        self.step, self.bend, self.near = step, bend, near
        self.h: dict[float, list[tuple[float, float]]] = defaultdict(list)    # taken horizontal runs: y -> [(xa, xb)]
        self.v: dict[float, list[tuple[float, float]]] = defaultdict(list)    # taken vertical runs: x -> [(ya, yb)]
        self.extra_x: set[float] = set()
        self._active = self.obstacles
        self.extra_y: set[float] = set()

    # ------------------------------------------------------------------ grid
    def _lines(self, lo, hi, extra) -> list[float]:
        n = int((hi - lo) / self.step) + 1
        vals = {round(lo + i * self.step, 2) for i in range(n + 1)} | {round(v, 2) for v in extra if lo <= v <= hi}
        return sorted(vals)

    def _blocked(self, x1, y1, x2, y2) -> bool:
        xa, xb, ya, yb = min(x1, x2), max(x1, x2), min(y1, y2), max(y1, y2)
        for ox, oy, ow, oh in self._active:
            if xa < ox + ow and xb > ox and ya < oy + oh and yb > oy:
                return True
        return False

    # ------------------------------------------------------------------ search
    def route(self, a, b):
        """a, b: (x, y, direction) or (x, y, direction, stub length): the line leaves straight for the stub before it may turn."""
        (ax, ay, ad), (bx, by, bd) = a[:3], b[:3]
        la, lb = (a[3] if len(a) > 3 else STUB), (b[3] if len(b) > 3 else STUB)
        sa = (ax + DIRS[ad][0] * la, ay + DIRS[ad][1] * la)
        sb = (bx + DIRS[bd][0] * lb, by + DIRS[bd][1] * lb)
        inside = lambda p, o: o[0] <= p[0] <= o[0] + o[2] and o[1] <= p[1] <= o[1] + o[3]
        self._active = [o for o in self.obstacles if not (inside(sa, o) or inside(sb, o))]   # an end's own small body (a tee's dot)
        x0, y0, x1, y1 = self.bounds
        xs = self._lines(x0, x1, self.extra_x | {sa[0], sb[0]})
        ys = self._lines(y0, y1, self.extra_y | {sa[1], sb[1]})
        ix = {v: i for i, v in enumerate(xs)}
        iy = {v: i for i, v in enumerate(ys)}
        start = (ix[round(sa[0], 2)], iy[round(sa[1], 2)])
        goal = (ix[round(sb[0], 2)], iy[round(sb[1], 2)])
        arrive = tuple(-v for v in DIRS[bd])                         # the last move goes toward the port
        h = lambda c: abs(xs[c[0]] - xs[goal[0]]) + abs(ys[c[1]] - ys[goal[1]])
        startd = DIRS[ad]
        pq = [(h(start), 0.0, start, startd)]
        best = {(start, startd): 0.0}
        prev: dict = {}
        found = None
        while pq:
            f, g, cell, d = heapq.heappop(pq)
            if g > best.get((cell, d), 1e18) + 1e-9:
                continue
            if cell == goal:
                found = (cell, d)
                break
            cx, cy = cell
            for nd in DIRS.values():
                if nd == (-d[0], -d[1]):
                    continue
                nx, ny = cx + nd[0], cy + nd[1]
                if not (0 <= nx < len(xs) and 0 <= ny < len(ys)):
                    continue
                X1, Y1, X2, Y2 = xs[cx], ys[cy], xs[nx], ys[ny]
                if self._blocked(X1, Y1, X2, Y2):
                    continue
                horiz = nd[1] == 0
                if self._along(horiz, X1, Y1, X2, Y2):
                    continue                                          # never along another route
                if nd != d and (cx, cy) != start and self._on_route(X1, Y1):
                    continue                                          # never turn on another route (it would read as a junction)
                cost = abs(X2 - X1) + abs(Y2 - Y1)
                if nd != d:
                    cost += self.bend
                cost += self._crowding(horiz, X1, Y1, X2, Y2)
                if (nx, ny) == goal and nd != arrive and (cx, cy) != start:
                    cost += self.bend                                 # come in straight toward the port
                ng = g + cost
                st = ((nx, ny), nd)
                if ng < best.get(st, 1e18) - 1e-9:
                    best[st] = ng
                    prev[st] = (cell, d)
                    heapq.heappush(pq, (ng + h((nx, ny)), ng, (nx, ny), nd))
        if found is None:
            return None
        cells = []
        st = found
        while st[0] != start or st[1] != startd:
            cells.append(st[0])
            st = prev.get(st)
            if st is None:
                break
        cells.append(start)
        cells.reverse()
        grid = [(xs[c[0]], ys[c[1]]) for c in cells]
        pts = _simplify([(ax, ay)] + grid + [(bx, by)])
        self.take(pts)
        return pts

    def _crowding(self, horiz, X1, Y1, X2, Y2) -> float:
        """A route running right beside another (closer than `near`) pays: parallel runs keep a readable gap."""
        pen = 0.0
        lo, hi = (min(X1, X2), max(X1, X2)) if horiz else (min(Y1, Y2), max(Y1, Y2))
        pos = Y1 if horiz else X1
        for p, runs in (self.h if horiz else self.v).items():
            if 0 < abs(p - pos) < self.near:
                for a, b in runs:
                    if a < hi and b > lo:
                        pen += 150.0
        return pen

    def _along(self, horiz, X1, Y1, X2, Y2) -> bool:
        if horiz:
            lo, hi = min(X1, X2), max(X1, X2)
            return any(a < hi - 1e-6 and b > lo + 1e-6 for a, b in self.h.get(round(Y1, 2), ()))
        lo, hi = min(Y1, Y2), max(Y1, Y2)
        return any(a < hi - 1e-6 and b > lo + 1e-6 for a, b in self.v.get(round(X1, 2), ()))

    def _on_route(self, x, y) -> bool:
        return any(a - 1e-6 <= x <= b + 1e-6 for a, b in self.h.get(round(y, 2), ())) or \
               any(a - 1e-6 <= y <= b + 1e-6 for a, b in self.v.get(round(x, 2), ()))

    def take(self, pts) -> None:
        """Remember a path (routed here, or drawn some other way) so later routes keep off it."""
        for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
            if abs(y1 - y2) < 1e-6:
                self.h[round(y1, 2)].append((min(x1, x2), max(x1, x2)))
                self.extra_y.add(round(y1, 2))
            elif abs(x1 - x2) < 1e-6:
                self.v[round(x1, 2)].append((min(y1, y2), max(y1, y2)))
                self.extra_x.add(round(x1, 2))


def _simplify(pts):
    out = [pts[0]]
    for p in pts[1:]:
        if abs(p[0] - out[-1][0]) < 1e-6 and abs(p[1] - out[-1][1]) < 1e-6:
            continue
        out.append(p)
    i = 1
    while i < len(out) - 1:
        a, b, c = out[i - 1], out[i], out[i + 1]
        if (abs(a[0] - b[0]) < 1e-6 and abs(b[0] - c[0]) < 1e-6) or (abs(a[1] - b[1]) < 1e-6 and abs(b[1] - c[1]) < 1e-6):
            out.pop(i)
        else:
            i += 1
    return out
