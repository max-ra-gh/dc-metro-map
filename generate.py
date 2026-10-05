#!/usr/bin/env python3
"""Generate DC-Metro.svg from spec.json.

Python 3, stdlib only, deterministic: the same spec.json always produces a
byte-identical SVG. spec.json is the single source of truth; nothing about the
map is hard-coded here beyond the resolution rules SPEC.md states.

    ./generate.py                  write DC-Metro.svg
    ./generate.py --check          run every check, write nothing
    ./generate.py --census         print the counts SPEC.md 10 quotes
    ./generate.py --layers geo,lines,stations
    ./generate.py --force          write the SVG even with checks failing
    ./generate.py --pdf --png      also write a PDF (text as outlines) and a PNG;
                                   these two need Inkscape and the map's font
"""

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys

SPEC_PATH = "spec.json"
OUT_PATH = "DC-Metro.svg"
LAYER_GROUPS = ("geo", "lines", "stations", "labels", "legend")


# --------------------------------------------------------------------------
# formatting
# --------------------------------------------------------------------------

def fmt(v):
    if abs(v) < 5e-5:
        return "0"
    s = "%.4f" % v
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


def pt(p):
    return "%s %s" % (fmt(p[0]), fmt(p[1]))


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def attrs(pairs):
    return "".join(' %s="%s"' % (k, esc(v)) for k, v in pairs if v is not None)


# --------------------------------------------------------------------------
# geometry
# --------------------------------------------------------------------------

def polar(centre, r, deg):
    a = math.radians(deg)
    return (centre[0] + r * math.cos(a), centre[1] + r * math.sin(a))


def angle_of(centre, p):
    d = math.degrees(math.atan2(p[1] - centre[1], p[0] - centre[0]))
    return d + 360.0 if d < 0 else d


def axis_basis(axis):
    a = math.radians(axis["angle"])
    return (math.cos(a), math.sin(a)), (-math.sin(a), math.cos(a))


def axis_point(axis, n, s):
    d, nrm = axis_basis(axis)
    o = axis["origin"]
    return (o[0] + s * d[0] + n * nrm[0], o[1] + s * d[1] + n * nrm[1])


def axis_offset_of(axis, p):
    """Signed perpendicular slot offset of an arbitrary point from the axis."""
    _, nrm = axis_basis(axis)
    o = axis["origin"]
    return (p[0] - o[0]) * nrm[0] + (p[1] - o[1]) * nrm[1]


def slot_line_ring_s(axis, n, centre, r):
    """The two `s` values where the slot line of `axis` cuts a ring.

    Returns (s_pos, s_neg) -- the larger root first. `s_pos` is the default
    branch; endpoint specs say "branch": "neg" for the other one.
    """
    d, _ = axis_basis(axis)
    base = axis_point(axis, n, 0.0)
    q = (base[0] - centre[0], base[1] - centre[1])
    b = q[0] * d[0] + q[1] * d[1]
    c = q[0] * q[0] + q[1] * q[1] - r * r
    disc = b * b - c
    if disc < 0:
        raise SpecError("slot line of axis at n=%g does not reach r=%g" % (n, r))
    root = math.sqrt(disc)
    return (-b + root, -b - root)


def slot_line_intersection(a1, n1, a2, n2):
    d1, _ = axis_basis(a1)
    d2, _ = axis_basis(a2)
    p1 = axis_point(a1, n1, 0.0)
    p2 = axis_point(a2, n2, 0.0)
    den = d1[0] * d2[1] - d1[1] * d2[0]
    if abs(den) < 1e-12:
        raise SpecError("parallel axes cannot intersect")
    t = ((p2[0] - p1[0]) * d2[1] - (p2[1] - p1[1]) * d2[0]) / den
    return (p1[0] + t * d1[0], p1[1] + t * d1[1])


def arc_sweep(theta0, theta1, direction):
    """(delta, large_arc_flag, sweep_flag) for an arc from theta0 to theta1."""
    if direction == "cw":
        delta = theta1 - theta0
        while delta <= 0:
            delta += 360.0
        return delta, 1 if delta > 180.0 else 0, 1
    if direction == "ccw":
        delta = theta0 - theta1
        while delta <= 0:
            delta += 360.0
        return delta, 1 if delta > 180.0 else 0, 0
    raise SpecError("unknown arc direction %r" % direction)


class SpecError(Exception):
    pass


# --------------------------------------------------------------------------
# spec model
# --------------------------------------------------------------------------

class Spec:
    def __init__(self, data):
        self.data = data
        self.centre = tuple(data["canvas"]["centre"])
        self.R = float(data["grid"]["R"])
        self.rings = {r["id"]: r for r in data["rings"]}
        self.axes = data["axes"]
        self.runs = data["runs"]
        self.corridors = data["corridors"]
        self.lines = {ln["id"]: ln for ln in data["lines"]}
        self.const = data["constants"]
        self.refs = {"ring": set(), "axis": set(), "line": set(), "corridor": set(),
                     "run": set()}

    def ring(self, rid):
        if rid not in self.rings:
            raise SpecError("unknown ring %r" % rid)
        self.refs["ring"].add(rid)
        return float(self.rings[rid]["r"])

    def axis(self, aid):
        if aid not in self.axes:
            raise SpecError("unknown axis %r" % aid)
        self.refs["axis"].add(aid)
        return self.axes[aid]

    def slot(self, aid, name):
        """A slot is either a name in the axis's slots map or a literal offset."""
        if isinstance(name, (int, float)):
            return float(name)
        ax = self.axis(aid)
        if name not in ax["slots"]:
            raise SpecError("axis %r has no slot %r" % (aid, name))
        return float(ax["slots"][name])

    def run(self, rid):
        if rid not in self.runs:
            raise SpecError("unknown run %r" % rid)
        self.refs["run"].add(rid)
        return self.runs[rid]

    def corridor(self, cid):
        if cid not in self.corridors:
            raise SpecError("unknown corridor %r" % cid)
        self.refs["corridor"].add(cid)
        return self.corridors[cid]

    def line(self, lid):
        if lid not in self.lines:
            raise SpecError("unknown line %r" % lid)
        self.refs["line"].add(lid)
        return self.lines[lid]

# --------------------------------------------------------------------------
# endpoint resolution
# --------------------------------------------------------------------------

def resolve_endpoint(sp, spec_ep, axis_id, slot_n, prev):
    """Resolve an endpoint spec on the current axis/slot line to a point."""
    if "point" in spec_ep:
        return tuple(spec_ep["point"])
    if spec_ep.get("prev"):
        if prev is None:
            raise SpecError("`prev` endpoint with no previous point")
        return prev
    if "s" in spec_ep and "axis" not in spec_ep:
        return axis_point(sp.axis(axis_id), slot_n, float(spec_ep["s"]))
    if "ring" in spec_ep:
        r = sp.ring(spec_ep["ring"])
        s_pos, s_neg = slot_line_ring_s(sp.axis(axis_id), slot_n, sp.centre, r)
        s = s_neg if spec_ep.get("branch") == "neg" else s_pos
        return axis_point(sp.axis(axis_id), slot_n, s)
    if "axis" in spec_ep:
        other = sp.axis(spec_ep["axis"])
        n2 = sp.slot(spec_ep["axis"], spec_ep.get("slot", "mid"))
        if "s" in spec_ep:
            return axis_point(other, n2, float(spec_ep["s"]))
        return slot_line_intersection(sp.axis(axis_id), slot_n, other, n2)
    raise SpecError("unrecognised endpoint spec %r" % (spec_ep,))


def resolve_arc_target_angle(sp, spec_ep, ring_r):
    if "angle" in spec_ep:
        return float(spec_ep["angle"])
    if "axis" in spec_ep:
        ax = sp.axis(spec_ep["axis"])
        n = sp.slot(spec_ep["axis"], spec_ep.get("slot", "mid"))
        s_pos, s_neg = slot_line_ring_s(ax, n, sp.centre, ring_r)
        s = s_neg if spec_ep.get("branch") == "neg" else s_pos
        return angle_of(sp.centre, axis_point(ax, n, s))
    if "point" in spec_ep:
        return angle_of(sp.centre, tuple(spec_ep["point"]))
    raise SpecError("unrecognised arc target %r" % (spec_ep,))


# --------------------------------------------------------------------------
# lines
# --------------------------------------------------------------------------

def resolve_line(sp, line):
    """Return (path_d, vertices, diagnostics) for one line."""
    cmds = []
    verts = []
    diag = []
    cur = None
    cur_axis = None

    for idx, seg in enumerate(line["segments"]):
        kind = seg["kind"]
        if kind == "straight":
            axis_id = seg["axis"]
            n = sp.slot(axis_id, seg.get("slot", "mid"))
            start = resolve_endpoint(sp, seg["from"], axis_id, n, cur)
            if cur is not None:
                diag.append(("continuity", idx, math.dist(cur, start)))
                diag.append(("on-slot-line", idx,
                             abs(axis_offset_of(sp.axis(axis_id), cur) - n)))
            end = resolve_endpoint(sp, seg["to"], axis_id, n, start)
            if cur is None:
                cmds.append("M " + pt(start))
                verts.append(start)
            cmds.append("L " + pt(end))
            verts.append(end)
            cur, cur_axis = end, axis_id
        elif kind == "arc":
            if cur is None:
                raise SpecError("arc cannot open a path")
            r = sp.ring(seg["ring"])
            diag.append(("arc-on-ring", idx,
                         abs(math.dist(cur, sp.centre) - r)))
            t0 = angle_of(sp.centre, cur)
            t1 = resolve_arc_target_angle(sp, seg["to"], r)
            delta, laf, sf = arc_sweep(t0, t1, seg["dir"])
            end = polar(sp.centre, r, t0 + delta if seg["dir"] == "cw" else t0 - delta)
            cmds.append("A %s %s 0 %d %d %s" % (fmt(r), fmt(r), laf, sf, pt(end)))
            verts.append(end)
            cur = end
            cur_axis = None
        elif kind == "riser":
            axis_id = seg["axis"]
            ax = sp.axis(axis_id)
            n1 = sp.slot(axis_id, seg["to_slot"])
            n0 = axis_offset_of(ax, cur)
            _, nrm = axis_basis(ax)
            end = (cur[0] + (n1 - n0) * nrm[0], cur[1] + (n1 - n0) * nrm[1])
            cmds.append("L " + pt(end))
            verts.append(end)
            cur, cur_axis = end, axis_id
        elif kind == "join":
            tgt = seg["to"]
            axis_id = tgt.get("axis", cur_axis)
            n = sp.slot(axis_id, tgt.get("slot", "mid")) if axis_id else 0.0
            end = resolve_endpoint(sp, tgt, axis_id, n, cur)
            cmds.append("L " + pt(end))
            verts.append(end)
            cur, cur_axis = end, axis_id
        else:
            raise SpecError("unknown segment kind %r" % kind)

    return " ".join(cmds), verts, diag


# --------------------------------------------------------------------------
# stations
# --------------------------------------------------------------------------

def resolve_axis_placement(sp, at, default_slot="mid"):
    axis_id = at["axis"]
    ax = sp.axis(axis_id)
    n = sp.slot(axis_id, at.get("slot", default_slot))
    if "at_ring" in at:
        r = sp.ring(at["at_ring"])
        s_pos, s_neg = slot_line_ring_s(ax, n, sp.centre, r)
        s = s_neg if at.get("branch") == "neg" else s_pos
    else:
        s = float(at["s"])
    return axis_point(ax, n, s)


def resolve_station(sp, st):
    """Return {'dots': [...], 'anchor': (x,y), 'kind': ..., extras}."""
    at = st["at"]
    if "ring" in at:
        r = sp.ring(at["ring"])
        p = polar(sp.centre, r, float(at["angle"]))
        return {"dots": [{"p": p, "line": st["lines"][0] if st["lines"] else None}],
                "anchor": p, "normal": ("radial", float(at["angle"]))}
    if "run" in at:
        run = sp.run(at["run"])
        i = int(at["i"])
        if not 0 <= i < int(run["count"]):
            raise SpecError("%s: run index %d out of range for %s" %
                            (st["id"], i, at["run"]))
        axis_id = run["axis"]
        n = sp.slot(axis_id, at.get("slot", run.get("slot", "mid")))
        s = float(run["s0"]) + i * float(run["step"])
        p = axis_point(sp.axis(axis_id), n, s)
        return {"dots": [{"p": p, "line": st["lines"][0] if st["lines"] else None}],
                "anchor": p, "normal": ("axis", axis_id)}
    if "axis" in at:
        p = resolve_axis_placement(sp, at)
        return {"dots": [{"p": p, "line": st["lines"][0] if st["lines"] else None}],
                "anchor": p, "normal": ("axis", at["axis"])}
    if "dots" in at:
        dots = [{"p": resolve_axis_placement(sp, d), "line": d.get("line")}
                for d in at["dots"]]
        cx = sum(d["p"][0] for d in dots) / len(dots)
        cy = sum(d["p"][1] for d in dots) / len(dots)
        return {"dots": dots, "anchor": (cx, cy), "normal": None}
    if "capsule" in at:
        cap = at["capsule"]
        axis_id = cap["axis"]
        n = sp.slot(axis_id, cap.get("slot", "mid"))
        a = resolve_endpoint(sp, cap["from"], axis_id, n, None)
        b = resolve_endpoint(sp, cap["to"], axis_id, n, None)
        return {"dots": [{"p": a, "line": None}, {"p": b, "line": None}],
                "anchor": ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0),
                "normal": ("axis", axis_id), "capsule": (a, b)}
    raise SpecError("%s: unrecognised `at` %r" % (st["id"], at))


def tick_direction(sp, placed):
    kind = placed["normal"]
    if kind[0] == "radial":
        a = math.radians(kind[1])
        return (math.cos(a), math.sin(a))
    _, nrm = axis_basis(sp.axis(kind[1]))
    return nrm


def upright_rotate(deg):
    """`deg` normalised into (-90, 90], the half-turn that keeps a line of type
    upright. The 180 deg flip leaves the line the text sits on alone and only
    reverses the direction it is read in, so a caller that needs the text to
    stay on one side of its anchor has to answer that with `text-anchor`."""
    r = deg % 180.0
    return r - 180.0 if r > 90.0 else r


def tangential_rotate(sp, axis_id):
    """Label rotation that reads tangentially (perpendicular to the radial
    spoke `axis_id`), normalised to (-90, 90] so text is never upside-down."""
    return upright_rotate(sp.axis(axis_id)["angle"] - 270.0)


