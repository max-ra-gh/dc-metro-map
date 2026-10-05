#!/usr/bin/env python3
"""Measures each label's clearance from the nearest line-path casing ink.

The 7 line paths use only M / L / A, so they flatten exactly. Each is walked at
0.5 px and tested against every label box (from collide.py) inflated by half the
casing width, which is the widest ink a line puts on the page. A negative
clearance means the type sits inside the casing.
"""
import argparse
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collide  # noqa: E402
from collide import DEFAULT_SPEC, g  # noqa: E402

CASING_HALF = 15.0      # half the 30 px casing stroke
STEP = 0.5              # flattening step, px
NEAR = 200.0            # ignore ink further than this from a box centre


def arc_points(p0, r, laf, sf, p1):
    """Flatten an SVG arc with rx = ry = r about a centre we solve for."""
    (x0, y0), (x1, y1) = p0, p1
    dx, dy = (x1 - x0) / 2.0, (y1 - y0) / 2.0
    q = math.hypot(dx, dy)
    h = math.sqrt(max(r * r - q * q, 0.0))
    mx, my = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    sign = 1.0 if laf != sf else -1.0
    cx, cy = mx + sign * h * (-dy / q), my + sign * h * (dx / q)
    a0 = math.atan2(y0 - cy, x0 - cx)
    d = math.atan2(y1 - cy, x1 - cx) - a0
    if sf and d < 0:
        d += 2 * math.pi
    if not sf and d > 0:
        d -= 2 * math.pi
    n = max(int(abs(d) * r / STEP), 2)
    return [(cx + r * math.cos(a0 + d * i / n), cy + r * math.sin(a0 + d * i / n))
            for i in range(n + 1)]


def path_points(d):
    toks = d.replace(",", " ").split()
    pts, i, cur = [], 0, None
    while i < len(toks):
        c = toks[i]
        if c == "M":
            cur = (float(toks[i + 1]), float(toks[i + 2]))
            pts.append(cur)
            i += 3
        elif c == "L":
            nxt = (float(toks[i + 1]), float(toks[i + 2]))
            n = max(int(math.dist(cur, nxt) / STEP), 1)
            pts += [(cur[0] + (nxt[0] - cur[0]) * k / n,
                     cur[1] + (nxt[1] - cur[1]) * k / n) for k in range(1, n + 1)]
            cur = nxt
            i += 3
        elif c == "A":
            r = float(toks[i + 1])
            laf, sf = int(toks[i + 4]), int(toks[i + 5])
            nxt = (float(toks[i + 6]), float(toks[i + 7]))
            pts += arc_points(cur, r, laf, sf, nxt)[1:]
            cur = nxt
            i += 8
        else:
            raise SystemExit("unhandled path command %r" % c)
    return pts


def census(spec_path, corridors=None):
    """{station id: clearance px} for every label in `corridors` (all if None)."""
    sp = g.Spec(json.load(open(spec_path, encoding="utf-8")))
    resolved, placements = g.resolve_all(sp)
    ink = []
    for ln in sp.data["lines"]:
        ink += path_points(resolved[ln["id"]]["d"])
    worst = {}
    for st in sp.data["stations"]:
        if corridors and st["corridor"] not in corridors:
            continue
        lp = g.label_placement(sp, st, placements[st["id"]])
        for b in collide.label_boxes(sp, st, lp):
            near = 1e9
            for p in ink:
                dx, dy = p[0] - b["c"][0], p[1] - b["c"][1]
                if abs(dx) > NEAR or abs(dy) > NEAR:
                    continue
                lx = dx * b["u"][0] + dy * b["u"][1]
                ly = dx * b["v"][0] + dy * b["v"][1]
                near = min(near, math.hypot(max(abs(lx) - b["hw"], 0.0),
                                            max(abs(ly) - b["hh"], 0.0)))
            worst[st["id"]] = min(worst.get(st["id"], 1e9 + CASING_HALF),
                                  near - CASING_HALF)
    return worst


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("spec", nargs="?", default=DEFAULT_SPEC,
                    help="spec.json to census (default: the repo's)")
    ap.add_argument("--corridors", default=None,
                    help="comma-separated corridor ids (default: all)")
    args = ap.parse_args()
    cors = set(args.corridors.split(",")) if args.corridors else None
    worst = census(args.spec, cors)
    print("label-vs-stroke, corridors %s (casing half-width %g)"
          % (",".join(sorted(cors)) if cors else "all", CASING_HALF))
    for sid in sorted(worst, key=lambda s: worst[s]):
        print("   %-40s clearance %+7.2f px%s"
              % (sid, worst[sid], "   <-- OVERLAP" if worst[sid] < 0 else ""))
    print("   overlaps: %d of %d labels"
          % (sum(1 for v in worst.values() if v < 0), len(worst)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
