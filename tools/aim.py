#!/usr/bin/env python3
"""Tabulates how far each label sits from the station it names, along its aim.

`miss` is the quantity the aim rule drives to zero and `run_checks` asserts: the offset
from the optical centre of the label's FIRST line to the station's dot centre
along the direction its own anchor leaves free -- the type's down for an
END-anchored block (whose reading line must pass through the dot) and the
corridor's own direction for a START-anchored one (which must begin directly
below the dot). The first line rather than the base point, so that a wrapped
label is held to the same aim a single-line one is: the base point is the centre
of the whole block, half its extra advance further down. `xerr` re-expresses
it as an along-corridor distance, which is how far down the line the words appear
to have walked -- the same number as `miss` under the start rule, which is already
measured there. Only the 19 aimed (straight, oblique) labels are held to it;
concentric type cannot aim and is reported as `-`.
"""
import argparse
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import generate as g  # noqa: E402

DEFAULT_SPEC = os.path.join(REPO, "spec.json")


def rows(spec_path):
    sp = g.Spec(json.load(open(spec_path, encoding="utf-8")))
    _resolved, placements = g.resolve_all(sp)
    out = []
    for st in sp.data["stations"]:
        placed = placements[st["id"]]
        lp = g.label_placement(sp, st, placed)
        _perp, alng = g.label_basis(sp, st, lp["curve"], placed["anchor"])
        dwn = g.type_down(sp, lp["base"], lp["rotate"], lp["curve"])
        ad = alng[0] * dwn[0] + alng[1] * dwn[1]
        aim = placed["anchor"]
        # the direction the anchor leaves free: an end-anchored block finishes
        # on its first line's optical centre, so that reading LINE must hit the
        # dot; a start-anchored one begins there, so the point sits under it.
        start = lp["align"] == "start"
        w = alng if start else dwn
        lift = g.label_extra(sp, st, lp["wrap_dy"]) / 2.0
        pt = (lp["base"][0] - lift * dwn[0], lp["base"][1] - lift * dwn[1])
        miss = ((pt[0] - aim[0]) * w[0] + (pt[1] - aim[1]) * w[1])
        xerr = miss if start else (-miss / ad if abs(ad) > 1e-9 else float("nan"))
        out.append({"cor": st["corridor"], "run": st["at"].get("run", ""),
                    "id": st["id"], "curve": lp["curve"], "align": lp["align"],
                    "a_down": ad, "miss": miss, "xerr": xerr,
                    "aimed": lp["aim"] is not None,
                    "base": lp["base"]})
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("spec", nargs="?", default=DEFAULT_SPEC,
                    help="spec.json to tabulate (default: the repo's)")
    args = ap.parse_args()
    table = rows(args.spec)
    print("%-6s %-18s %-30s %-11s %-6s %8s %9s %9s %5s"
          % ("cor", "run", "id", "curve", "align", "a.down", "miss", "xerr", "aim"))
    for r in table:
        print("%-6s %-18s %-30s %-11s %-6s %8.4f %9.3f %9.3f %5s"
              % (r["cor"], r["run"], r["id"], r["curve"], r["align"],
                 r["a_down"], r["miss"], r["xerr"],
                 "yes" if r["aimed"] else "-"))
    aimed = [r for r in table if r["aimed"]]
    print("\n%d aimed labels, worst |xerr| %.3g px"
          % (len(aimed), max((abs(r["xerr"]) for r in aimed), default=0.0)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