LABEL_KEYS = ("side", "distance", "ds", "align", "rotate", "wrap_dy", "curve")

CURVE_MODES = ("concentric", "radial", "straight")

# How many passes the label offset gets to settle (label_placement): `distance`
# is a gap measured along the type's own down, and under the curved modes that
# down is a property of the point being solved for.
LABEL_OFFSET_PASSES = 12

# Advance widths in 1/1000 em for every character the 98 station labels use,
# taken from the Helvetica-Bold AFM table as a proxy for Inter Bold (both run
# ~0.58 em over this caps/lowercase mix). One table serves both type sizes: the
# 9 px subtitles are Inter Regular, which is narrower than the bold table, so
# the estimate stays generous there too. ADVANCE_SAFETY covers the difference
# between the proxy and the real face; a too-short arc would truncate the text,
# a too-long one costs nothing.
ADVANCE_PER_MILLE = {
    " ": 278, "'": 238, "’": 238, "-": 333, "/": 278, ".": 278,
    "0": 556, "1": 556, "2": 556, "3": 556, "4": 556, "5": 556, "6": 556,
    "7": 556, "8": 556, "9": 556,
    "A": 722, "B": 722, "C": 722, "D": 722, "E": 667, "F": 611, "G": 778,
    "H": 722, "I": 278, "J": 556, "K": 722, "L": 611, "M": 833, "N": 722,
    "O": 778, "P": 667, "Q": 778, "R": 722, "S": 667, "T": 611, "U": 722,
    "V": 667, "W": 944, "X": 667, "Y": 667, "Z": 611,
    "a": 556, "b": 611, "c": 556, "d": 611, "e": 556, "f": 333, "g": 611,
    "h": 611, "i": 278, "j": 278, "k": 556, "l": 278, "m": 889, "n": 611,
    "o": 611, "p": 611, "q": 611, "r": 389, "s": 556, "t": 333, "u": 611,
    "v": 556, "w": 778, "x": 556, "y": 556, "z": 500,
}
ADVANCE_DEFAULT = 1000
ADVANCE_SAFETY = 1.08


def text_advance(text, size_px):
    """Conservative advance of `text` at `size_px`, in user units."""
    mille = sum(ADVANCE_PER_MILLE.get(ch, ADVANCE_DEFAULT) for ch in text)
    return mille / 1000.0 * float(size_px) * ADVANCE_SAFETY


def label_axis(sp, st):
    """The corridor axis a station's label is measured against, or None for a
    ring placement (which is measured radially)."""
    at = st["at"]
    if "run" in at:
        return sp.run(at["run"])["axis"]
    if "capsule" in at:
        return at["capsule"]["axis"]
    if "axis" in at:
        return at["axis"]
    if "dots" in at:
        return at["dots"][0]["axis"]
    return None


def label_default(sp, st):
    """Corridor default, then the run's, then the station's `override`."""
    d = dict(sp.corridor(st["corridor"])["label_default"])
    run_id = st["at"].get("run")
    if run_id:
        d.update(sp.run(run_id).get("label_default", {}))
    d.update(st["label"].get("override", {}))
    bad = [k for k in d if k not in LABEL_KEYS]
    if bad:
        raise SpecError("%s: unknown label key(s) %s" % (st["id"], sorted(bad)))
    return d


def label_lines(sp, st, wrap_dy):
    """Every line of a label as (text, size_px, weight, dy), `dy` the advance
    from the previous line and 0 for the first."""
    prim = sp.const["fonts"]["primary"]
    sec = sp.const["fonts"]["secondary"]
    lines = [(part, prim["size_px"], prim["weight"], 0.0 if i == 0 else wrap_dy)
             for i, part in enumerate(st["label"]["title"])]
    lines += [(part, sec["size_px"], sec["weight"],
               sec["first_dy"] if i == 0 else sec["subsequent_dy"])
              for i, part in enumerate(st["label"].get("subtitle", []))]
    return lines


def label_extra(sp, st, wrap_dy):
    """Total advance of every label line after the first."""
    return sum(dy for _, _, _, dy in label_lines(sp, st, wrap_dy))


def outward_dot(sp, anchor, u):
    """Rule A: cos of the angle between the outward radial at the station and
    the reading direction. > 0 means reading outward is reading forward."""
    v = (anchor[0] - sp.centre[0], anchor[1] - sp.centre[1])
    d = math.hypot(v[0], v[1])
    if d < 1e-9:
        return 0.0
    return (v[0] * u[0] + v[1] * u[1]) / d


def extreme_dot(placed, perp, nsign):
    """The platform dot furthest along the side the label sits on.

    A capsule's two dots both sit ON the axis centreline, so their projections
    are equal in exact arithmetic and the winner would otherwise be decided by
    the last bit of a cosine. The tie is therefore broken explicitly, to the
    FIRST dot -- a capsule's `from` end -- so that a label measured off one of
    them (Metro Center's, off the lower circle) keeps its origin whatever the
    rounding does."""
    pts = [d["p"] for d in placed["dots"]]
    if len(pts) == 1:
        return pts[0]
    proj = [nsign * (p[0] * perp[0] + p[1] * perp[1]) for p in pts]
    best = max(proj)
    return next(p for p, s in zip(pts, proj) if s > best - 1e-9)


def label_basis(sp, st, curve=None, anchor=None):
    """(perp, along) of the station's corridor frame: the axis normal and
    direction, or radially outward and tangential for a ring placement.

    `radial` type is SET on the outward radial, so its offset is taken there
    too, whatever frame the base point came from; SPEC.md 6.1 states the rule
    and 6.7 the mode. The two stations it decides are the corridor-frame pair
    on the New Carrollton branch: Minnesota Av, whose `nc-diagonal` normal
    points at 45 deg, and New Carrollton, whose `nc-exit` normal points at
    67.1 -- both close enough to the TANGENT of the ring the label is meant to
    stand off that the offset slid the type along the ring instead of away
    from it, taking Minnesota Av's block off the corner and New Carrollton's
    across the Beltway. A ring placement has no axis and already resolved
    radially; this only adds the axis-placed radial labels to it."""
    aid = label_axis(sp, st)
    if curve == "radial" or aid is None:
        if anchor is not None:
            a = math.radians(angle_of(sp.centre, anchor))
        else:
            a = math.radians(float(st["at"]["angle"]))
        return (math.cos(a), math.sin(a)), (-math.sin(a), math.cos(a))
    d, nrm = axis_basis(sp.axis(aid))
    return nrm, d


def marker_outer_r(sp, st):
    """The widest ink the station's own marker puts down, as a half-width from
    its dot -- or, for a capsule, from its bar's centreline."""
    cst = sp.const
    marker = st["marker"]
    if marker == "interchange":
        c = cst["interchange_dot"]
        return float(c["outer_r"]) + float(c["outer_stroke_width"]) / 2.0
    if marker == "terminal":
        c = cst["terminal_dot"]
        return float(c["r"]) + float(c["stroke_width"]) / 2.0
    if marker == "capsule":
        return float(cst["capsule"]["casing_stroke_width"]) / 2.0
    c = cst["station_dot"]
    return float(c["r"]) + float(c["stroke_width"]) / 2.0


def band_offsets(sp, st, placed):
    """Perpendicular offsets, from the corridor frame's origin, of the centreline
    of every line in the band the station sits in.

    In the axis frame that is the slot each serving line runs in, so a station
    reads the whole band it belongs to and not just its own dot; a line with no
    slot of its own on that axis (the Red spokes, the Tysons leg) falls back to
    the station's own dots. In the ring frame the station's line runs through its
    dot, so the offset is 0 -- a concentric NEIGHBOUR of the station's own ring is
    deliberately not counted (SPEC.md 6.2)."""
    aid = label_axis(sp, st)
    if aid is None:
        return [0.0]
    slots = sp.axes[aid]["slots"]
    offs = [float(slots[lid]) for lid in st["lines"] if lid in slots]
    if offs:
        return offs
    ax = sp.axis(aid)
    return [axis_offset_of(ax, d["p"]) for d in placed["dots"]]


def obstruction_extent(sp, st, placed, perp, nsign, p0):
    """How far the ink at a station protrudes along the label's offset direction,
    measured from the corridor frame's origin: the further of the station's own
    marker edge (on the extreme dot on the label's side) and the outer casing
    edge of the band it sits in. `distance` is the gap beyond it."""
    w = (nsign * perp[0], nsign * perp[1])
    ex = extreme_dot(placed, perp, nsign)
    marker = ((ex[0] - p0[0]) * w[0] + (ex[1] - p0[1]) * w[1]
              + marker_outer_r(sp, st))
    band = (max(nsign * o for o in band_offsets(sp, st, placed))
            + float(sp.const["line"]["casing_width"]) / 2.0)
    return max(marker, band)


def type_down(sp, p, rotate, curve):
    """The label's own down direction at base point `p`: the radial, signed by
    hemisphere, for concentric type, and the normal of `rotate` otherwise.

    The hemisphere test is `label_arcs`' own (`y <= centre` reads as upper, where
    down is inward); the two must agree, or the gap would be solved against a
    direction the arcs do not use."""
    if curve == "concentric":
        vx, vy = p[0] - sp.centre[0], p[1] - sp.centre[1]
        d = math.hypot(vx, vy)
        if d < 1e-9:
            raise SpecError("a concentric label cannot sit on the map centre")
        sgn = -1.0 if vy <= 0.0 else 1.0
        return (sgn * vx / d, sgn * vy / d)
    a = math.radians(rotate)
    return (-math.sin(a), math.cos(a))


def reading_dir(dwn):
    """A line of type's reading direction, recovered from its down direction:
    the two are a right-handed pair, so reading is a quarter turn back."""
    return (dwn[1], -dwn[0])


def label_align(sp, d, side, curve, base, dwn):
    """Which end of the block of type sits on the base point.

    A declared `align` wins. `radial` re-derives it from the TRUE radial reading
    direction, so the words run outward of the marker on both halves of the map
    even though the upright flip reverses that direction on the left one
    (SPEC.md 6.7); every other mode takes it from the side the label sits on.
    """
    if "align" in d:
        return d["align"]
    if curve == "radial":
        return "start" if outward_dot(sp, base, reading_dir(dwn)) >= 0.0 else "end"
    return "start" if side == "right" else "end"


def aim_degenerate(sp, curve, alng, dwn, align):
    """True where a slide along the corridor cannot aim the label at all.

    A MIDDLE-anchored block has no near end: it straddles its base point, so
    there is nothing for a slide to hug to the dot and the whole freedom is
    spent by `distance` and `ds` instead. That is the only thing a middle
    anchor is ever asked for -- words centred on their marker rather than
    finishing or starting at it -- and Arlington Cemetery and McPherson Sq,
    each centred on its own dot, are the two cases (SPEC.md 6.2).

    `concentric` type is set on a CIRCLE about the map centre, and a circle
    offset from a station never passes through it whatever the slide. The
    counterpart of aiming there is angular -- the anchor at the dot's own theta
    -- which a ring station has by construction (its offset is radial) and a
    spoke station cannot have at all (its offset is not). So concentric labels
    are left where the gap puts them; SPEC.md 6.7 says so.

    The straight modes need the type OBLIQUE to its corridor, measured by the
    sine and the cosine of the angle between them -- `along.down` and
    `along.reading` -- with `constants.label.aim_degenerate_dot` the magnitude
    below which either is declared degenerate and no slide is made. Type reading
    ALONG its corridor (`along.down` -> 0) is the end rule's own denominator; it
    is already square beside its marker, and sliding it only slides it down its
    own reading line: L'Enfant Plaza on the east trunk and New Carrollton on
    its radial exit. Type reading ACROSS it (`along.reading` -> 0) is set square
    across the corridor, so its `distance` -- which acts along the reading
    direction there -- has already answered where the words start, and its
    offset carries them nowhere along the corridor to correct: the seven
    `outer-green` radial labels, which read across their ring, and Rosslyn and
    Smithsonian on the core arcs; their slide is 0 under either start rule.
    """
    if curve == "concentric" or align == "middle":
        return True
    up = reading_dir(dwn)
    eps = float(sp.const["label"]["aim_degenerate_dot"])
    return (abs(alng[0] * dwn[0] + alng[1] * dwn[1]) < eps
            or abs(alng[0] * up[0] + alng[1] * up[1]) < eps)


def aim_slide(sp, curve, p0, n, perp, alng, dwn, lift, align, aim):
    """The slide ALONG the corridor that hugs a label's NEAR END to its own
    station's dot, leaving the gap across the corridor untouched.

    The gap fixes only where the base point sits ACROSS the corridor; where it
    sits along it is free, and a slanted rank has to spend that freedom or the
    words sit somewhere other than beside the station they name. Which end is
    the near one is exactly what `text-anchor` says, and the two answers are
    different geometry:

    * END-anchored, the block finishes on the base point, so the near end is the
      base point itself and the type runs back up the reading line away from the
      station. Aiming it is putting that READING LINE through the dot centre --
      solve `(base - dot).down = 0`. `W` is the case that shows it: type turned
      -52.25 deg and hung 36 px below a horizontal band reads back up into the
      corridor 28 px further on, so a label that finishes square under its dot
      aims a whole half-span past it. The slide is the drop divided by the
      tangent of the angle between the type and the corridor.
    * START-anchored, the block BEGINS on the base point and runs away from it,
      so the same reading-line rule solves for the far intersection: on `E` --
      type turned 37.75 deg above a horizontal corridor -- it stands the anchor
      66 px down the line PAST the dot and shoves the whole rank outward, worst at
      the map edge (Downtown Largo, Morgan Blvd, Addison Rd). What a start
      anchor wants instead is to begin DIRECTLY BELOW the dot in the corridor
      frame -- solve `(base - dot).along = 0` -- so the words start square under
      the marker they name and `distance` alone says how far off it they sit.
      The dot's perpendicular foot on the baseline is not that point either: it
      is measured across the type rather than across the corridor, and on `E` it
      leaves the anchors ~25 px back up the corridor from their stations.
      Middle-anchored type keeps the reading-line rule: its near end is neither
      end, and the line through the dot is what centres it on one.

    Both rules are solved on the FIRST line's optical centre, `base - lift.down`
    with `lift` the block-centring lift of `extra/2`, not on the base point: the
    base point is the centre of the WHOLE block, which a wrapped label carries
    half its extra advance further down, and a slanted reading line taken from
    there lands short of the dot by that drop divided by the tangent. `W` is the
    case: Vienna, Dunn Loring and West Falls Church each carry a subtitle, and
    end-aimed off the block centre their titles sat 7 px back down the corridor
    from their own dots while the five single-line labels in the same rank
    aimed true. Aiming the title line instead puts a wrapped label exactly where
    a single-line one at the same station would sit, which is what a rank wants;
    `lift` is 0 for a single-line label, so nothing else moves.

    `q` is the uncorrected aimed point measured from the dot. The start rule
    divides by `along.along` = 1, so it has no denominator of its own; the
    degeneracies that get 0 are `aim_degenerate`'s either way, and they are about
    what a slide along the corridor can usefully do at all.
    """
    if aim_degenerate(sp, curve, alng, dwn, align):
        return 0.0
    w = alng if align == "start" else dwn
    q = (p0[0] + n * perp[0] - lift * dwn[0] - aim[0],
         p0[1] + n * perp[1] - lift * dwn[1] - aim[1])
    return -(q[0] * w[0] + q[1] * w[1]) / (alng[0] * w[0] + alng[1] * w[1])


