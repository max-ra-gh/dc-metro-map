#!/usr/bin/env python3
"""Diffs the collision census between two specs, overall and per region.

Answers "did that change help?" — it runs collide.py over a before-spec and an
after-spec and reports which overlapping pairs went away and which are new.
`--region` narrows the report to pairs touching a named set of stations; pass it
more than once to get one block per region of interest.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collide  # noqa: E402


def touching(pairs, region):
    return {p for p in pairs if region is None or any(s in region for s in p)}


def report(name, region, before, after):
    b_ll, b_lm = before
    a_ll, a_lm = after
    rb_ll, ra_ll = touching(b_ll, region), touching(a_ll, region)
    rb_lm, ra_lm = touching(b_lm, region), touching(a_lm, region)
    print("== %s ==" % name)
    print("  label-label   before %2d -> after %2d" % (len(rb_ll), len(ra_ll)))
    print("     gone : %s" % ", ".join("%s/%s" % p for p in sorted(rb_ll - ra_ll)))
    print("     new  : %s" % ", ".join("%s/%s" % p for p in sorted(ra_ll - rb_ll)))
    print("  label-marker  before %2d -> after %2d" % (len(rb_lm), len(ra_lm)))
    print("     gone : %s" % ", ".join("%s>%s" % p for p in sorted(rb_lm - ra_lm)))
    print("     new  : %s" % ", ".join("%s>%s" % p for p in sorted(ra_lm - rb_lm)))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("before", help="spec.json before the change")
    ap.add_argument("after", nargs="?", default=collide.DEFAULT_SPEC,
                    help="spec.json after it (default: the repo's)")
    ap.add_argument("--region", action="append", default=[],
                    metavar="NAME=id,id,...",
                    help="report a named subset of station ids; repeatable")
    args = ap.parse_args()
    before, after = collide.census(args.before), collide.census(args.after)
    report("whole map", None, before, after)
    for spec in args.region:
        if "=" not in spec:
            raise SystemExit("--region wants NAME=id,id,... (got %r)" % spec)
        name, ids = spec.split("=", 1)
        report(name, set(i.strip() for i in ids.split(",") if i.strip()),
               before, after)
    return 0


if __name__ == "__main__":
    sys.exit(main())
