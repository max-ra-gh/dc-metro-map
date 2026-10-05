#!/usr/bin/env python3
"""Counts label-vs-label and label-vs-marker overlaps in the resolved map.

Each line of type is modelled as an oriented box: for straight and radial text
the box is the text advance by the block height turned by `rotate`; for curved
text it is the box of the arc's CHORD, padded on both sides by the sagitta
r(1 - cos(dtheta/2)), which is the most the glyph run bows away from the chord.
That padding is why curved counts are APPROXIMATE and read slightly high.
"""
import argparse
import json
import math
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SPEC = os.path.join(REPO, "spec.json")
sys.path.insert(0, REPO)
import generate as g  # noqa: E402

CAP = 0.73          # cap height as a fraction of the type size
DESC = 0.12         # descender


def load(spec_path):
    sp = g.Spec(json.load(open(spec_path, encoding="utf-8")))
    _resolved, placements = g.resolve_all(sp)
    return sp, placements


def box(centre, ux, uy, half_w, half_h):
    return {"c": centre, "u": (ux, uy), "v": (-uy, ux), "hw": half_w, "hh": half_h}


def proj(b, axis):
    cx = b["c"][0] * axis[0] + b["c"][1] * axis[1]
    rad = (abs(b["u"][0] * axis[0] + b["u"][1] * axis[1]) * b["hw"]
           + abs(b["v"][0] * axis[0] + b["v"][1] * axis[1]) * b["hh"])
    return cx - rad, cx + rad


def boxes_overlap(a, b):
    for axis in (a["u"], a["v"], b["u"], b["v"]):
        a0, a1 = proj(a, axis)
        b0, b1 = proj(b, axis)
        if a1 <= b0 or b1 <= a0:
            return False
    return True


def box_circle_overlap(b, p, r):
    dx, dy = p[0] - b["c"][0], p[1] - b["c"][1]
    lx = dx * b["u"][0] + dy * b["u"][1]
    ly = dx * b["v"][0] + dy * b["v"][1]
    cx = max(-b["hw"], min(b["hw"], lx))
    cy = max(-b["hh"], min(b["hh"], ly))
    return math.hypot(lx - cx, ly - cy) < r


def label_boxes(sp, st, lp):
    """One box per line of type."""
    out = []
    if lp["curve"] != "concentric":          # straight and radial are both flat
        a = math.radians(lp["rotate"])
        u = (math.cos(a), math.sin(a))
        v = (-math.sin(a), math.cos(a))
        x, y = lp["x"], lp["y"]
        for text, size, _weight, dy in g.label_lines(sp, st, lp["wrap_dy"]):
            x += dy * v[0]
            y += dy * v[1]
            w = g.text_advance(text, size)
            s = {"start": 0.0, "middle": -0.5, "end": -1.0}[lp["align"]]
            bx = x + (s + 0.5) * w * u[0] + (DESC - CAP) / 2.0 * size * v[0]
            by = y + (s + 0.5) * w * u[1] + (DESC - CAP) / 2.0 * size * v[1]
            out.append(box((bx, by), u[0], u[1], w / 2.0, (CAP + DESC) * size / 2.0))
        return out
    cx, cy = sp.centre
    for arc in g.label_arcs(sp, st, lp):
        r = arc["r"]
        deg = math.degrees(arc["width"] / r)           # the text run, without the pad
        # the arc path already runs in the reading direction: walk it from `from`
        t0 = math.degrees(math.atan2(arc["from"][1] - cy, arc["from"][0] - cx))
        t1 = math.degrees(math.atan2(arc["to"][1] - cy, arc["to"][0] - cx))
        d = (t1 - t0 + 540.0) % 360.0 - 180.0
        step = d / arc["span_deg"] if arc["span_deg"] else 1.0
        frac = {"start": 0.0, "middle": 0.5, "end": 1.0}[lp["align"]]
        pad_deg = arc["span_deg"] - deg
        a0 = t0 + step * pad_deg * frac
        p0 = g.polar(sp.centre, r, a0)
        p1 = g.polar(sp.centre, r, a0 + step * deg)
        ux, uy = p1[0] - p0[0], p1[1] - p0[1]
        ln = math.hypot(ux, uy) or 1.0
        ux, uy = ux / ln, uy / ln
        sag = r * (1.0 - math.cos(math.radians(deg) / 2.0))
        mid = ((p0[0] + p1[0]) / 2.0, (p0[1] + p1[1]) / 2.0)
        size = arc["size"]
        # the type sits between the baseline and cap height, on the outward side
        # of the baseline in the upper hemisphere and inward in the lower one
        nx, ny = -uy, ux
        out.append(box((mid[0] + (DESC - CAP) / 2.0 * size * nx,
                        mid[1] + (DESC - CAP) / 2.0 * size * ny),
                       ux, uy, ln / 2.0, (CAP + DESC) * size / 2.0 + sag))
    return out


def markers(sp, placements):
    k = sp.const
    out = []
    for st in sp.data["stations"]:
        pl = placements[st["id"]]
        if st["marker"] == "interchange":
            r = (k["interchange_dot"]["outer_r"]
                 + k["interchange_dot"]["outer_stroke_width"] / 2)
        elif st["marker"] == "terminal":
            r = k["terminal_dot"]["r"] + k["terminal_dot"]["stroke_width"] / 2
        elif st["marker"] == "capsule":
            r = k["capsule"]["casing_stroke_width"] / 2
        else:
            r = k["station_dot"]["r"] + k["station_dot"]["stroke_width"] / 2
        pts = (list(pl["capsule"]) if "capsule" in st["at"]
               else [d["p"] for d in pl["dots"]])
        for p in pts:
            out.append((st["id"], p, r))
    return out


def census(spec_path):
    """(label-label pairs, label-marker pairs) for one spec."""
    sp, placements = load(spec_path)
    boxes = []
    for st in sp.data["stations"]:
        lp = g.label_placement(sp, st, placements[st["id"]])
        for b in label_boxes(sp, st, lp):
            boxes.append((st["id"], b))
    label_label = set()
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            if boxes[i][0] == boxes[j][0]:
                continue
            if boxes_overlap(boxes[i][1], boxes[j][1]):
                label_label.add(tuple(sorted((boxes[i][0], boxes[j][0]))))
    label_marker = set()
    for sid, b in boxes:
        for mid, p, r in markers(sp, placements):
            if mid != sid and box_circle_overlap(b, p, r):
                label_marker.add((sid, mid))
    return label_label, label_marker


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("spec", nargs="?", default=DEFAULT_SPEC,
                    help="spec.json to census (default: the repo's)")
    args = ap.parse_args()
    ll, lm = census(args.spec)
    print("%s" % args.spec)
    print("  label-label  %3d   label-marker %3d" % (len(ll), len(lm)))
    print("  label-label  : %s" % ", ".join("%s/%s" % p for p in sorted(ll)))
    print("  label-marker : %s" % ", ".join("%s>%s" % p for p in sorted(lm)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