def label_origin(sp, st, placed, perp, nsign):
    """Where the obstruction extent is measured from: the station's foot on the
    axis centreline, or the dot itself for a ring placement."""
    aid = label_axis(sp, st)
    if aid is None:
        return placed["anchor"]
    ax = sp.axis(aid)
    d, _ = axis_basis(ax)
    p = extreme_dot(placed, perp, nsign)
    o = ax["origin"]
    s = (p[0] - o[0]) * d[0] + (p[1] - o[1]) * d[1]
    return (o[0] + s * d[0], o[1] + s * d[1])


def label_placement(sp, st, placed):
    """Resolve one label's frame: base point, anchor point, align and rotate.

    `distance` is a GAP, not an offset: the white paper between the ink at the
    station and the leading edge of the block of type, along the label's own
    offset direction. Three terms are solved out of it before the base point is
    placed -- how far the station's own marker protrudes, how far the outer
    casing edge of the band it sits in protrudes (`obstruction_extent`), and how
    much of its own optical half-depth the block spends across the corridor. All
    three vary station by station, which is the point: one number in the spec
    then reads as the same gap under a plain dot, an interchange ring, a terminal
    circle and a capsule alike, on the mean slot or on a far one.

    The gap answers where the base point sits ACROSS the corridor. Where it sits
    ALONG the corridor is `ds` plus `aim_slide`, the slide that hugs the near end
    of the label's FIRST line to its own station's dot — the one thing the offset
    alone cannot get right, because type turned away from the corridor normal
    walks sideways as it comes back up to the corridor. Which end is near is
    `align`'s answer, so it is resolved inside the fixed point too, alongside the
    gap and the slide:
    `radial` reads both its turn and its `align` off the base point, so the slide
    moves the very directions it is solved against.

    `curve` = "radial" is the one mode that re-derives `rotate` after the fact.
    The base point is resolved exactly as the other two modes resolve it, and
    the line of type is then laid STRAIGHT along the radius through it, at
    `upright_rotate(theta)` — the station's own angle turned into (-90, 90] so
    the glyphs never stand on their head. On the right half of the map that
    leaves the reading direction pointing outward; on the left half the 180 deg
    flip turns it inward, and `align` has to answer that or the words would run
    back over the marker. It does, because rule A re-run on the true radial
    reading direction is exactly the test needed, which is what `label_align`
    does under this mode: outward-reading gives `start` and inward-reading `end`,
    so the text block sits outward of the station either way. `lp["outward"]`
    keeps the NOMINAL dot instead, since `side` — which only signs `distance`
    here — is still derived from the corridor's declared `rotate`.
    """
    d = label_default(sp, st)
    rotate = d.get("rotate", 0.0)
    if isinstance(rotate, dict):
        rotate = tangential_rotate(sp, rotate["axis"])
    rotate = float(rotate)
    a = math.radians(rotate)
    u = (math.cos(a), math.sin(a))
    v = (-math.sin(a), math.cos(a))
    ou = outward_dot(sp, placed["anchor"], u)
    side = d.get("side") or ("right" if ou >= 0.0 else "left")
    if side not in ("left", "right"):
        raise SpecError("%s: label side %r is not 'left' or 'right'" % (st["id"], side))
    curve = d.get("curve", sp.const["label"]["curve_default"])
    if curve not in CURVE_MODES:
        raise SpecError("%s: label curve %r is not one of %s"
                        % (st["id"], curve, list(CURVE_MODES)))
    perp, alng = label_basis(sp, st, curve, placed["anchor"])
    # `side` is read off the reading direction: "right" leans the offset the way
    # the type reads, which is what makes it agree with rule A. Type set ALONG
    # its own corridor is offset exactly across it, so that reading carries no
    # information and the sign falls back to the outward radial -- "right" then
    # means the block sits away from the map centre, which is the same thing
    # rule A means by it. L'Enfant Plaza, horizontal type on the horizontal east
    # trunk, is the case. If that vanishes too the tie breaks to +perp,
    # arbitrarily but totally.
    pu = perp[0] * u[0] + perp[1] * u[1]
    if abs(pu) < 1e-9:
        pu = outward_dot(sp, placed["anchor"], perp)
    nsign = (1.0 if side == "right" else -1.0) * (1.0 if pu > 0 else -1.0)
    p0 = label_origin(sp, st, placed, perp, nsign)
    ds = float(d.get("ds", 0.0))
    wrap = float(d.get("wrap_dy", sp.const["fonts"]["primary"]["wrap_dy"]))
    extra = label_extra(sp, st, wrap)
    lift = extra / 2.0
    dv = float(sp.const["label"]["baseline_dy"]) - lift
    gap = float(d.get("distance", sp.const["label"]["distance"]))
    ext = obstruction_extent(sp, st, placed, perp, nsign, p0)
    half = float(sp.const["label"]["baseline_dy"]) + lift
    # `distance` is the white paper between the ink and the LEADING EDGE of the
    # block, not between the ink and the base point. Centring puts the
    # block's optical centre on the base point, so that edge sits `half` back
    # along the type's own down -- one half cap height for every label, plus
    # half the advance of the lines a wrapped one adds. Only the component
    # ACROSS the corridor is spent, hence the projection: a spoke's concentric
    # type reads across the offset and spends nothing of it, a trunk's slanted
    # type spends four fifths. Under the two curved modes `down` is a property
    # of the base point being solved for -- the radial through it, or the turn
    # `radial` derives from it -- so the offset is a fixed point, iterated from
    # the uncompensated one. It is a hard contraction (a few px of shift barely
    # turns a radius) and settles in three or four passes; not settling is a
    # build failure rather than a silently truncated iteration.
    n = nsign * (ext + gap)
    slide = 0.0
    aim = placed["anchor"]
    for _ in range(LABEL_OFFSET_PASSES):
        base = (p0[0] + n * perp[0] + (ds + slide) * alng[0],
                p0[1] + n * perp[1] + (ds + slide) * alng[1])
        turn = rotate if curve != "radial" else upright_rotate(math.degrees(
            math.atan2(base[1] - sp.centre[1], base[0] - sp.centre[0])))
        dwn = type_down(sp, base, turn, curve)
        settled = nsign * (ext + gap
                           + half * abs(dwn[0] * perp[0] + dwn[1] * perp[1]))
        aimed = aim_slide(sp, curve, p0, settled, perp, alng, dwn, lift,
                          label_align(sp, d, side, curve, base, dwn), aim)
        if abs(settled - n) < 1e-12 and abs(aimed - slide) < 1e-12:
            break
        n, slide = settled, aimed
    else:
        raise SpecError("%s: the label offset did not settle in %d passes"
                        % (st["id"], LABEL_OFFSET_PASSES))
    n, slide = settled, aimed
    base = (p0[0] + n * perp[0] + (ds + slide) * alng[0],
            p0[1] + n * perp[1] + (ds + slide) * alng[1])
    if curve == "radial":
        rotate = upright_rotate(math.degrees(
            math.atan2(base[1] - sp.centre[1], base[0] - sp.centre[0])))
        a = math.radians(rotate)
        v = (-math.sin(a), math.cos(a))
    align = label_align(sp, d, side, curve, base, dwn)
    aimed_at = None if aim_degenerate(sp, curve, alng, dwn, align) else aim
    return {
        "base": base,
        "x": base[0] + dv * v[0],
        "y": base[1] + dv * v[1],
        "align": align,
        "rotate": rotate,
        "wrap_dy": wrap,
        "baseline_dv": dv,
        "curve": curve,
        "side": side,
        "outward": ou,
        "aim": aimed_at,
        "slide": slide,
        "ds": ds,
        "declared_side": "side" in d,
        "gap": gap,
        "extent": ext,
        "origin": p0,
        "perp": perp,
        "along": alng,
        "nsign": nsign,
    }


def marks_advance(sp, st):
    """Room the station's inline marks take at the end of their label line."""
    return len(st.get("marks", [])) * float(sp.const["station_marks"]["advance"])


def mark_tspans(st, key, mark, dx):
    """One station feature mark (parking, hospital) as inline <tspan>s.

    A mark is a glyph of the label's own text run, so the renderer sets it
    against the name with the font it actually has - no width estimate comes
    into it - and on a <textPath> it follows the same orbit. A mark with a
    `box` is two glyphs: the box, a filled square of the same font, and the
    letter drawn back over it by a negative dx. `features` sets the letter's
    OpenType features.
    """
    def tspan(glyph, shift, first):
        return "<tspan" + attrs([
            ("id", "mark-%s-%s" % (st["id"], key) if first else None),
            ("dx", fmt(shift) if shift else None),
            ("font-size", "%spx" % fmt(glyph["size_px"])), ("font-weight", "700"),
            ("fill", glyph["fill"]),
            ("stroke", glyph["fill"] if "stroke_width" in glyph else "none"),
            ("stroke-width", fmt(glyph["stroke_width"]) if "stroke_width" in glyph else None),
            ("stroke-linejoin", "round" if "stroke_width" in glyph else None),
            ("style", "font-feature-settings:%s" % glyph["features"] if "features" in glyph else None),
        ]) + ">" + esc(glyph["text"]) + "</tspan>"
    if "box" not in mark:
        return tspan(mark, dx, True)
    return tspan(mark["box"], dx, True) + tspan(mark, mark["overlay_dx"], False)


def marked_line(sp, st, lp, line, part):
    """One label line's content: its text, with the station's marks after it -
    or before it when the label is end-anchored, so the marks always sit at the
    end of the name away from the station."""
    cst = sp.const["station_marks"]
    keys = st.get("marks", []) if line == st.get("marks_line", 0) else []
    if not keys:
        return esc(part)
    run = [None] + keys if lp["align"] != "end" else keys[::-1] + [None]
    out, owed = [], 0.0
    for i, key in enumerate(run):
        mark = cst["marks"][key] if key else {}
        dx = float(cst["gap"]) + owed + float(mark.get("lead_dx", 0.0)) if i else 0.0
        if key:
            out.append(mark_tspans(st, key, mark, dx))
        else:
            out.append('<tspan dx="%s">%s</tspan>' % (fmt(dx), esc(part)) if i else esc(part))
        owed = float(mark.get("tail_dx", 0.0))
    return "".join(out)


def along_label(sp, lp, point, down, s, lift):
    """The point `s` along a label line's reading direction from its anchor
    `point`, lifted `lift` off the baseline. Curved type is followed round its
    own arc."""
    u = (down[1], -down[0])
    if lp["curve"] != "concentric":
        return (point[0] + s * u[0] - lift * down[0], point[1] + s * u[1] - lift * down[1])
    cx, cy = sp.centre
    r = math.hypot(point[0] - cx, point[1] - cy)
    theta = math.atan2(point[1] - cy, point[0] - cx)
    turn = 1.0 if -math.sin(theta) * u[0] + math.cos(theta) * u[1] > 0 else -1.0
    outward = 1.0 if math.cos(theta) * down[0] + math.sin(theta) * down[1] > 0 else -1.0
    theta += turn * s / r
    r -= outward * lift
    return (cx + r * math.cos(theta), cy + r * math.sin(theta))


def label_discs(sp, placements):
    """Every station label as a row of discs: points along each line of type,
    at the middle of its height, each with half the type size as its radius. A
    geography line that must not run through a label is cut where it comes
    within a pad of any of them. The widths are the generator's conservative
    advances, so the discs overrun the real ink a little - which is the safe
    side for a gap."""
    discs = []
    for st in sp.data["stations"]:
        lp = label_placement(sp, st, placements[st["id"]])
        frac = {"start": 0.0, "middle": 0.5, "end": 1.0}[lp["align"]]
        lines = label_lines(sp, st, lp["wrap_dy"])
        for i, ((text, size, _, _), (point, down)) in enumerate(
                zip(lines, label_baselines(sp, st, lp))):
            width = text_advance(text, size)
            lo, hi = -width * frac, width * (1.0 - frac)
            if i == st.get("marks_line", 0):
                if lp["align"] == "end":
                    lo -= marks_advance(sp, st)
                else:
                    hi += marks_advance(sp, st)
            steps = max(int((hi - lo) / 3.0), 1)
            for k in range(steps + 1):
                p = along_label(sp, lp, point, down, lo + (hi - lo) * k / steps, 0.36 * size)
                discs.append((p[0], p[1], 0.5 * size, st["id"]))
    return discs


