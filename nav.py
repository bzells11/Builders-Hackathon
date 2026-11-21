"""Map model for Mech Royale: terrain height, building columns on a 2 m grid, line of sight, and a
flow field (BFS) toward a goal on a 4 m walk grid. Standard library only; built once at hello."""
import array
import json
import math
import os
from collections import deque

CELL = 2.0     # fine grid for line of sight and wall columns
WCELL = 4.0    # walk grid for the flow field


class Map:
    def __init__(self, mdir):
        w = json.load(open(os.path.join(mdir, "v2", "world.json")))
        t = w["terrain"]
        self.tx0, self.ty0, self.tstep = t["minX"], t["minY"], t["step"]
        self.tnx, self.tny = t["nx"], t["ny"]
        self.tsc = t.get("scale", 0.01)
        h = array.array("h")
        with open(os.path.join(mdir, "v2", t["file"]), "rb") as f:
            h.frombytes(f.read())
        self.th = h
        b = w["bounds"]
        self.x0, self.y0 = b["minX"], b["minY"]
        self.x1, self.y1 = b["maxX"], b["maxY"]
        self.nx = int((self.x1 - self.x0) / CELL) + 1
        self.ny = int((self.y1 - self.y0) / CELL) + 1
        self.cols = {}  # cell index -> list of (base, top)
        self.roofs = []  # (cx, cy, top, height_above_ground, area, ring)
        for p in w["prisms"]:
            self._fill(p)
            if p["kind"] == "building" and not p.get("holes"):
                r = p["ring"]
                n = len(r) // 2
                a = cx = cy = 0.0
                for k in range(n):
                    x0_, y0_ = r[2 * k], r[2 * k + 1]
                    x1_, y1_ = r[2 * ((k + 1) % n)], r[2 * ((k + 1) % n) + 1]
                    c = x0_ * y1_ - x1_ * y0_
                    a += c
                    cx += (x0_ + x1_) * c
                    cy += (y0_ + y1_) * c
                if abs(a) > 1e-6:
                    cx, cy, a = cx / (3 * a), cy / (3 * a), abs(a) / 2
                    self.roofs.append((cx, cy, p["top"], p["top"] - self.ground(cx, cy), a, r))
        self._walk_grid()
        self.flow = None
        self.flow_goal = None
        self._job = None

    # ---------- terrain
    def ground(self, x, y):
        fx = (x - self.tx0) / self.tstep
        fy = (y - self.ty0) / self.tstep
        i = min(max(int(fx), 0), self.tnx - 2)
        j = min(max(int(fy), 0), self.tny - 2)
        ax, ay = min(max(fx - i, 0.0), 1.0), min(max(fy - j, 0.0), 1.0)
        n = self.tnx
        h = self.th
        k = j * n + i
        a = h[k] * (1 - ax) + h[k + 1] * ax
        c = h[k + n] * (1 - ax) + h[k + n + 1] * ax
        return (a * (1 - ay) + c * ay) * self.tsc

    # ---------- prisms -> columns (scanline, even-odd so holes cut out)
    def _fill(self, p):
        rings = [p["ring"]] + list(p.get("holes") or [])
        edges = []
        ymin, ymax = 1e9, -1e9
        for r in rings:
            n = len(r) // 2
            for k in range(n):
                ax, ay = r[2 * k], r[2 * k + 1]
                bx, by = r[2 * ((k + 1) % n)], r[2 * ((k + 1) % n) + 1]
                if ay != by:
                    edges.append((ax, ay, bx, by))
                ymin, ymax = min(ymin, ay), max(ymax, ay)
        if not edges:
            return
        base, top = p["base"], p["top"]
        j0 = max(0, int((ymin - self.y0) / CELL))
        j1 = min(self.ny - 1, int((ymax - self.y0) / CELL) + 1)
        for j in range(j0, j1 + 1):
            yc = self.y0 + (j + 0.5) * CELL
            xs = []
            for ax, ay, bx, by in edges:
                if (ay <= yc < by) or (by <= yc < ay):
                    xs.append(ax + (yc - ay) * (bx - ax) / (by - ay))
            xs.sort()
            for k in range(0, len(xs) - 1, 2):
                i0 = max(0, int(math.ceil((xs[k] - self.x0) / CELL - 0.5)))
                i1 = min(self.nx - 1, int(math.floor((xs[k + 1] - self.x0) / CELL - 0.5)))
                for i in range(i0, i1 + 1):
                    self.cols.setdefault(j * self.nx + i, []).append((base, top))

    def solid(self, x, y, z):
        i = int((x - self.x0) / CELL)
        j = int((y - self.y0) / CELL)
        if i < 0 or j < 0 or i >= self.nx or j >= self.ny:
            return True
        c = self.cols.get(j * self.nx + i)
        if c:
            for b, t in c:
                if b <= z <= t:
                    return True
        return False

    def top_at(self, x, y):
        """Highest building top over this point, or None."""
        i = int((x - self.x0) / CELL)
        j = int((y - self.y0) / CELL)
        c = self.cols.get(j * self.nx + i)
        return max(t for _, t in c) if c else None

    def good_roofs(self, cx, cy, r, hmin=12.0, hmax=50.0, amin=250.0):
        """Flat roofs whose middle is inside the circle (cx, cy, r) and reachable with a jet."""
        out = []
        for rx, ry, top, h, a, ring in self.roofs:
            if hmin <= h <= hmax and a >= amin and math.hypot(rx - cx, ry - cy) < r:
                tt = self.top_at(rx, ry)
                if tt is not None and abs(tt - top) < 0.5:
                    out.append((rx, ry, top, h, a, ring))
        return out

    @staticmethod
    def edge_point(ring, px, py):
        """Closest point on a ring's outline to (px, py)."""
        n = len(ring) // 2
        best, bp = 1e18, (ring[0], ring[1])
        for k in range(n):
            ax, ay = ring[2 * k], ring[2 * k + 1]
            bx, by = ring[2 * ((k + 1) % n)], ring[2 * ((k + 1) % n) + 1]
            dx, dy = bx - ax, by - ay
            L = dx * dx + dy * dy
            f = 0.0 if L == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L))
            qx, qy = ax + dx * f, ay + dy * f
            d = (qx - px) ** 2 + (qy - py) ** 2
            if d < best:
                best, bp = d, (qx, qy)
        return bp

    def los(self, a, b, step=CELL):
        """True when the segment a->b (3D points) passes no building column and stays above ground."""
        dx, dy, dz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
        d = math.hypot(dx, dy)
        n = int(d / step)
        if n < 1:
            return True
        cols = self.cols
        x0, y0, nx = self.x0, self.y0, self.nx
        for k in range(1, n):
            f = k / n
            x, y, z = a[0] + dx * f, a[1] + dy * f, a[2] + dz * f
            c = cols.get(int((y - y0) / CELL) * nx + int((x - x0) / CELL))
            if c:
                for bb, tt in c:
                    if bb <= z <= tt:
                        return False
            if (k & 3) == 0 and z < self.ground(x, y) - 0.3:
                return False
        return True

    # ---------- walk grid and flow field
    def _walk_grid(self):
        self.wnx = int((self.x1 - self.x0) / WCELL) + 1
        self.wny = int((self.y1 - self.y0) / WCELL) + 1
        blk = bytearray(self.wnx * self.wny)
        r = int(WCELL / CELL)
        for j in range(self.wny):
            for i in range(self.wnx):
                cx, cy = self.x0 + (i + 0.5) * WCELL, self.y0 + (j + 0.5) * WCELL
                g = self.ground(cx, cy)
                bad = False
                for dj in range(r):
                    for di in range(r):
                        c = self.cols.get((j * r + dj) * self.nx + i * r + di)
                        if c:
                            for b, t in c:
                                if b < g + 9.0 and t > g + 1.2:
                                    bad = True
                if bad:
                    blk[j * self.wnx + i] = 1
        # grow by one cell so a 2.5 m radius body clears corners
        grown = bytearray(blk)
        W = self.wnx
        for j in range(1, self.wny - 1):
            for i in range(1, W - 1):
                if not blk[j * W + i] and (blk[j * W + i - 1] or blk[j * W + i + 1] or blk[(j - 1) * W + i] or blk[(j + 1) * W + i]):
                    grown[j * W + i] = 2
        self.blk = grown

    def wcell(self, x, y):
        i = min(max(int((x - self.x0) / WCELL), 0), self.wnx - 1)
        j = min(max(int((y - self.y0) / WCELL), 0), self.wny - 1)
        return j * self.wnx + i

    def start_flow(self, gx, gy):
        """Begin a BFS from the goal; advance it with step_flow() a slice per tick."""
        n = self.wnx * self.wny
        dist = array.array("i", [-1]) * n
        g = self.wcell(gx, gy)
        dist[g] = 0
        self._job = (dist, deque([g]), (gx, gy))

    def step_flow(self, budget=6000):
        if not self._job:
            return
        dist, q, goal = self._job
        W, H, blk = self.wnx, self.wny, self.blk
        k = 0
        while q and k < budget:
            c = q.popleft()
            k += 1
            dc = dist[c] + 1
            i = c % W
            for nb in (c - 1 if i > 0 else -1, c + 1 if i < W - 1 else -1, c - W, c + W):
                if 0 <= nb < W * H and dist[nb] < 0:
                    if blk[nb] == 1:
                        dist[nb] = -2
                        continue
                    dist[nb] = dc
                    q.append(nb)
        if not q:
            self.flow, self.flow_goal, self._job = dist, goal, None

    def flow_dir(self, x, y):
        """Unit direction to step toward the flow goal, or None if no field / unreachable."""
        if self.flow is None:
            return None
        W = self.wnx
        c = self.wcell(x, y)
        d0 = self.flow[c]
        best, bc = d0 if d0 >= 0 else 1 << 30, None
        i = c % W
        for di, dj in ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (1, -1), (-1, 1), (1, 1)):
            if not (0 <= i + di < W):
                continue
            nb = c + di + dj * W
            if 0 <= nb < len(self.flow):
                v = self.flow[nb]
                if 0 <= v < best:
                    best, bc = v, nb
        if bc is None:
            return None
        tx = self.x0 + (bc % W + 0.5) * WCELL
        ty = self.y0 + (bc // W + 0.5) * WCELL
        dx, dy = tx - x, ty - y
        n = math.hypot(dx, dy) or 1.0
        return dx / n, dy / n
