#!/usr/bin/env python3
"""Decomposes each station's label offset into ink, block depth and white gap.

`distance` is the white paper between the ink at a station and the leading
edge of its block of type. This prints the three terms the generator
solves for it -- `extent` (how far the station's own marker or its band's outer
casing protrudes), `lift` (how much of the block's optical half-depth is spent
across the corridor), and `gap` (the paper that is left, which is `distance`) --
plus the total offset they sum to. Per-corridor spread at the foot shows which
corridors read evenly.
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
        lp = g.label_placement(sp, st, placements[st["id"]])
        # base = origin + n * perp + (along terms); perp _|_ along, so the
        # across-corridor offset n is just the perp component of (base - origin).
        n = lp["nsign"] * ((lp["base"][0] - lp["origin"][0]) * lp["perp"][0]
                           + (lp["base"][1] - lp["origin"][1]) * lp["perp"][1])
        out.append({"cor": st["corridor"], "run": st["at"].get("run", ""),
                    "id": st["id"], "marker": st["marker"], "curve": lp["curve"],
                    "extent": lp["extent"], "gap": lp["gap"],
                    "lift": n - lp["extent"] - lp["gap"], "offset": n})
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("spec", nargs="?", default=DEFAULT_SPEC,
                    help="spec.json to tabulate (default: the repo's)")
    args = ap.parse_args()
    table = rows(args.spec)
    print("%-6s %-18s %-30s %-11s %-11s %8s %6s %8s %8s"
          % ("cor", "run", "id", "marker", "curve",
             "extent", "lift", "gap", "offset"))
    for r in table:
        print("%-6s %-18s %-30s %-11s %-11s %8.3f %6.2f %8.3f %8.3f"
              % (r["cor"], r["run"], r["id"], r["marker"], r["curve"],
                 r["extent"], r["lift"], r["gap"], r["offset"]))
    by = {}
    for r in table:
        by.setdefault(r["cor"], []).append(r["gap"])
    print()
    for cor in sorted(by):
        v = by[cor]
        print("%-6s n=%2d  gap min %8.3f  max %8.3f  spread %7.3f"
              % (cor, len(v), min(v), max(v), max(v) - min(v)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