def label_arcs(sp, st, lp):
    """One concentric arc per line of a curved label.

    Each arc is a circle segment about the map centre through that line's
    baseline point, so the text curves with the rings and the arc's own tangent
    supplies the rotation. Reading runs with +theta in the upper hemisphere and
    with -theta in the lower one; that flip is what keeps the glyphs upright on
    both sides of the map, and it also flips the sense of "below the baseline",
    which is inward above the centre line and outward below it. A label whose
    anchor sits exactly on the centre line (sin theta = 0, due east or due west)
    is read as upper-hemisphere: the text is vertical either way, and the tie
    has to break somewhere.
    """
    cx, cy = sp.centre
    bx, by = lp["base"]
    r0 = math.hypot(bx - cx, by - cy)
    theta = math.degrees(math.atan2(by - cy, bx - cx))
    upper = (by - cy) <= 0.0
    down = -1.0 if upper else 1.0
    fwd = 1.0 if upper else -1.0
    pad = float(sp.const["label"]["curve_pad_px"])
    frac = {"start": 0.0, "middle": 0.5, "end": 1.0}.get(lp["align"])
    if frac is None:
        raise SpecError("%s: label align %r cannot be curved"
                        % (st["id"], lp["align"]))
    arcs, dv = [], lp["baseline_dv"]
    for text, size, weight, dy in label_lines(sp, st, lp["wrap_dy"]):
        dv += dy
        r = r0 + down * dv
        if r <= 0.0:
            raise SpecError("%s: curved label line crosses the map centre" % st["id"])
        width = text_advance(text, size)
        marks = marks_advance(sp, st) if len(arcs) == st.get("marks_line", 0) else 0.0
        span = width + marks + (2.0 * pad if frac == 0.5 else pad)
        deg = math.degrees(span / r)
        a0 = theta - fwd * deg * frac
        p0 = polar(sp.centre, r, a0)
        p1 = polar(sp.centre, r, a0 + fwd * deg)
        arcs.append({
            "d": "M %s A %s %s 0 %d %d %s"
                 % (pt(p0), fmt(r), fmt(r), 1 if deg > 180.0 else 0,
                    1 if upper else 0, pt(p1)),
            "text": text, "size": size, "weight": weight,
            "offset": "%g%%" % (frac * 100.0),
            "r": r, "span_deg": deg, "width": width,
            "from": p0, "to": p1,
        })
    return arcs


def label_baselines(sp, st, lp):
    """(baseline point, type-down direction) per line, read back out of the
    geometry that will actually be EMITTED rather than out of `label_placement`.

    Concentric lines are recovered from the arc endpoints — the anchor is the
    point at `align`'s fraction along the arc, and the type's down is the left
    normal of the direction of travel, which is how a renderer hangs glyphs on
    a <textPath>. Straight and radial lines come off the rotated <text>. Both
    sides exist so check_labels can assert the optical-centring invariant
    without trusting the placement maths it is checking.
    """
    frac = {"start": 0.0, "middle": 0.5, "end": 1.0}[lp["align"]]
    if lp["curve"] != "concentric":
        a = math.radians(lp["rotate"])
        v = (-math.sin(a), math.cos(a))
        out, dv = [], lp["baseline_dv"]
        for _, _, _, dy in label_lines(sp, st, lp["wrap_dy"]):
            dv += dy
            out.append(((lp["base"][0] + dv * v[0], lp["base"][1] + dv * v[1]), v))
        return out
    cx, cy = sp.centre
    out = []
    for arc in label_arcs(sp, st, lp):
        t0 = math.degrees(math.atan2(arc["from"][1] - cy, arc["from"][0] - cx))
        t1 = math.degrees(math.atan2(arc["to"][1] - cy, arc["to"][0] - cx))
        sweep = (t1 - t0 + 540.0) % 360.0 - 180.0
        a = math.radians(t0 + sweep * frac)
        sgn = 1.0 if sweep >= 0.0 else -1.0
        u = (-math.sin(a) * sgn, math.cos(a) * sgn)
        out.append((polar(sp.centre, arc["r"], t0 + sweep * frac), (-u[1], u[0])))
    return out


# --------------------------------------------------------------------------
# emitters
# --------------------------------------------------------------------------

class Out:
    def __init__(self):
        self.buf = []
        self.depth = 0

    def line(self, text):
        self.buf.append("  " * self.depth + text)

    def open(self, text):
        self.line(text)
        self.depth += 1

    def close(self, text):
        self.depth -= 1
        self.line(text)

    def text(self):
        return "\n".join(self.buf) + "\n"


def ring_arc_d(sp, r, a0, a1, direction, close=None):
    p0 = polar(sp.centre, r, a0)
    delta, laf, sf = arc_sweep(a0, a1, direction)
    p1 = polar(sp.centre, r, a0 + delta if direction == "cw" else a0 - delta)
    d = "M %s A %s %s 0 %d %d %s" % (pt(p0), fmt(r), fmt(r), laf, sf, pt(p1))
    if close == "chord":
        d += " Z"
    return d


def emit_geo_shape(sp, o, item, discs=None):
    shape = item["shape"]
    if shape == "rect":
        a = [
            ("id", item["id"]), ("x", fmt(item["x"])), ("y", fmt(item["y"])),
            ("width", fmt(item["width"])), ("height", fmt(item["height"])),
            ("rx", fmt(item["rx"])), ("ry", fmt(item["ry"])),
            ("fill", item["fill"]),
        ]
        if "stroke" in item:
            a += [("stroke", item["stroke"]),
                  ("stroke-width", fmt(item["stroke_width"]))]
        if "rotate" in item:
            cx = item["x"] + item["width"] / 2.0
            cy = item["y"] + item["height"] / 2.0
            a.append(("transform", "rotate(%s %s %s)"
                      % (fmt(item["rotate"]), fmt(cx), fmt(cy))))
        o.line("<rect" + attrs(a) + " />")
    elif shape == "ring_arc":
        r = sp.ring(item["ring"])
        d = ring_arc_d(sp, r, float(item["from_angle"]), float(item["to_angle"]),
                       item["dir"])
        o.line("<path" + attrs([
            ("id", item["id"]), ("d", d), ("fill", "none"),
            ("stroke", item["stroke"]),
            ("stroke-width", fmt(item["stroke_width"])),
            ("stroke-linecap", item.get("linecap", "butt")),
        ]) + " />")
    elif shape == "wedge":
        o.line("<path" + attrs([
            ("id", item["id"]), ("d", wedge_d(sp, item)), ("fill", item["fill"]),
        ]) + " />")
    elif shape == "trail":
        o.line("<path" + attrs([
            ("id", item["id"]),
            ("d", masked_trail_d(sp, item, discs) if "label_gap" in item else trail_d(sp, item)),
            ("fill", "none"), ("stroke", item["stroke"]),
            ("stroke-width", fmt(item["stroke_width"])),
            ("stroke-linecap", item.get("linecap", "butt")),
            ("stroke-dasharray", item.get("dasharray")),
        ]) + " />")
    else:
        raise SpecError("unknown park shape %r" % shape)


def open_geo_labels(o, group_id, font):
    o.open("<g" + attrs([
        ("id", group_id), ("font-size", "%spx" % fmt(font["size_px"])),
        ("font-weight", str(font["weight"])), ("font-style", font["style"]),
        ("fill", font["fill"]), ("text-anchor", "middle"),
        ("letter-spacing", font.get("letter_spacing")),
    ]) + ">")


def emit_geo_ring_label(sp, o, lab, r, font):
    """Type on a ring, its caps centred `offset` outside radius `r` at `angle`.
    It reads left to right: clockwise with its top outward in the map's upper
    half, counter-clockwise with its top toward the centre in the lower half."""
    dy = float(font["baseline_dy"])
    r += float(lab.get("offset", 0.0))
    lower = math.sin(math.radians(float(lab["angle"]))) > 0
    span = float(lab["half_span"]) * (1 if lower else -1)
    d = ring_arc_d(sp, r + dy if lower else r - dy, lab["angle"] + span,
                   lab["angle"] - span, "ccw" if lower else "cw")
    o.line('<defs><path id="%s-arc" d="%s"/></defs>' % (lab["id"], d))
    o.line('<text id="%s"><textPath href="#%s-arc" startOffset="50%%">%s</textPath></text>'
           % (lab["id"], lab["id"], esc(lab["text"])))


def emit_geo_label(o, lab, font):
    """Straight geography type: the block of `lines` centred on `at` and turned
    by `rotate` about it. `size_px` and `letter_spacing` on the label replace the font's."""
    a = math.radians(float(lab["rotate"]))
    grow = float(lab.get("size_px", font["size_px"])) / float(font["size_px"])
    wrap = float(font["wrap_dy"]) * grow
    down = float(font["baseline_dy"]) * grow - wrap * (len(lab["lines"]) - 1) / 2.0
    x, y = fmt(lab["at"][0] - down * math.sin(a)), fmt(lab["at"][1] + down * math.cos(a))
    o.open("<text" + attrs([
        ("id", lab["id"]), ("x", x), ("y", y), ("fill", lab.get("fill")),
        ("font-size", "%spx" % fmt(lab["size_px"]) if "size_px" in lab else None),
        ("letter-spacing", lab.get("letter_spacing")),
        ("transform", "rotate(%s, %s, %s)" % (fmt(lab["rotate"]), x, y)
         if lab["rotate"] else None),
    ]) + ">")
    for i, part in enumerate(lab["lines"]):
        o.line("<tspan" + attrs([
            ("x", x), ("dy", "0" if i == 0 else fmt(wrap)),
        ]) + ">" + esc(part) + "</tspan>")
    o.close("</text>")


def emit_geo(sp, o, placements):
    geo = sp.data["geo"]
    discs = label_discs(sp, placements)
    o.open('<g id="layer-geography" data-name="%s">' % esc(layer_name(sp, "layer-geography")))

    o.open('<g id="geo-parks">')
    for park in geo["parks"]:
        emit_geo_shape(sp, o, park, discs)
    o.close("</g>")

    o.open('<g id="geo-water">')
    ana = geo["water"]["anacostia"]
    ar = sp.ring(ana["ring"])
    ana_d = ring_arc_d(sp, ar, float(ana["from_angle"]), float(ana["to_angle"]),
                       ana["dir"])
    o.line("<path" + attrs([
        ("id", "casing-river-anacostia"), ("d", ana_d), ("fill", "none"),
        ("stroke", ana["casing"]["stroke"]),
        ("stroke-width", fmt(ana["casing"]["stroke_width"])),
        ("stroke-linecap", ana.get("linecap", "butt")),
    ]) + " />")
    o.line("<path" + attrs([
        ("id", "river-potomac"), ("d", potomac_d(sp)),
        ("fill", geo["water"]["potomac"]["fill"]),
        ("fill-rule", geo["water"]["potomac"]["fill_rule"]),
    ]) + " />")
    o.line("<path" + attrs([
        ("id", "river-anacostia"), ("d", ana_d), ("fill", "none"),
        ("stroke", ana["stroke"]), ("stroke-width", fmt(ana["stroke_width"])),
        ("stroke-linecap", ana.get("linecap", "butt")),
    ]) + " />")
    o.close("</g>")

    if geo.get("islands"):
        o.open('<g id="geo-islands">')
        for isl in geo["islands"]:
            emit_geo_shape(sp, o, isl)
        o.close("</g>")

    plane = geo["airports"]["plane"]
    for airport in geo["airports"]["items"]:
        o.open('<g id="%s">' % airport["id"])
        emit_geo_shape(sp, o, airport["apron"])
        o.line("<path" + attrs([
            ("id", airport["id"] + "-plane"), ("d", plane["d"]), ("fill", plane["fill"]),
            ("transform", "translate(%s) rotate(%s)"
             % (pt(airport["at"]), fmt(plane["rotate"]))),
        ]) + " />")
        o.close("</g>")

    belt = geo["beltway"]
    o.line("<circle" + attrs([
        ("id", belt["id"]), ("cx", fmt(sp.centre[0])), ("cy", fmt(sp.centre[1])),
        ("r", fmt(sp.ring(belt["ring"]))), ("fill", belt["fill"]),
        ("stroke", belt["stroke"]), ("stroke-width", fmt(belt["stroke_width"])),
    ]) + " />")

    marks = geo["landmarks"]
    o.open("<g" + attrs([
        ("id", "geo-landmarks"), ("fill", marks["fill"]),
        ("fill-rule", marks["fill_rule"]),
    ]) + ">")
    shapes = {item["id"]: item["d"] for item in marks["items"] if "d" in item}
    for item in marks["items"]:
        o.line("<path" + attrs([
            ("id", item["id"]), ("d", shapes[item.get("like", item["id"])]),
            ("fill", item.get("fill")), ("fill-rule", item.get("fill_rule")),
            ("transform", "translate(%s)" % pt(item["at"])
             + (" scale(%s)" % fmt(item["scale"]) if "scale" in item else "")),
        ]) + " />")
    o.close("</g>")

    font = sp.const["fonts"]["park"]
    open_geo_labels(o, "geo-labels-parks", font)
    for item in geo["parks"] + geo.get("islands", []):
        if "label" in item:
            emit_geo_label(o, item["label"], font)
    o.close("</g>")

    font = sp.const["fonts"]["water"]
    open_geo_labels(o, "geo-labels-water", font)
    pot = geo["water"]["potomac"]
    for lab in (pot["label"], pot["tidal_basin"]["label"], pot["south_arm"]["label"]):
        emit_geo_label(o, lab, font)
    emit_geo_ring_label(sp, o, ana["label"], ar, font)
    o.close("</g>")

    font = sp.const["fonts"]["boundary"]
    open_geo_labels(o, "geo-labels-boundary", font)
    for lab in geo["boundary_labels"]["on_ring"]:
        emit_geo_ring_label(sp, o, lab, sp.ring(lab["ring"]), font)
    for lab in geo["boundary_labels"]["straight"]:
        emit_geo_label(o, lab, font)
    o.close("</g>")

    font = sp.const["fonts"]["beltway"]
    open_geo_labels(o, "geo-labels-beltway", font)
    for lab in belt["labels"]:
        emit_geo_ring_label(sp, o, lab, sp.ring(belt["ring"]), font)
    o.close("</g>")
    o.close("</g>")


def wedge_d(sp, park):
    """A rounded wedge: the annulus between two rings, cut by two radial edges,
    with all four corners filleted tangentially.

    A fillet of radius f tangent to a ring of radius r from inside the wedge has
    its centre at radius r + f (inner ring) or r - f (outer one); that centre is
    f from the radial edge, so the arc leaves the edge angle by asin(f/rc) and
    the edge itself runs between the feet at sqrt(rc**2 - f**2).
    """
    ri = sp.ring(park["inner_ring"])
    ro = sp.ring(park["outer_ring"])
    a0, a1 = float(park["from_angle"]), float(park["to_angle"])
    f = float(park["fillet_r"])
    rc_i, rc_o = ri + f, ro - f
    if f >= ri or rc_o <= rc_i or a1 <= a0:
        raise SpecError("%s: fillet %g does not fit the wedge" % (park["id"], f))
    da_i = math.degrees(math.asin(f / rc_i))
    da_o = math.degrees(math.asin(f / rc_o))
    s_i, s_o = math.sqrt(rc_i ** 2 - f * f), math.sqrt(rc_o ** 2 - f * f)
    at = lambda r, a: pt(polar(sp.centre, r, a))
    arc = lambda r, sweep, r_to, a_to: ("A %s %s 0 0 %d %s"
                                        % (fmt(r), fmt(r), sweep, at(r_to, a_to)))
    return " ".join([
        "M " + at(ri, a0 + da_i),
        arc(ri, 1, ri, a1 - da_i),
        arc(f, 0, s_i, a1),
        "L " + at(s_o, a1),
        arc(f, 0, ro, a1 - da_o),
        arc(ro, 0, ro, a0 + da_o),
        arc(f, 0, s_o, a0),
        "L " + at(s_i, a0),
        arc(f, 0, ri, a0 + da_i),
        "Z",
    ])


