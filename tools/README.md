# tools/

Measurement scripts for `DC-Metro.svg`. They answer the questions `generate.py`'s assertions do
not: an assertion proves a rule was applied, these measure what the rule *produced* — how close two
labels came, how much white a station actually shows, whether a change helped or just moved the
problem.

All are Python 3, **stdlib only**, and import `generate.py` from the repo root, so they see exactly
the geometry the generator resolved rather than a re-parse of the SVG. Each takes the spec path as a
positional argument and defaults to the repo's own `spec.json`, so the bare command is the common
case. `measure.py` is the one exception to stdlib-only in spirit: it shells out to **Inkscape**,
which must be on `PATH`.

| script | what it measures |
| --- | --- |
| `collide.py` | label-vs-label and label-vs-marker overlaps, as oriented boxes. Curved counts read slightly high — an arc's box is its chord padded by the sagitta. |
| `strokes.py` | each label's clearance from the nearest line-path casing ink, by flattening the 7 line paths at 0.5 px. Negative means the type sits inside the casing. |
| `gaps.py` | the three terms of each station's label offset — `extent` (ink), `lift` (block depth spent across the corridor) and `gap` (the white that is left, i.e. `distance`). Per-corridor spread at the foot. |
| `aim.py` | how far each label sits from the station it names along its aim direction — the quantity the aim rule drives to zero for the 19 straight labels. |
| `measure.py` | the **rendered** white gap: rasterises the map twice at one crop and walks pixels. The check on whether the solved gap is the gap the eye reads. Needs Inkscape. |
| `delta.py` | the collision census diff between two specs, overall and per `--region`. "Did that change help?" |
| `png.py` | not a CLI — a minimal stdlib PNG reader for `measure.py`. |

```sh
./tools/collide.py                                  # census the repo spec
./tools/strokes.py --corridors W,E                  # just the two trunks
./tools/gaps.py | tail -20                          # per-corridor gap spread
./tools/aim.py
./tools/measure.py east-falls-church --box 360,960,180,140 --scale 3
./tools/delta.py before.json --region "west=vienna,dunn-loring,east-falls-church"
```

## Verification protocol

Two lanes. Which one applies depends on whether the change can move a label relative to the ink.

### Fast lane — the default

For anything that does not change placement semantics (refactors, doc edits, tooling, a constant
that only affects colour):

1. `./generate.py` — runs clean, every assertion passes. No assertion checks the spec against a
   count stored in the spec, so a design edit never "fails" a census: if an assertion fires, the
   geometry or the schema is actually wrong.
2. The SVG is byte-identical if it should be: `md5 DC-Metro.svg` before and after.
3. `./generate.py --census` — the counts are sane.
4. One render, and one targeted crop of the region touched.
   **Never verify labels with `rsvg-convert`.** librsvg silently drops `<textPath>`, so every
   concentric label vanishes from the render and it still exits 0. Only
   `inkscape --export-type=png` or a browser draws the full label set (SPEC.md §6.7,
   "Renderers").

### Full audit — for placement-semantics changes

Anything that moves where a label, marker or line sits: a new rule in `label_placement`, a change to
`distance` / `ds` / `rotate` / `curve` semantics, a re-pinned ring, a new run.

1. Everything in the fast lane.
2. **Censuses** — `collide.py` and `strokes.py` before and after. A change that fixes one region and
   quietly breaks another shows up here and nowhere else.
3. **Tables** — `gaps.py` and `aim.py`. Read the per-corridor spread, not just the totals: the point
   of the derived schema is that stations on one corridor read the *same*, so a spread that opens up
   is a regression even when no assertion fires and no boxes overlap.
4. **`delta.py`** against the pre-change spec, with `--region` for the areas you meant to affect —
   which makes the intended change and the collateral separable.
5. **Negative tests** — break the rule on purpose and confirm the assertion catches it. A check that
   has never failed is a check that has not been tested. `measure.py` is the tiebreak when the solved
   number and the rendered number disagree.

The two lanes exist because the assertions are strong on *rules* and silent on *outcomes*: every one
of them will happily pass while two labels sit on top of each other.

## Docs protocol

Update the relevant `spec.json` `*_note` fields **immediately** alongside any semantic change. Batch
the matching SPEC.md prose edits into a **session-end docs-sync pass** rather than mid-task — keeps
the machine-readable notes always current without churning the prose on every intermediate step.

## Where the label code lives

The rules are SPEC.md §6 and the per-object reasons are `spec.json` notes; this is only the map from
one to the other, for when an engine change is what you are making.

| touching | rules | key functions in `generate.py` |
| --- | --- | --- |
| the frame | §6.1 | `label_basis`, `label_origin`, `extreme_dot` |
| gap, aim, the fixed point | §6.2 | `label_placement` (orchestrator), `label_default`, `obstruction_extent`, `marker_outer_r`, `band_offsets`, `aim_degenerate`, `aim_slide`, `label_extra` |
| side — rule A | §6.3 | `outward_dot`, called from inside `label_placement` |
| `rotate` | §6.4 | `tangential_rotate`, `upright_rotate` |
| curve modes | §6.7 | `type_down`, `reading_dir`, `label_align`, `label_arcs`, `label_baselines` (recovers the emitted geometry for the checks) |
| emission | §11.1 | `emit_straight_label`, `emit_curved_label`, `emit_labels` |
| assertions | §11.2 | `check_labels` |

The parameter keys are `LABEL_KEYS`; a key outside it raises `SpecError` wherever it appears. The
placement fixed point iterates `LABEL_OFFSET_PASSES` times and fails the build rather than truncate.
