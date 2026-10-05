#!/usr/bin/env python3
"""Measures the RENDERED white gap between a station's ink and its own label.

The only tool here that rasterises. It renders the map twice at the same crop --
once with the line and station layers, once with the label layer stripped down to
a single label -- then reports the shortest distance from any pixel of that
isolated label to any pixel of the ink. That is the white the eye actually reads
under a station, as opposed to the gap the generator solved for (tools/gaps.py).

REQUIRES INKSCAPE on PATH; never rsvg-convert, which drops <textPath> and would
render 73 of the 98 labels as nothing at all. Writes its scratch SVG/PNG files
to --workdir (default: a temp dir, removed on exit).
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import png  # noqa: E402

DEFAULT_SPEC = os.path.join(REPO, "spec.json")
GENERATE = os.path.join(REPO, "generate.py")
LABEL_RE = re.compile(r'<(?:text|g)\b[^>]*\bid="label-([a-z0-9-]+)"')


def emit(spec, layers, work, tag):
    """Run generate.py for a subset of layers."""
    out = os.path.join(work, "m-%s.svg" % tag)
    subprocess.run([sys.executable, GENERATE, "--spec", spec, "-o", out,
                    "--layers", layers], check=True, capture_output=True)
    return out


def isolate(svg, sid, out):
    """Drop every label element but `sid` (its <defs> arcs can stay)."""
    keep, depth, kept = [], 0, None
    for line in open(svg, encoding="utf-8"):
        m = LABEL_RE.search(line)
        if m and kept is None:
            kept = m.group(1) == sid
            depth = 0 if line.rstrip().endswith("/>") or "</" in line else 1
            if kept:
                keep.append(line)
            if depth == 0:
                kept = None
            continue
        if kept is not None:
            if kept:
                keep.append(line)
            s = line.strip()
            if s.startswith("</"):
                depth -= 1
            elif s.startswith("<") and not s.endswith("/>") and "</" not in s:
                depth += 1
            if depth == 0:
                kept = None
            continue
        keep.append(line)
    open(out, "w", encoding="utf-8").write("".join(keep))
    return out


def raster(svg, box, scale, work, tag, thresh=140):
    """Render `svg` cropped to `box` and return the set of inked pixel columns."""
    tmp = os.path.join(work, "m-%s-crop.svg" % tag)
    out = os.path.join(work, "m-%s.png" % tag)
    src = open(svg, encoding="utf-8").read()
    src = re.sub(r'viewBox="[^"]*" width="[^"]*" height="[^"]*"',
                 'viewBox="%g %g %g %g" width="%g" height="%g"'
                 % (box[0], box[1], box[2], box[3], box[2], box[3]), src, count=1)
    open(tmp, "w", encoding="utf-8").write(src)
    subprocess.run(["inkscape", "-w", str(int(box[2] * scale)), "-b", "white",
                    tmp, "-o", out], check=True, capture_output=True)
    img = png.load(out)
    w, h = img[0], img[1]
    cols = {}
    for y in range(h):
        for x in range(w):
            r, gr, b = png.px(img, x, y)
            if (r * 299 + gr * 587 + b * 114) / 1000.0 < thresh:
                cols.setdefault(x, []).append(y)
    return cols, w, h


def chamfer(cols, w, h):
    """Distance (in 3-4 chamfer units) from every cell to the nearest set cell.
    Two passes over the grid, stdlib only."""
    INF = 1 << 24
    d = [INF] * (w * h)
    for x, ys in cols.items():
        for y in ys:
            d[y * w + x] = 0
    for y in range(h):
        base = y * w
        for x in range(w):
            i = base + x
            v = d[i]
            if v == 0:
                continue
            if y:
                if x:
                    v = min(v, d[i - w - 1] + 4)
                v = min(v, d[i - w] + 3)
                if x + 1 < w:
                    v = min(v, d[i - w + 1] + 4)
            if x:
                v = min(v, d[i - 1] + 3)
            d[i] = v
    for y in range(h - 1, -1, -1):
        base = y * w
        for x in range(w - 1, -1, -1):
            i = base + x
            v = d[i]
            if v == 0:
                continue
            if y + 1 < h:
                if x + 1 < w:
                    v = min(v, d[i + w + 1] + 4)
                v = min(v, d[i + w] + 3)
                if x:
                    v = min(v, d[i + w - 1] + 4)
            if x + 1 < w:
                v = min(v, d[i + 1] + 3)
            d[i] = v
    return d


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("station", nargs="+", help="station id(s) to measure")
    ap.add_argument("--spec", default=DEFAULT_SPEC)
    ap.add_argument("--box", default=None, metavar="X0,Y0,W,H",
                   help="crop in map units (default: the whole 2000x2000 page)")
    ap.add_argument("--scale", type=float, default=4.0,
                    help="render scale, px per map unit (default 4)")
    ap.add_argument("--workdir", default=None,
                    help="keep scratch renders here instead of a temp dir")
    args = ap.parse_args()
    if not shutil.which("inkscape"):
        raise SystemExit("measure.py needs Inkscape on PATH (see the docstring)")
    if args.box:
        box = tuple(float(v) for v in args.box.split(","))
    else:
        canvas = json.load(open(args.spec, encoding="utf-8"))["canvas"]
        box = (0.0, 0.0, float(canvas["width"]), float(canvas["height"]))
    work = args.workdir or tempfile.mkdtemp(prefix="dcmetro-measure-")
    os.makedirs(work, exist_ok=True)
    try:
        ink, w, h = raster(emit(args.spec, "lines,stations", work, "ink"),
                           box, args.scale, work, "ink", 235)
        dt = chamfer(ink, w, h)
        labels = emit(args.spec, "labels", work, "lab")
        for sid in args.station:
            one = isolate(labels, sid, os.path.join(work, "m-one.svg"))
            lab, _, _ = raster(one, box, args.scale, work, "one")
            if not lab:
                print("%-30s no label pixels in the crop" % sid)
                continue
            best, at = None, None
            for x, ys in lab.items():
                for y in ys:
                    v = dt[y * w + x]
                    if best is None or v < best:
                        best, at = v, (box[0] + x / args.scale,
                                       box[1] + y / args.scale)
            print("%-30s white gap %6.2f px  (nearest label pixel at %.1f, %.1f)"
                  % (sid, (best / 3.0 - 1.0) / args.scale, at[0], at[1]))
    finally:
        if not args.workdir:
            shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