def trail_d(sp, park):
    if "segments" not in park:
        return "M %s L %s" % (pt(park["from"]), pt(park["to"]))
    cmds = []
    cur = None
    for seg in park["segments"]:
        kind = seg["kind"]
        if kind == "straight":
            if "from" in seg:
                cur = tuple(seg["from"])
                cmds.append("M " + pt(cur))
            end = tuple(seg["to"])
            cmds.append("L " + pt(end))
            cur = end
        elif kind == "fillet":
            prev_dir = _last_direction(park, seg, cur)
            end = tuple(seg["to"])
            chord = (end[0] - cur[0], end[1] - cur[1])
            cross = prev_dir[0] * chord[1] - prev_dir[1] * chord[0]
            cmds.append("A %s %s 0 0 %d %s" %
                        (fmt(seg["r"]), fmt(seg["r"]), 1 if cross > 0 else 0, pt(end)))
            cur = end
        elif kind == "arc":
            r = sp.ring(seg["ring"])
            a0, a1 = float(seg["from_angle"]), float(seg["to_angle"])
            delta, laf, sf = arc_sweep(a0, a1, seg["dir"])
            end = polar(sp.centre, r, a0 + delta if seg["dir"] == "cw" else a0 - delta)
            cmds.append("A %s %s 0 %d %d %s" % (fmt(r), fmt(r), laf, sf, pt(end)))
            cur = end
        elif kind == "cubic":
            cmds.append(seg["d"])
            cur = tuple(seg["to"])
        elif kind == "ellipse_arc":
            cur = tuple(seg["to"])
            cmds.append("A %s %s %s 0 %d %s" % (fmt(seg["rx"]), fmt(seg["ry"]),
                                                fmt(seg["rotation"]), seg["sweep"], pt(cur)))
        else:
            raise SpecError("unknown trail segment %r" % kind)
    return " ".join(cmds)


def trail_points(sp, park, step):
    """The trail as points `step` apart, with the indices where a sub-path
    starts (a straight that has its own `from`) and the indices of the segment
    ends. It reads straight segments, ring arcs and cubics that carry their two
    control points (`c1`, `c2`)."""
    pts, starts, corners = [], [], []
    for seg in park["segments"]:
        kind = seg["kind"]
        if kind == "straight":
            if "from" in seg:
                starts.append(len(pts))
                pts.append(tuple(seg["from"]))
            a, b = pts[-1], tuple(seg["to"])
            n = max(int(math.hypot(b[0] - a[0], b[1] - a[1]) / step), 1)
            pts += [(a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n)
                    for i in range(1, n + 1)]
        elif kind == "arc":
            r = sp.ring(seg["ring"])
            a0 = float(seg["from_angle"])
            delta, _, sf = arc_sweep(a0, float(seg["to_angle"]), seg["dir"])
            n = max(int(math.radians(delta) * r / step), 1)
            pts += [polar(sp.centre, r, a0 + (delta if sf else -delta) * i / n)
                    for i in range(1, n + 1)]
        elif kind == "cubic" and "c1" in seg:
            p0, p1, p2, p3 = pts[-1], seg["c1"], seg["c2"], seg["to"]
            n = max(int(math.hypot(p3[0] - p0[0], p3[1] - p0[1]) * 1.3 / step), 1)
            for i in range(1, n + 1):
                t = i / float(n)
                u = 1.0 - t
                pts.append(tuple(u ** 3 * p0[k] + 3 * u * u * t * p1[k]
                                 + 3 * u * t * t * p2[k] + t ** 3 * p3[k] for k in (0, 1)))
        else:
            raise SpecError("%s: cannot sample trail segment %r" % (park["id"], kind))
        corners.append(len(pts) - 1)
    return pts, set(starts), set(corners)


def masked_trail_d(sp, park, discs):
    """The trail's path with a gap wherever it comes within `label_gap` of a
    station label: each kept run is its own sub-path, so the line stops before
    a label and starts again after it; the labels of the stations in
    `label_gap_except` make no gap. A straight with its own `from` also
    starts a sub-path, and with it the dash pattern, so a dot is exactly there.
    Each segment end stays in the path, which keeps the corners sharp."""
    gap = float(park["label_gap"])
    pts, starts, corners = trail_points(sp, park, 1.0)
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    skip = park.get("label_gap_except", [])
    near = [d for d in discs if d[3] not in skip and min(xs) - 30 < d[0] < max(xs) + 30
            and min(ys) - 30 < d[1] < max(ys) + 30]
    keep = [all(math.hypot(p[0] - d[0], p[1] - d[1]) > d[2] + gap for d in near) for p in pts]
    runs, run = [], []
    for i, k in enumerate(keep):
        if run and (not k or i in starts):
            runs.append(run)
            run = []
        if k:
            run.append(i)
    if run:
        runs.append(run)
    return " ".join("M " + " L ".join(pt(pts[i]) for n, i in enumerate(r)
                                      if n % 4 == 0 or i == r[-1] or i in corners)
                    for r in runs if len(r) > 1)


def _last_direction(park, fillet_seg, cur):
    segs = park["segments"]
    i = segs.index(fillet_seg)
    prev = segs[i - 1]
    a = math.radians(prev["angle"])
    return (math.cos(a), math.sin(a))


def bulge_fillet_d(sp, arm, bulge):
    """Water that rounds the land's corner where the arm's west bank meets the
    bulge: the patch between the bank line, the bulge ring and a circle of
    radius `fillet` that touches both from the land side."""
    rho, big = float(bulge["fillet"]), sp.ring(bulge["ring"])
    edge = arm["west_bank_edge"]
    a, b = arm["vertices"][edge["from"]], arm["vertices"][edge["to"]]
    length = math.dist(a, b)
    u = ((b[0] - a[0]) / length, (b[1] - a[1]) / length)
    n = (-u[1], u[0])
    base = (a[0] + rho * n[0] - sp.centre[0], a[1] + rho * n[1] - sp.centre[1])
    along = base[0] * u[0] + base[1] * u[1]
    t = -along - math.sqrt(along ** 2 - base[0] ** 2 - base[1] ** 2 + (big + rho) ** 2)
    on_line = (a[0] + t * u[0], a[1] + t * u[1])
    centre = (on_line[0] + rho * n[0], on_line[1] + rho * n[1])
    k = big / (big + rho)
    on_ring = (sp.centre[0] + (centre[0] - sp.centre[0]) * k,
               sp.centre[1] + (centre[1] - sp.centre[1]) * k)
    corner = polar(sp.centre, big, float(bulge["to_angle"]))
    def sweep(p, q, c):
        return int((p[0] - c[0]) * (q[1] - c[1]) - (p[1] - c[1]) * (q[0] - c[0]) > 0)
    return ("M %s L %s A %s %s 0 0 %d %s A %s %s 0 0 %d %s Z"
            % (pt(corner), pt(on_line), fmt(rho), fmt(rho), sweep(on_line, on_ring, centre),
               pt(on_ring), fmt(big), fmt(big), sweep(on_ring, corner, sp.centre), pt(corner)))


def potomac_d(sp):
    pot = sp.data["geo"]["water"]["potomac"]
    arm = pot["nw_arm"]
    parts = ["M " + " L ".join(pt(v) for v in arm["vertices"]) + " Z"]
    bulge = pot["central_bulge"]
    parts.append(ring_arc_d(sp, sp.ring(bulge["ring"]), float(bulge["from_angle"]),
                            float(bulge["to_angle"]), bulge["dir"], bulge["close"]))
    if "fillet" in bulge:
        parts.append(bulge_fillet_d(sp, arm, bulge))
    parts.append(pot["south_arm"]["d"])
    basin = pot["tidal_basin"]
    parts.append("M %s A %s %s %s 0 1 %s Z"
                 % (pt(basin["from"]), fmt(basin["rx"]), fmt(basin["ry"]),
                    fmt(basin["rotation"]), pt(basin["to"])))
    return " ".join(parts)


def layer_name(sp, lid):
    for layer in sp.data["layers"]:
        if layer["id"] == lid:
            return layer["name"]
    return lid


def emit_lines(sp, o, resolved):
    c = sp.const["line"]
    o.open('<g id="layer-lines" data-name="%s" fill="none" stroke-width="%s" '
           'stroke-linecap="%s" stroke-linejoin="%s">'
           % (esc(layer_name(sp, "layer-lines")), fmt(c["stroke_width"]),
              c["linecap"], c["linejoin"]))
    service = sp.data["service_patterns"]
    for lid in draw_order(sp):
        ln = sp.line(lid)
        d = resolved[lid]["d"]
        o.line("<path" + attrs([
            ("id", "casing-line-" + lid), ("d", d),
            ("stroke", c["casing_colour"]), ("stroke-width", fmt(c["casing_width"])),
        ]) + " />")
        o.line("<path" + attrs([
            ("id", "line-" + lid), ("d", d), ("stroke", ln["colour"]),
        ]) + " />")
        for section in service["sections"]:
            if section["line"] == lid:
                o.line("<path" + attrs([
                    ("id", "service-" + section["id"]), ("d", resolve_line(sp, section)[0]),
                    ("stroke", service["stroke"]), ("stroke-dasharray", service["dasharray"]),
                ]) + " />")
    o.close("</g>")


def draw_order(sp):
    for layer in sp.data["layers"]:
        if layer["id"] == "layer-lines":
            order = layer.get("draw_order") or [ln["id"] for ln in sp.data["lines"]]
            for lid in order:
                if lid not in sp.lines:
                    raise SpecError("layer-lines draw_order references unknown line %r"
                                    % lid)
            missing = [ln["id"] for ln in sp.data["lines"] if ln["id"] not in order]
            if missing:
                raise SpecError("lines missing from draw_order: %s" % missing)
            return order
    return [ln["id"] for ln in sp.data["lines"]]


def circle(o, eid, p, r, fill, stroke, width):
    o.line("<circle" + attrs([
        ("id", eid), ("cx", fmt(p[0])), ("cy", fmt(p[1])), ("r", fmt(r)),
        ("fill", fill), ("stroke", stroke),
        ("stroke-width", fmt(width) if width is not None else None),
    ]) + " />")


def emit_marker(sp, o, st, placed):
    cst = sp.const
    marker = st["marker"]
    sid = st["id"]
    o.open("<g" + attrs([("id", "stop-" + sid), ("data-st", st["name"]),
                         ("data-cor", st["corridor"])]) + ">")
    if st.get("tick"):
        dx, dy = tick_direction(sp, placed)
        half = float(cst["station_tick"]["length"]) / 2.0
        p = placed["dots"][0]["p"]
        a = (p[0] - dx * half, p[1] - dy * half)
        b = (p[0] + dx * half, p[1] + dy * half)
        o.line("<path" + attrs([
            ("id", "tick-" + sid), ("d", "M %s L %s" % (pt(a), pt(b))),
            ("fill", "none"), ("stroke", cst["station_tick"]["stroke"]),
            ("stroke-width", fmt(cst["station_tick"]["stroke_width"])),
            ("stroke-linecap", cst["station_tick"]["linecap"]),
        ]) + " />")

    if marker == "dot":
        c = cst["station_dot"]
        circle(o, "dot-" + sid, placed["dots"][0]["p"], c["r"], c["fill"],
               c["stroke"], c["stroke_width"])
    elif marker == "terminal":
        c = cst["terminal_dot"]
        f = cst["fonts"]["terminal_letter"]
        letters = st.get("terminal_letters", [])
        light = set(f.get("light_line_colour_keys", []))
        for i, dot in enumerate(placed["dots"]):
            suffix = "" if len(placed["dots"]) == 1 else "-" + (dot["line"] or str(i))
            line = sp.line(dot["line"] or st["lines"][0])
            circle(o, "terminal-" + sid + suffix, dot["p"], c["r"], line["colour"],
                   c["stroke"], c["stroke_width"])
            if i < len(letters):
                ink = line["colour_key"] in light
                o.line("<text" + attrs([
                    ("id", "terminal-letter-" + sid + suffix),
                    ("x", fmt(dot["p"][0])),
                    ("y", fmt(dot["p"][1] + float(c["letter_baseline_dy"]))),
                    ("text-anchor", f["anchor"]), ("font-size", "%spx" % fmt(f["size_px"])),
                    ("font-weight", str(f["weight"])),
                    ("fill", f["fill_on_light_line"] if ink else f["fill"]),
                ]) + ">" + esc(letters[i]) + "</text>")
    elif marker == "interchange":
        c = cst["interchange_dot"]
        p = placed["dots"][0]["p"]
        circle(o, "interchange-" + sid + "-outer", p, c["outer_r"], c["fill"],
               c["stroke"], c["outer_stroke_width"])
        circle(o, "interchange-" + sid + "-inner", p, c["inner_r"], c["fill"],
               c["stroke"], c["inner_stroke_width"])
    elif marker == "capsule":
        c = cst["capsule"]
        a, b = placed["capsule"]
        d = "M %s L %s" % (pt(a), pt(b))
        for suffix, colour, width in (("-bar", c["casing_colour"], c["casing_stroke_width"]),
                                      ("-bar-inner", c["inner_colour"], c["inner_stroke_width"])):
            o.line("<path" + attrs([
                ("id", "capsule-" + sid + suffix), ("d", d), ("fill", "none"),
                ("stroke", colour), ("stroke-width", fmt(width)),
                ("stroke-linecap", c["linecap"]),
            ]) + " />")
        for name, p in (("a", a), ("b", b)):
            circle(o, "capsule-" + sid + "-" + name, p, c["end_dot_r"],
                   cst["station_dot"]["fill"], cst["station_dot"]["stroke"],
                   c["end_dot_stroke_width"])
    else:
        raise SpecError("unknown marker %r" % marker)
    o.close("</g>")


def emit_stations(sp, o, placements):
    groups = [("layer-stations", ("dot", "terminal"), "stops"),
              ("layer-interchanges", ("interchange", "capsule"), "ix")]
    for lid, markers, prefix in groups:
        o.open('<g id="%s" data-name="%s">' % (lid, esc(layer_name(sp, lid))))
        for cid in corridor_order(sp, markers):
            o.open('<g id="%s-%s">' % (prefix, cid.lower()))
            for st in sp.data["stations"]:
                if st["corridor"] == cid and st["marker"] in markers:
                    emit_marker(sp, o, st, placements[st["id"]])
            o.close("</g>")
        o.close("</g>")


def corridor_order(sp, markers):
    seen = []
    for st in sp.data["stations"]:
        if st["marker"] in markers and st["corridor"] not in seen:
            seen.append(st["corridor"])
    return seen


def emit_straight_label(sp, o, st, lp, shared):
    """Flat text turned by `lp["rotate"]`: one <text> with a <tspan> per line.

    This serves both `curve` = "straight" and `curve` = "radial" — a radius is
    a straight line, so a rotated <text> is the whole of it and a <textPath>
    would buy nothing but a renderer dependency (librsvg drops textPath). The
    two modes differ only in where `rotate` came from, which label_placement
    has already settled.
    """
    sec = sp.const["fonts"]["secondary"]
    x, y = fmt(lp["x"]), fmt(lp["y"])
    rot = None
    if abs(lp["rotate"]) > 1e-9:
        rot = "rotate(%s, %s, %s)" % (fmt(lp["rotate"]), x, y)
    o.open("<text" + attrs([
        ("id", "label-" + st["id"]), ("x", x), ("y", y),
        ("text-anchor", None if shared else lp["align"]), ("transform", rot),
        ("data-st", st["name"]), ("data-cor", st["corridor"]),
    ]) + ">")
    for i, part in enumerate(st["label"]["title"]):
        o.line("<tspan" + attrs([
            ("x", x), ("dy", "0" if i == 0 else fmt(lp["wrap_dy"])),
        ]) + ">" + marked_line(sp, st, lp, i, part) + "</tspan>")
    for i, part in enumerate(st["label"].get("subtitle", [])):
        dy = sec["first_dy"] if i == 0 else sec["subsequent_dy"]
        o.line("<tspan" + attrs([
            ("x", x), ("dy", fmt(dy)),
            ("font-size", "%spx" % fmt(sec["size_px"])),
            ("font-weight", str(sec["weight"])),
        ]) + ">" + esc(part) + "</tspan>")
    o.close("</text>")


def emit_curved_label(sp, o, st, lp, arcs, shared):
    """One <text> per line, each holding a single <textPath>. Several textPath
    children inside one <text> is legal SVG 1.1 but Inkscape mislays them, so
    the lines are kept in separate elements and grouped."""
    prim = sp.const["fonts"]["primary"]
    o.open("<g" + attrs([
        ("id", "label-" + st["id"]),
        ("text-anchor", None if shared else lp["align"]),
        ("data-st", st["name"]), ("data-cor", st["corridor"]),
    ]) + ">")
    for i, arc in enumerate(arcs):
        second = arc["size"] != prim["size_px"] or arc["weight"] != prim["weight"]
        o.line("<text" + attrs([
            ("id", "label-%s-%d" % (st["id"], i + 1)),
            ("font-size", "%spx" % fmt(arc["size"]) if second else None),
            ("font-weight", str(arc["weight"]) if second else None),
        ]) + '><textPath href="#arc-%s-%d" startOffset="%s">%s</textPath></text>'
            % (st["id"], i + 1, arc["offset"], marked_line(sp, st, lp, i, arc["text"])))
    o.close("</g>")


def emit_labels(sp, o, placements):
    prim = sp.const["fonts"]["primary"]
    groups = [(cid, [st for st in sp.data["stations"] if st["corridor"] == cid])
              for cid in corridor_order(sp, ("dot", "terminal", "interchange",
                                             "capsule"))]
    placed = {st["id"]: label_placement(sp, st, placements[st["id"]])
              for _, group in groups for st in group}
    arcs = {st["id"]: label_arcs(sp, st, placed[st["id"]])
            for _, group in groups for st in group
            if placed[st["id"]]["curve"] == "concentric"}
    o.open('<g id="layer-labels" data-name="%s" font-size="%spx" font-weight="%s" '
           'fill="%s">' % (esc(layer_name(sp, "layer-labels")), fmt(prim["size_px"]),
                           prim["weight"], prim["fill"]))
    if arcs:
        o.open('<defs id="label-arcs">')
        for _, group in groups:
            for st in group:
                for i, arc in enumerate(arcs.get(st["id"], [])):
                    o.line('<path id="arc-%s-%d" d="%s"/>'
                           % (st["id"], i + 1, arc["d"]))
        o.close("</defs>")
    for cid, group in groups:
        aligns = {placed[st["id"]]["align"] for st in group}
        shared = aligns.pop() if len(aligns) == 1 else None
        o.open("<g" + attrs([("id", "labels-" + cid.lower()),
                             ("text-anchor", shared)]) + ">")
        for st in group:
            lp = placed[st["id"]]
            if st["id"] in arcs:
                emit_curved_label(sp, o, st, lp, arcs[st["id"]], shared)
            else:
                emit_straight_label(sp, o, st, lp, shared)
        o.close("</g>")
    emit_connections(sp, o)
    o.close("</g>")


def emit_connections(sp, o):
    """Rail-connection badges: one rounded rect per service with its name in
    it, laid out as a row or a column centred on the item's `at`."""
    con = sp.data["connections"]
    font, badge = con["font"], con["badge"]
    height, gap = float(badge["height"]), float(badge["gap"])
    o.open("<g" + attrs([
        ("id", "connections"), ("font-size", "%spx" % fmt(font["size_px"])),
        ("font-weight", str(font["weight"])), ("text-anchor", "middle"),
    ]) + ">")
    for item in con["items"]:
        column = item["layout"] == "column"
        services = [con["services"][key] for key in item["services"]]
        sizes = [height if column else float(s["width"]) for s in services]
        widest = max(float(s["width"]) for s in services)
        offset = -(sum(sizes) + gap * (len(sizes) - 1)) / 2.0
        for key, service, size in zip(item["services"], services, sizes):
            along = offset + size / 2.0
            width = float(service["width"])
            shift = {"start": -1.0, "middle": 0.0, "end": 1.0}[item.get("align", "middle")]
            cx = item["at"][0] + (shift * (widest - width) / 2.0 if column else along)
            cy = item["at"][1] + (along if column else 0.0)
            o.line("<rect" + attrs([
                ("id", "connection-%s-%s" % (item["station"], key)),
                ("x", fmt(cx - width / 2.0)), ("y", fmt(cy - height / 2.0)),
                ("width", fmt(width)), ("height", fmt(height)),
                ("rx", fmt(badge["rx"])), ("fill", service["fill"]),
            ]) + " />")
            o.line("<text" + attrs([
                ("x", fmt(cx)), ("y", fmt(cy + float(font["baseline_dy"]))),
                ("fill", font["fill"]),
            ]) + ">%s</text>" % esc(service["text"]))
            offset += size + gap
    o.close("</g>")


def legend_text(o, font, x, y, text, anchor=None):
    """One line of legend type, its capitals centred on `y`."""
    o.line("<text" + attrs([
        ("x", fmt(x)), ("y", fmt(y + 0.364 * float(font["size_px"]))),
        ("font-size", "%spx" % fmt(font["size_px"])),
        ("font-weight", str(font["weight"])), ("text-anchor", anchor),
    ]) + ">%s</text>" % esc(text))


def legend_symbol_half(sp, row):
    """Half the width of a legend row's sample, so a column of samples can be
    set flush to one side."""
    cst, kind = sp.const, row["kind"]
    if kind == "station":
        return cst["station_dot"]["r"] + cst["station_dot"]["stroke_width"] / 2.0
    if kind == "interchange":
        c = cst["interchange_dot"]
        return (c["outer_r"] + c["outer_stroke_width"] / 2.0) * float(row["scale"])
    if kind == "mark":
        box = cst["station_marks"]["marks"][row["mark"]]["box"]
        return (0.7275 * float(box["size_px"]) + float(box["stroke_width"])) / 2.0
    if kind == "connections":
        con = sp.data["connections"]
        widths = [float(con["services"][key]["width"]) for key in row["services"]]
        return (sum(widths) + float(con["badge"]["gap"]) * (len(widths) - 1)) / 2.0
    if kind == "airport":
        apron = sp.data["geo"]["airports"]["items"][0]["apron"]
        return float(apron["width"]) * float(row["scale"]) / 2.0
    if kind in ("swatch", "service"):
        return float(row["width"]) / 2.0
    if kind == "landmarks":
        return (float(row["step"]) * (len(row["items"]) - 1) + 22.0 * float(row["scale"])) / 2.0
    raise SpecError("unknown legend row kind %r" % kind)


def legend_symbol(sp, o, row, cx, cy):
    """The sample a legend row shows, centred on (cx, cy). Every sample is
    drawn from the same constants as the thing on the map it stands for."""
    cst, geo, kind = sp.const, sp.data["geo"], row["kind"]
    if kind == "station":
        c = cst["station_dot"]
        circle(o, None, (cx, cy), c["r"], c["fill"], c["stroke"], c["stroke_width"])
    elif kind == "interchange":
        c, k = cst["interchange_dot"], float(row["scale"])
        circle(o, None, (cx, cy), c["outer_r"] * k, c["fill"], c["stroke"],
               c["outer_stroke_width"] * k)
        circle(o, None, (cx, cy), c["inner_r"] * k, c["fill"], c["stroke"],
               c["inner_stroke_width"] * k)
    elif kind == "mark":
        mark = cst["station_marks"]["marks"][row["mark"]]
        box = mark["box"]
        side = 0.7275 * float(box["size_px"])
        o.line("<text" + attrs([
            ("x", fmt(cx - 0.455 * float(box["size_px"]))), ("y", fmt(cy + side / 2.0)),
        ]) + ">" + mark_tspans({"id": "legend"}, row["mark"], mark, 0.0) + "</text>")
    elif kind == "connections":
        con = sp.data["connections"]
        font, badge = con["font"], con["badge"]
        services = [con["services"][key] for key in row["services"]]
        gap, height = float(badge["gap"]), float(badge["height"])
        x = cx - (sum(float(s["width"]) for s in services) + gap * (len(services) - 1)) / 2.0
        for service in services:
            width = float(service["width"])
            o.line("<rect" + attrs([
                ("x", fmt(x)), ("y", fmt(cy - height / 2.0)), ("width", fmt(width)),
                ("height", fmt(height)), ("rx", fmt(badge["rx"])), ("fill", service["fill"]),
            ]) + " />")
            o.line("<text" + attrs([
                ("x", fmt(x + width / 2.0)), ("y", fmt(cy + float(font["baseline_dy"]))),
                ("font-size", "%spx" % fmt(font["size_px"])),
                ("font-weight", str(font["weight"])), ("text-anchor", "middle"),
                ("fill", font["fill"]),
            ]) + ">%s</text>" % esc(service["text"]))
            x += width + gap
    elif kind == "airport":
        plane, apron = geo["airports"]["plane"], geo["airports"]["items"][0]["apron"]
        k = float(row["scale"])
        half = float(apron["width"]) * k / 2.0
        o.line("<rect" + attrs([
            ("x", fmt(cx - half)), ("y", fmt(cy - half)), ("width", fmt(2 * half)),
            ("height", fmt(2 * half)), ("rx", fmt(float(apron["rx"]) * k)),
            ("fill", apron["fill"]),
        ]) + " />")
        o.line("<path" + attrs([
            ("d", plane["d"]), ("fill", plane["fill"]),
            ("transform", "translate(%s) rotate(%s) scale(%s)"
             % (pt((cx, cy)), fmt(plane["rotate"]), fmt(k))),
        ]) + " />")
    elif kind == "swatch":
        w, h = float(row["width"]), float(row["height"])
        o.line("<rect" + attrs([
            ("x", fmt(cx - w / 2.0)), ("y", fmt(cy - h / 2.0)), ("width", fmt(w)),
            ("height", fmt(h)), ("rx", fmt(row["rx"])), ("fill", row["fill"]),
        ]) + " />")
    elif kind == "service":
        service, half = sp.data["service_patterns"], float(row["width"]) / 2.0
        d = "M %s H %s" % (pt((cx - half, cy)), fmt(cx + half))
        for stroke, dash in ((sp.line(row["line"])["colour"], None), (service["stroke"], service["dasharray"])):
            o.line("<path" + attrs([
                ("d", d), ("stroke", stroke), ("stroke-width", fmt(row["height"])),
                ("stroke-dasharray", dash),
                ("stroke-dashoffset", dash.split()[0] if dash else None),
            ]) + " />")
    elif kind == "landmarks":
        marks = geo["landmarks"]
        items = {item["id"]: item for item in marks["items"]}
        step, k = float(row["step"]), float(row["scale"])
        x = cx - step * (len(row["items"]) - 1) / 2.0
        for lid in row["items"]:
            o.line("<path" + attrs([
                ("d", items[lid]["d"]), ("fill", marks["fill"]),
                ("fill-rule", marks["fill_rule"]),
                ("transform", "translate(%s) scale(%s)" % (pt((x, cy)), fmt(k))),
            ]) + " />")
            x += step
    else:
        raise SpecError("unknown legend row kind %r" % kind)


def emit_legend(sp, o):
    """The map's key, in the canvas's corners: the sign, title and contact lines;
    the line key with the service pattern and the accessibility line; the symbol
    key with the credits; and the north sign."""
    leg = sp.data["legend"]
    fonts, step = leg["fonts"], float(leg["row_height"])
    o.open('<g id="layer-legend" data-name="%s" fill="%s">'
           % (esc(layer_name(sp, "layer-legend")), leg["fill"]))

    title = leg["title"]
    o.open('<g id="legend-title">')
    x, y = title["at"]
    logo = title["logo"]
    size = float(logo["size"])
    top = y + float(logo["dy"]) - size / 2.0
    o.line('<defs><clipPath id="legend-logo-outline"><path d="%s" /></clipPath></defs>' % logo["d"])
    o.open("<g" + attrs([
        ("clip-path", "url(#legend-logo-outline)"), ("fill", "none"), ("stroke", leg["fill"]),
        ("transform", "translate(%s) scale(%s)"
         % (pt((x, top)), fmt(size / float(logo["d_box"])))),
    ]) + ">")
    for inner, outer in logo["rings"]["bands"]:
        o.line("<circle" + attrs([
            ("cx", fmt(logo["rings"]["centre"][0])), ("cy", fmt(logo["rings"]["centre"][1])),
            ("r", fmt((inner + outer) / 2.0)), ("stroke-width", fmt(outer - inner)),
        ]) + " />")
    o.close("</g>")
    x += size + float(logo["gap"])
    legend_text(o, fonts["title"], x + float(title.get("text_dx", 0.0)),
                y + float(title.get("text_dy", 0.0)), title["text"])
    for i, note in enumerate(title["notes"]):
        legend_text(o, fonts["text"], x + float(title.get("notes_dx", 0.0)),
                    y + float(title["first_dy"]) + i * float(title["note_dy"]), note)
    info = title["contact"]
    for i, line in enumerate(info["lines"]):
        legend_text(o, fonts["text"], x + float(info.get("dx", 0.0)),
                    y + float(info["first_dy"]) + i * float(info["line_dy"]), line)
    o.close("</g>")

    key = leg["lines"]
    letter, dot = sp.const["fonts"]["terminal_letter"], sp.const["terminal_dot"]
    light = set(letter["light_line_colour_keys"])
    o.open('<g id="legend-lines">')
    x, y = key["at"]
    legend_text(o, fonts["section"], x, y + float(key.get("heading_dy", 0.0)), key["heading"])
    for i, row in enumerate(key["rows"]):
        cy = y + (i + 1) * step
        line = sp.line(row["line"])
        circle(o, None, (x + float(key["dot_dx"]), cy), key["dot_r"], line["colour"],
               dot["stroke"], key["dot_stroke_width"])
        o.line("<text" + attrs([
            ("x", fmt(x + float(key["dot_dx"]))),
            ("y", fmt(cy + 0.364 * float(fonts["heading"]["size_px"]))),
            ("text-anchor", "middle"), ("font-size", "%spx" % fmt(fonts["heading"]["size_px"])),
            ("font-weight", str(fonts["heading"]["weight"])),
            ("fill", letter["fill_on_light_line"] if line["colour_key"] in light
             else letter["fill"]),
        ]) + ">%s</text>" % esc(row["letter"]))
        legend_text(o, fonts["heading"], x + float(key["name_dx"]), cy, row["name"])
        legend_text(o, fonts["text"], x + float(key["ends_dx"]), cy, row["ends"])
    cy = y + (len(key["rows"]) + 1) * step
    legend_symbol(sp, o, key["service"], x + float(key["service"]["width"]) / 2.0, cy)
    legend_text(o, fonts["text"], x + float(key["name_dx"]), cy, key["service"]["text"])
    o.close("</g>")

    key = leg["symbols"]
    o.open('<g id="legend-symbols">')
    x, y = key["at"]
    anchor = key.get("text_anchor")
    legend_text(o, fonts["section"], x + float(key.get("heading_dx", 0.0)),
                y + float(key.get("heading_dy", 0.0)), key["heading"], anchor)
    for i, row in enumerate(key["rows"]):
        cy = y + (i + 1) * float(key.get("row_height", step))
        half = legend_symbol_half(sp, row)
        cx = {"start": x + half, "middle": x + float(key["symbol_width"]) / 2.0,
              "end": x + float(key["symbol_width"]) - half}[key["symbol_align"]]
        legend_symbol(sp, o, row, cx, cy)
        legend_text(o, fonts["text"], x + float(key["text_dx"]), cy, row["text"], anchor)
    o.close("</g>")

    access = leg["accessible"]
    icon = access["icon"]
    x, y = access["at"]
    o.open('<g id="legend-accessible">')
    o.line("<path" + attrs([
        ("d", icon["d"]),
        ("transform", "translate(%s) scale(%s)" % (pt((x, y)), fmt(icon["scale"]))),
    ]) + " />")
    legend_text(o, fonts["section"], x + float(access["text_dx"]), y, access["text"])
    o.close("</g>")

    credits = leg["credits"]
    x, y = credits["at"]
    o.open('<g id="legend-credits" fill="%s">' % credits["fill"])
    rule = credits["rule"]
    x0 = x - float(rule["width"]) if credits.get("anchor") == "end" else x
    o.line("<path" + attrs([
        ("d", "M %s H %s" % (pt((x0, y + float(rule["dy"]))), fmt(x0 + float(rule["width"])))),
        ("stroke", credits["fill"]), ("stroke-width", fmt(rule["stroke_width"])),
    ]) + " />")
    for i, line in enumerate(credits["lines"]):
        legend_text(o, fonts["text"], x, y + i * float(credits["line_dy"]), line, credits.get("anchor"))
    o.close("</g>")

    north = leg["north"]
    x, y = north["at"]
    o.open('<g id="legend-north">')
    o.line("<path" + attrs([
        ("d", north["d"]), ("fill", north["ink"]),
        ("transform", "translate(%s)" % pt((x, y))),
    ]) + " />")
    o.line("<text" + attrs([
        ("x", fmt(x)), ("y", fmt(y + float(north["letter_dy"]))),
        ("text-anchor", "middle"), ("font-size", "%spx" % fmt(north["letter_px"])),
        ("font-weight", "700"), ("fill", north["ink"]),
    ]) + ">%s</text>" % esc(north["letter"]))
    o.close("</g>")
    o.close("</g>")


def build_svg(sp, resolved, placements, layers):
    cv = sp.data["canvas"]
    o = Out()
    o.line('<?xml version="1.0" encoding="UTF-8"?>')
    o.open('<svg xmlns="http://www.w3.org/2000/svg" version="1.1" '
           'viewBox="%s" width="%s" height="%s" font-family="%s">'
           % (" ".join(fmt(v) for v in cv["view_box"]), fmt(cv["width"]),
              fmt(cv["height"]), esc(sp.const["fonts"]["family"])))
    o.line("<title id=\"title\">%s</title>" % esc(sp.data["title"]))
    o.line('<g id="layer-page" data-name="%s" />' % esc(layer_name(sp, "layer-page")))
    if "geo" in layers:
        emit_geo(sp, o, placements)
    if "lines" in layers:
        emit_lines(sp, o, resolved)
    if "stations" in layers:
        emit_stations(sp, o, placements)
    if "labels" in layers:
        emit_labels(sp, o, placements)
    if "legend" in layers:
        emit_legend(sp, o)
    o.close("</svg>")
    return o.text()


# --------------------------------------------------------------------------
# checks
# --------------------------------------------------------------------------

class Checker:
    def __init__(self):
        self.failures = []
        self.passed = 0

    def ok(self, cond, msg):
        if cond:
            self.passed += 1
        else:
            self.failures.append(msg)

    def close(self, a, b, tol, msg):
        self.ok(abs(a - b) <= tol, "%s (%.6f vs %.6f, tol %g)" % (msg, a, b, tol))


FRACTION_RE = re.compile(r"^(\d*)R(?:/(\d+))?$")
DERIVED_RE = re.compile(r"^([a-z\-]+)\s*([+-])\s*([\d.]+)$")


def check_grid(sp, ck):
    for ring in sp.data["rings"]:
        basis, frac, r = ring["basis"], ring.get("fraction_of_R"), float(ring["r"])
        if basis == "grid":
            m = FRACTION_RE.match((frac or "").split(" ")[0])
            ck.ok(m is not None, "ring %s: unparsable grid fraction %r" % (ring["id"], frac))
            if m:
                num = float(m.group(1) or 1)
                den = float(m.group(2) or 1)
                ck.close(r, num * sp.R / den, 1e-9,
                         "ring %s != %s" % (ring["id"], frac))
        elif basis == "derived":
            m = DERIVED_RE.match((frac or "").split(" (")[0].strip())
            if m:
                base = sp.rings[m.group(1)]["r"]
                delta = float(m.group(3)) * (1 if m.group(2) == "+" else -1)
                ck.close(r, base + delta, 1e-9,
                         "ring %s != %s" % (ring["id"], frac))
    ck.close(sp.rings["beltway"]["r"], sp.R, 1e-9, "beltway != R")
    ck.close(sp.rings["yg-seam"]["r"],
             (sp.rings["yellow"]["r"] + sp.rings["green"]["r"]) / 2.0, 1e-9,
             "yg-seam != (yellow+green)/2")
    ck.close(sp.rings["core"]["r"], sp.R / 3.0, 1e-9, "core != R/3")
    ck.close(sp.rings["core-blue"]["r"], sp.rings["core"]["r"]
             - sp.const["trunk"]["slot_pitch"], 1e-9, "core-blue != core - slot pitch")
    ck.close(sp.rings["core-orange"]["r"], sp.rings["core"]["r"]
             + sp.const["trunk"]["slot_pitch"], 1e-9, "core-orange != core + slot pitch")
    ck.close(sp.rings["potomac-trail"]["r"], sp.rings["potomac-bulge"]["r"]
             + sp.const["geo"]["trail_offset_from_water_edge"], 1e-9,
             "potomac-trail != potomac-bulge + trail offset")
    ck.close(sp.const["geo"]["trail_offset_from_water_edge"],
             sp.const["geo"]["trail_stroke_width"] / 2.0
             + sp.const["geo"]["trail_sliver"], 1e-9, "sliver rule broken")
    pitch = sp.const["trunk"]["slot_pitch"]
    ck.close(sp.rings["yellow"]["r"] - sp.rings["core-orange"]["r"], pitch, 1e-9,
             "yellow != core-orange + slot pitch")
    ck.close(sp.rings["green"]["r"] - sp.rings["yellow"]["r"], pitch, 1e-9,
             "Yellow/Green pitch != slot pitch")
    ck.ok(sp.rings["yellow"]["r"] < sp.rings["green"]["r"],
          "Yellow must nest inside Green")


def check_references(sp, ck, resolved, placements):
    for st in sp.data["stations"]:
        sp.corridor(st["corridor"])
        for lid in st["lines"]:
            sp.line(lid)
    unref = sorted(set(sp.rings) - sp.refs["ring"])
    ck.ok(unref == [], "every ring must be drawn on by something, unreferenced: %s" % unref)
    ids = [st["id"] for st in sp.data["stations"]]
    ck.ok(len(set(ids)) == len(ids), "duplicate station ids")


def check_continuity(sp, ck, resolved):
    for lid, r in resolved.items():
        for kind, idx, err in r["diag"]:
            ck.ok(err <= 1e-6, "line %s segment %d: %s error %.9f" % (lid, idx, kind, err))


def check_mean_slot(sp, ck):
    """A station dot sits at the mean slot of the lines that serve it."""
    for st in sp.data["stations"]:
        at = st["at"]
        if "run" in at:
            run = sp.runs[at["run"]]
            axis_id, slot = run["axis"], at.get("slot", run.get("slot", "mid"))
        elif "axis" in at and "capsule" not in at:
            axis_id, slot = at["axis"], at.get("slot", "mid")
        else:
            continue
        slots = sp.axes[axis_id]["slots"]
        per_line = [slots[l] for l in st["lines"] if l in slots]
        if len(per_line) != len(st["lines"]) or not per_line:
            continue
        mean = sum(per_line) / len(per_line)
        actual = sp.slot(axis_id, slot)
        ck.close(actual, mean, 1e-6,
                 "%s: slot %r is not the mean slot of %s" %
                 (st["id"], slot, st["lines"]))


def check_labels(sp, ck, placements):
    """The label schema: text everywhere, rule A resolvable everywhere, and every
    placement asserted off the baselines the SVG is about to carry.

    Nothing here counts the spec against a number the spec also stores -- an
    expectation edited in the same commit as the thing it describes can only
    confirm the edit, never catch it. The censuses live in `--census`, which
    reports them without asking anyone to maintain them.
    """
    eps = float(sp.const["label"]["rule_a_degenerate_dot"])
    bounds = sp.data["checks"]["curve_geometry"]
    baseline_dy = float(sp.const["label"]["baseline_dy"])
    wide, tight, uncentred, mismeasured, misaimed = [], [], [], [], []
    for st in sp.data["stations"]:
        lab = st["label"]
        ck.ok(bool(lab.get("title")), "%s: label.title is empty" % st["id"])
        ck.ok(all(isinstance(s, str) and s for s in lab.get("title", [])
                  + lab.get("subtitle", [])),
              "%s: label lines must be non-empty strings" % st["id"])
        lp = label_placement(sp, st, placements[st["id"]])
        if abs(lp["outward"]) < eps:
            ck.ok(lp["declared_side"],
                  "%s: rule A is degenerate here (|outward| = %.4f < %g) so the "
                  "corridor, run or station must declare `side`"
                  % (st["id"], abs(lp["outward"]), eps))
        if lp["curve"] == "concentric":
            for arc in label_arcs(sp, st, lp):
                if arc["span_deg"] >= float(bounds["max_span_deg"]):
                    wide.append((st["id"], arc["span_deg"]))
                if arc["r"] < float(bounds["min_line_radius_px"]):
                    tight.append((st["id"], arc["r"]))
        elif lp["curve"] == "radial":
            r = math.hypot(lp["base"][0] - sp.centre[0], lp["base"][1] - sp.centre[1])
            if r < float(bounds["min_line_radius_px"]):
                tight.append((st["id"], r))
        rows = label_baselines(sp, st, lp)
        mid = ((rows[0][0][0] + rows[-1][0][0]) / 2.0,
               (rows[0][0][1] + rows[-1][0][1]) / 2.0)
        v = rows[0][1]
        off = ((mid[0] - lp["base"][0]) * v[0] + (mid[1] - lp["base"][1]) * v[1])
        if abs(off - baseline_dy) > 1e-6:
            uncentred.append((st["id"], lp["curve"], off))
        # ... and where the words sit, read off those same baselines. The aimed
        # point is the FIRST line's optical centre -- `baseline_dy` up from its
        # own baseline, so a wrapped label is held to the same aim a single-line
        # one is -- and it owes its station the property its anchor makes free:
        # an END-anchored block finishes on that line, so its READING LINE must
        # pass through the dot centre; a START-anchored one begins there, so the
        # point must sit DIRECTLY BELOW the dot in the corridor frame. Both are
        # `(aimed - dot).w = 0` for the direction the anchor leaves free, and
        # both are what `aim_slide` spends the along-corridor freedom on. A
        # declared `ds` is the one thing allowed to sit on top of that: it slides
        # the block along the corridor by hand, so it shows up here as exactly
        # `ds * (along.w)` and the derived aim is still what the rest of the miss
        # measures. Degenerate labels carry no aim (SPEC.md 6.2) and are not
        # checked.
        if lp["aim"] is not None:
            w = lp["along"] if lp["align"] == "start" else v
            bx = rows[0][0][0] - baseline_dy * v[0]
            by = rows[0][0][1] - baseline_dy * v[1]
            miss = (bx - lp["aim"][0]) * w[0] + (by - lp["aim"][1]) * w[1]
            hand = lp["ds"] * (lp["along"][0] * w[0] + lp["along"][1] * w[1])
            if abs(miss - hand) > 1e-9:
                misaimed.append((st["id"], lp["curve"], lp["align"], miss - hand))
        # ... and the gap the block actually leaves, read off the same emitted
        # baselines: the block's optical depth is +-(baseline_dy + extra/2)
        # about the base point, so its two edges are the first line's cap top
        # (one cap height, 2 * baseline_dy, above that baseline) and the last
        # line's baseline. Whichever projects LOWER on the offset direction is
        # the edge the ink sees, and it must sit `distance` clear of the
        # obstruction extent.
        w = (lp["nsign"] * lp["perp"][0], lp["nsign"] * lp["perp"][1])
        edges = [(rows[0][0][0] - 2.0 * baseline_dy * rows[0][1][0],
                  rows[0][0][1] - 2.0 * baseline_dy * rows[0][1][1]), rows[-1][0]]
        lead = min((p[0] - lp["origin"][0]) * w[0]
                   + (p[1] - lp["origin"][1]) * w[1] for p in edges)
        if abs(lead - lp["extent"] - lp["gap"]) > 1e-6:
            mismeasured.append((st["id"], lp["curve"], lead - lp["extent"]))
    ck.ok(not wide, "curved label arcs wider than %g deg: %s"
          % (bounds["max_span_deg"], wide))
    ck.ok(not tight, "label lines inside r = %g (curved line, or radial base): %s"
          % (bounds["min_line_radius_px"], tight))
    ck.ok(not uncentred,
          "optical centring is mode- and hemisphere-independent: the midpoint of "
          "a block's first and last baseline must sit exactly baseline_dy = %g "
          "below its base point, measured along that line's own type-down. "
          "Offenders (id, mode, offset): %s" % (baseline_dy, uncentred))
    ck.ok(not misaimed,
          "a label whose reading line is straight must hug its own station at "
          "the end the anchor makes near, up to its own declared `ds`. "
          "END-anchored: the line its baselines sit on, extended, passes through "
          "the dot centre, so the words read into the marker they name and not "
          "past it. START-anchored: the base point shares its station's "
          "along-corridor coordinate, so the words begin directly below their "
          "own dot instead of a half-span down the line from it. `ds` is the one "
          "term allowed to move a label off that, by hand and by exactly the "
          "along-corridor slide it declares; the gap cannot break either rule "
          "and neither can the block's depth, both acting across the corridor. "
          "So a residual here is the derived aim itself going wrong -- the trap "
          "SPEC.md 6.7 warns about -- and not a hand nudge. "
          "Offenders (id, mode, anchor, residual px): %s" % (misaimed,))
    ck.ok(not mismeasured,
          "`distance` is the white gap between the ink at a station and the "
          "leading edge of its block of type, so the block edge nearest the "
          "obstruction extent must sit exactly `distance` beyond it, in every "
          "mode and whatever the block's depth. Offenders (id, mode, gap): %s"
          % (mismeasured,))


def touch_geo_refs(sp):
    """Resolve every ring the geography layer names, so the unreferenced-ring
    check sees them whether or not the SVG is being written."""
    geo = sp.data["geo"]
    for park in geo["parks"] + geo.get("islands", []):
        for key in ("ring", "inner_ring", "outer_ring"):
            if key in park:
                sp.ring(park[key])
        for seg in park.get("segments", []):
            if "ring" in seg:
                sp.ring(seg["ring"])
    sp.ring(geo["water"]["potomac"]["central_bulge"]["ring"])
    sp.ring(geo["water"]["anacostia"]["ring"])
    sp.ring(geo["beltway"]["ring"])


def check_geo(sp, ck):
    west = None
    for park in sp.data["geo"]["parks"]:
        if park["id"] == "park-potomac-west":
            west = park
    ck.ok(west is not None, "park-potomac-west missing")
    if not west:
        return
    straight, fillet, arc = west["segments"][0], west["segments"][1], west["segments"][2]
    start = polar(sp.centre, sp.ring(arc["ring"]), float(arc["from_angle"]))
    ck.close(math.dist(start, tuple(fillet["to"])), 0.0, 0.01,
             "Potomac west trail fillet does not meet the trail ring")
    drawn = math.degrees(math.atan2(straight["to"][1] - straight["from"][1],
                                    straight["to"][0] - straight["from"][0]))
    ck.close(drawn, straight["angle"], 1e-3, "Potomac west bank straight angle")
    bank = sp.data["geo"]["water"]["potomac"]["nw_arm"]["west_bank_edge"]
    ck.close(straight["angle"], bank["angle"], 1e-2,
             "trail is not parallel to the west bank edge")


def check_canvas(sp, ck, resolved, placements):
    cv = sp.data["canvas"]
    pts = []
    for r in resolved.values():
        pts.extend(r["verts"])
    for pl in placements.values():
        pts.extend(d["p"] for d in pl["dots"])
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    ck.ok(0 <= min(xs) and max(xs) <= cv["width"], "geometry leaves the canvas in x")
    ck.ok(0 <= min(ys), "geometry leaves the canvas in y")


def run_checks(sp, resolved, placements):
    ck = Checker()
    touch_geo_refs(sp)
    check_grid(sp, ck)
    check_continuity(sp, ck, resolved)
    check_mean_slot(sp, ck)
    check_labels(sp, ck, placements)
    check_geo(sp, ck)
    check_canvas(sp, ck, resolved, placements)
    check_references(sp, ck, resolved, placements)
    return ck


# --------------------------------------------------------------------------
# census
# --------------------------------------------------------------------------

# (heading, regex) over the emitted SVG. Counted from the real output rather
# than predicted, so the numbers cannot drift away from what is on the page.
ELEMENT_COUNTS = (
    ("layer groups", r'<g id="layer-'),
    ("corridor label groups", r'<g id="labels-'),
    ("concentric label groups", r'<g id="label-'),
    ("lines of curved type", r"<textPath\b"),
    ("label arcs in defs", r'id="arc-'),
    ("bare <text> labels", r'<text id="label-[a-z0-9-]+"[^>]*\bx='),
    ("<tspan> continuation lines", r"<tspan\b"),
    ("station groups", r'<g id="stop-'),
    ("plain dots", r'id="dot-'),
    ("interchange circles", r'id="interchange-'),
    ("terminal circles", r'id="terminal-(?!letter-)'),
    ("terminal letters", r'id="terminal-letter-'),
    ("capsule pieces", r'id="capsule-'),
    ("station ticks", r'id="tick-'),
    ("line paths", r'id="line-'),
    ("line casings", r'id="casing-line-'),
    ("parks", r'id="park-'),
    ("water paths", r'id="(?:river|casing-river)-'),
    ("beltway", r'id="capital-beltway"'),
)


def census(sp, resolved, placements, ck, out=sys.stdout):
    """Print every count SPEC.md used to mirror by hand, derived from the spec
    and from the SVG this same invocation would write."""
    d = sp.data
    def row(key, n, extra=""):
        out.write(("  %-28s %4s   %s" % (key, n, extra)).rstrip() + "\n")

    markers = {}
    for st in d["stations"]:
        markers[st["marker"]] = markers.get(st["marker"], 0) + 1
    rings = {}
    for r in d["rings"]:
        rings[r["basis"]] = rings.get(r["basis"], 0) + 1
    radial = sum(1 for a in d["axes"].values()
                 if tuple(a["origin"]) == tuple(sp.centre))
    out.write("spec census\n")
    row("rings", len(d["rings"]),
        ", ".join("%s %d" % (k, rings[k]) for k in sorted(rings)))
    row("axes", len(d["axes"]),
        "radial %d, offset %d" % (radial, len(d["axes"]) - radial))
    row("runs", len(d["runs"]), "covering %d stations directly"
        % sum(1 for st in d["stations"] if "run" in st["at"]))
    row("corridors", len(d["corridors"]))
    row("lines", len(d["lines"]), "%d segments"
        % sum(len(ln["segments"]) for ln in d["lines"]))
    row("stations", len(d["stations"]),
        ", ".join("%s %d" % (k, markers[k]) for k in sorted(markers)))
    row("station ticks", sum(1 for st in d["stations"] if st.get("tick")))
    islands = len(d["geo"].get("islands", []))
    airport_shapes = 2 * len(d["geo"]["airports"]["items"])
    row("geo features",
        len(d["geo"]["parks"]) + len(d["geo"]["water"]) + islands + airport_shapes + 1,
        "%d parks/trails, %d water, %d islands, %d airport shapes, 1 beltway"
        % (len(d["geo"]["parks"]), len(d["geo"]["water"]), islands, airport_shapes))

    modes, overrides, declared, degenerate, aimed = {}, 0, 0, 0, 0
    eps = float(sp.const["label"]["rule_a_degenerate_dot"])
    for st in d["stations"]:
        lp = label_placement(sp, st, placements[st["id"]])
        modes[lp["curve"]] = modes.get(lp["curve"], 0) + 1
        overrides += 1 if "override" in st["label"] else 0
        declared += 1 if lp["declared_side"] else 0
        degenerate += 1 if abs(lp["outward"]) < eps else 0
        aimed += 1 if lp["aim"] is not None else 0
    out.write("\nlabel census\n")
    row("labels", len(d["stations"]), "%d derived from corridor/run parameters"
        % (len(d["stations"]) - overrides))
    row("placement overrides", overrides)
    row("curve modes", sum(modes.values()),
        ", ".join("%s %d" % (m, modes[m]) for m in CURVE_MODES if modes.get(m)))
    row("declared `side`", declared)
    row("rule A degenerate", degenerate, "(each must declare `side`)")
    row("aimed labels", aimed,
        "(straight, oblique to their corridor, not middle-anchored)")

    svg = build_svg(sp, resolved, placements, set(LAYER_GROUPS))
    out.write("\nelement census (%s, all layers)\n" % OUT_PATH)
    for name, pattern in ELEMENT_COUNTS:
        row(name, len(re.findall(pattern, svg)))

    out.write("\nchecks\n")
    row("assertions passed", ck.passed)


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def resolve_all(sp):
    resolved = {}
    for ln in sp.data["lines"]:
        d, verts, diag = resolve_line(sp, ln)
        resolved[ln["id"]] = {"d": d, "verts": verts, "diag": diag}
    placements = {st["id"]: resolve_station(sp, st) for st in sp.data["stations"]}
    return resolved, placements


def parse_layers(value):
    if value in (None, "", "all"):
        return set(LAYER_GROUPS)
    picked = [v.strip() for v in value.split(",") if v.strip()]
    bad = [v for v in picked if v not in LAYER_GROUPS]
    if bad:
        raise SystemExit("unknown layer group(s): %s (choose from %s)"
                         % (", ".join(bad), ", ".join(LAYER_GROUPS)))
    return set(picked)


EXPORTS = {
    "pdf": (["--export-type=pdf", "--export-text-to-path"], "text as outlines"),
    "png": (["--export-type=png", "--export-width=4000", "--export-background=#ffffff"],
            "4000 px wide"),
}


def export(svg_path, kind):
    """Write a PDF or a PNG beside the SVG. The PDF has every text as outlines
    and the PNG is pixels, so each looks the same on a machine that does not
    have the map's font. The conversion is Inkscape's, which sets the type with
    the fonts of THIS machine: the map's font must be installed here."""
    inkscape = shutil.which("inkscape")
    if not inkscape:
        raise SpecError("--%s needs Inkscape on the PATH" % kind)
    path = os.path.splitext(svg_path)[0] + "." + kind
    done = subprocess.run([inkscape, svg_path] + EXPORTS[kind][0] + ["--export-filename=" + path],
                          capture_output=True, text=True)
    if done.returncode or not os.path.exists(path):
        raise SpecError("Inkscape did not write %s:\n%s" % (path, done.stderr.strip()))
    return path


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--spec", default=SPEC_PATH)
    ap.add_argument("-o", "--out", default=OUT_PATH)
    ap.add_argument("--check", action="store_true",
                    help="validate the spec and write nothing")
    ap.add_argument("--census", action="store_true",
                    help="print the spec/label/element counts and write nothing")
    ap.add_argument("--force", action="store_true",
                    help="carry on past failing checks; the exit status still "
                         "reports the failure")
    ap.add_argument("--layers", default="all",
                    help="comma-separated subset of %s" % ",".join(LAYER_GROUPS))
    ap.add_argument("--pdf", action="store_true",
                    help="also write a PDF beside the SVG, its text as outlines "
                         "(needs Inkscape and the map's font on this machine)")
    ap.add_argument("--png", action="store_true",
                    help="also write a PNG beside the SVG, 4000 px wide on white "
                         "(same needs as --pdf)")
    args = ap.parse_args(argv)

    root = os.path.dirname(os.path.abspath(args.spec)) or "."
    with open(args.spec, encoding="utf-8") as fh:
        sp = Spec(json.load(fh))

    resolved, placements = resolve_all(sp)
    ck = run_checks(sp, resolved, placements)
    if ck.failures:
        sys.stderr.write("CHECK %s (%d of %d assertions):\n"
                         % ("FAILED (forced)" if args.force else "FAILED",
                            len(ck.failures), len(ck.failures) + ck.passed))
        for f in ck.failures:
            sys.stderr.write("  - %s\n" % f)
        if not args.force:
            return 1
    status = 1 if ck.failures else 0
    if args.census:
        census(sp, resolved, placements, ck)
        return status
    if args.check:
        print("checks: %d assertions passed" % ck.passed)
        return status

    svg = build_svg(sp, resolved, placements, parse_layers(args.layers))
    out = args.out if os.path.isabs(args.out) else os.path.join(root, args.out)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(svg)
    print("%s: %d bytes, %d assertions passed%s"
          % (out, len(svg.encode("utf-8")), ck.passed,
             ", %d FAILED" % len(ck.failures) if ck.failures else ""))
    for kind in EXPORTS:
        if getattr(args, kind):
            print("%s: %s" % (export(out, kind), EXPORTS[kind][1]))
    return status


if __name__ == "__main__":
    sys.exit(main())
