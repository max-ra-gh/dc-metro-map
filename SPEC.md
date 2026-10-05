# DC-Metro.svg — declarative spec

Companion to `spec.json`. **`spec.json` is the source of record**: every value, and the reasoning
for every individual item, lives there — the reasoning as a `note` on the object it is about. This
document states the schema and the geometric rules the generator must honour, and enumerates nothing
it can point at instead.

`generate.py` reads `spec.json` and writes `DC-Metro.svg`. The SVG is a **build artefact** — never
hand-edit it; edit `spec.json` and re-run. See §11.

Two decisions are folded into the geometry:

1. **Canonical nesting** is Yellow inner, Green outer, and the two arcs never cross twice.
2. **Canonical grid** is a 2000 × 2000 canvas about centre **(1000, 1000)** with the Capital Beltway
   at **R = 720**, and rings expressed as clean fractions of R — §3.

---

## 1. Files

| File | Role |
| --- | --- |
| `spec.json` | machine-readable single source of truth (plain JSON, no schema deps) |
| `SPEC.md` | this document |
| `generate.py` | the generator — Python 3, stdlib only, deterministic |
| `DC-Metro.svg` | **build output**. Regenerate with `./generate.py`; never hand-edit |
| `DC-Metro.pdf` | **release output**, written by `./generate.py --pdf`: the same page with every text as outlines, so it does not need the map's font (§11) |
| `tools/` | measurement scripts — collisions, clearances, gaps, aim, rendered white. See `tools/README.md` |

Everything is stdlib-friendly: numbers, strings, lists, objects. No `$ref`, no expressions, no units
other than SVG user units and degrees.

---

## 2. Coordinate and angle conventions

The canvas is **2000 × 2000**, `viewBox="0 0 2000 2000"`, centre **(1000, 1000)**. Every construct is
positioned relative to that centre.

**Angle convention** (`conventions.angle_*` in the JSON):

* Degrees, `0°` = the **+x** axis (due east of centre).
* Angle **increases clockwise on screen**, because SVG's y axis points down. So
  `90° = due south`, `180° = west`, `270° = north`.
* `x = 1000 + r·cos θ`, `y = 1000 + r·sin θ`.
* Arc direction: `"dir": "cw"` means increasing θ → SVG `sweep-flag = 1`; `"dir": "ccw"` means
  decreasing θ → `sweep-flag = 0`. `large-arc-flag = 1` iff `|Δθ| > 180`. Wrap matters: an arc that
  must go the long way round is written `"to": {"angle": 360}`, not `0`.

---

## 3. The grid

`spec.json.grid` defines **R = 720**, the Capital Beltway radius, and `spec.json.rings` holds the
complete set of radii in use. Every ring declares a `basis`:

* **`grid`** — an exact fraction of R, written out in `fraction_of_R`.
* **`derived`** — follows from another ring by a rule stated in the same field, because a
  relationship in the map must keep holding. `grid.off_grid` collects those rules in one place.

Why any particular ring has the radius it has is on that ring, in `rings[].name`.

**One pitch, 28.** The nested core circles are a single band stack, each 28 out from the last, and
corridor slots step by the same 28 (`constants.trunk.slot_pitch`). The stack is anchored the way
every corridor is: the **station axis is the grid-pinned member** (`core` = R/3), and the ink rings
derive from it at pitch multiples — so the pitch rule supersedes the fraction grid for every ring of
the stack, and any seam between two of them is derived, with no clean fraction either.

### 3.1 Rings

A **ring** is a circle concentric with (1000,1000). A point on a ring is `(ring, angle)`.

### 3.2 Axes and slots

Every straight guide in the map is an **axis**:

```json
"east-trunk": { "origin": [1000, 1028], "angle": 0.0,
                "slots": { "orange": -28, "silver": 0, "blue+silver": 14, "blue": 28 } }
```

A point on an axis is `origin + s·d̂ + n·n̂`, where `d̂ = (cos a, sin a)` and `n̂ = (−sin a, cos a)`.

* `s` — signed distance **along** the axis, measured from `origin`.
* `n` — signed **slot** offset perpendicular to it.

Radial spokes have `origin = [1000,1000]`, so `s` is simply the radius. Non-radial axes put `origin`
at the natural corner or at the foot of the perpendicular from the centre, so `s` reads as "distance
along the corridor".

**Slot rule.** Parallel lines sharing a corridor sit at multiples of **14** either side of the axis;
adjacent lines are **28** apart. A **station dot sits at the mean slot of the lines that serve it** —
one rule, and an asserted one (§11.2), with no exceptions.

### 3.3 Runs

An arithmetic sequence of stations along one axis:

```json
"south-leg": { "axis": "south", "slot": "mid", "s0": 295.0254, "step": 59.1556, "count": 9 }
```

A station then refers to `{"run": "south-leg", "i": 3}`, optionally overriding `slot` where one end
of the run has branched away onto a different line. A run may also carry a `label_default` (§6) — a
run is the smallest object that can declare a label side or curve mode for a group.

A station that sits on no run is placed directly instead — on an axis at an explicit `s`, on a ring
at an angle, or pinned to a ring crossing with `at_ring` (§5).

**Where both ends of a run are pinned** — to a ring, to another run's endpoint, to the Beltway — the
**step is solved from them rather than declared by eye**, and the solution is recorded in the run's
own `note`. A run is also the unit at which a change of spacing regime is expressed: an arm that is
deliberately open on one side of a ring and compressed on the other is two runs, because one
arithmetic sequence cannot say that.

---

## 4. Lines

```json
{ "id": "silver", "name": "Silver line", "colour": "#919D9D", "casing": true,
  "segments": [ … ] }
```

Segments are an **ordered, point-continuous** list. Four kinds:

| kind | meaning |
| --- | --- |
| `straight` | along `axis` at `slot`, from an endpoint spec to an endpoint spec |
| `arc` | along `ring`, starting at the previous point, ending at `to`, in direction `dir` |
| `riser` | short straight perpendicular to `axis`, from the current slot to `to_slot` |
| `join` | straight from the previous point to an explicitly named target point |

An **endpoint spec** (`from` / `to` on a `straight`, `to` on an `arc`) is one of:

* `{"s": 762}` — a distance along the current axis
* `{"ring": "core-orange"}` — where the current slot line meets that ring (`"branch": "neg"` picks
  the other intersection)
* `{"axis": "nc-diagonal", "slot": "orange"}` — where the current slot line meets another slot line
* `{"point": [x, y]}` — literal
* `{"prev": true}` — continue from the previous segment's endpoint
* on an `arc`, `{"angle": 309}` or the axis/slot form above (resolved to an angle)

A slot line cuts a ring twice. The default is the **larger `s`** — further along the axis direction —
and `"branch": "neg"` picks the other.

Because the start of an arc is always "wherever the previous segment ended", **ring entry angles are
derived, never stored** — the spec says which slot line meets which ring, and the angle is checked
rather than declared (§11.2).

### 4.0 Service patterns

`spec.json.service_patterns` marks the parts of a line that only some of its trains serve - the
Yellow line north of Mt Vernon Sq and the two east branches of the Silver line - as WMATA's map
marks them "served by every other train". A section names its `line` and repeats that part of the
line's route as `segments`, in the lines' own vocabulary. It is drawn right after its line, over the
line's colour and under every line drawn later, as a second stroke of the line's width in `stroke`,
dashed by `dasharray`: white bars across the line. Ids are `service-<section>`. The legend's line
key ends with a sample of the same pattern (`legend.lines.service`).

### 4.1 Join constructions

* **Point-continuity bend.** Every corner on a metro line is a hard corner: the two segments share an
  endpoint exactly and nothing is rounded. `stroke-linejoin` is `round`, so the 26-wide stroke
  supplies the visual radius (13).
* **Riser.** Where a core-ring arc meets a trunk, the arc terminates at the slot line that passes
  through the centre and a short perpendicular riser drops to the line's own slot; a line already on
  that slot needs none.
* **Slot-line intersection.** Where two straight corridors meet at an angle, the corner is the
  intersection of the *offset* lines, not of the axes: the two lines turn at `n = ±14` about the
  shared corner.
* **Fillets (r = 14).** Not used on metro lines at all. The one true fillet in the drawing belongs to
  a geo trail and is solved for **exact tangency** (`spec.json.geo`); its radius equals the slot
  offset.
* **Casing.** Every line is drawn twice: white `stroke-width: 30`, then the colour at `26`. Same `d`,
  same order, casing first. Layer draw order is `layers[].draw_order`, so a later line crosses an
  earlier one with a 2 px white gap.
* **Sliver.** Green bank trails run parallel to water edges with a paper gap: the trail centreline
  sits `trail_stroke_width/2 + sliver` outside the water edge.

---

## 5. Stations

```json
{ "name": "Federal Center SW", "id": "federal-center-sw", "corridor": "E",
  "lines": ["blue","orange","silver"], "marker": "dot",
  "at": { "run": "east-trunk-inner", "i": 0 },
  "tick": true,
  "label": { "title": ["Federal Center SW"] } }
```

`at` is one of:

* `{"ring": …, "angle": …}`
* `{"run": …, "i": …, "slot"?: …}`
* `{"axis": …, "slot": …, "s": …}`
* `{"axis": …, "slot": …, "at_ring": …}` — pinned to a ring crossing
* `{"dots": [ … ]}` — a multi-platform terminal, one placement per line
* `{"capsule": {axis, slot, from, to}}` — a bar between two platform dots on one axis

`marker` ∈ `dot` | `interchange` | `terminal` | `capsule`.

**Marker geometry lives once in `constants`** — `station_dot`, `interchange_dot`, `terminal_dot`,
`capsule` — and never per station. A terminal is filled with its line's colour and carries a white
letter; a capsule's two bar widths are *derived* from the interchange ring's radius and stroke, and
`constants.capsule.derivation` records how.

**Ticks.** A station with `"tick": true` carries a white, round-capped tick (`constants.station_tick`)
drawn perpendicular to the line through the dot and centred on it: radial on a ring, normal to the
axis on a straight. A tick is white on white paper and puts down no ink, which is why nothing
measures clearance from it (§6.5).

---

## 6. Labels

> Which of `generate.py`'s functions implement which of the rules below is mapped in
> `tools/README.md`.

**A station carries label text, and placement only where it has been decided by hand.**

```json
"label": { "title": ["Foggy Bottom-GWU"], "subtitle": ["Kennedy Center"],
           "override": { "side": "right", "ds": 0.0, "curve": "straight" } }
```

`title` lines are 14 px / 700, `subtitle` lines 9 px / 400; each list is one line of type per entry,
so line breaks are authored, not computed. Most stations carry text alone. The few that also carry a
`label.override` are design decisions about one label each, and each says why in its own
`label.note`, beside the numbers it explains. Placement otherwise lives in
`corridors[].label_default`, `runs[].label_default` and `constants.label`.

### 6.1 The frame

A label is placed in its **corridor frame**, not relative to its dot:

| station `at` | origin | `perp` | `along` |
| --- | --- | --- | --- |
| `run` / `axis` / `capsule` / `dots` | the station's foot on the axis **centreline** | axis normal | axis direction |
| `ring` | the dot | radially outward | tangential |

Measuring from the centreline absorbs the slot term: a station parked off the mean slot needs no hand
value for it, and its label aligns to the corridor column rather than to a dot 14 px off it. For a
multi-platform station the foot is taken from the **extreme dot on the label's side**, not the mean;
a capsule's dots both sit on the centreline, and the tie breaks to the **first** dot, the capsule's
`from` end, because the capsule overrides measure off it.

**`radial` type overrides the frame.** Type set on the outward radial takes its offset on that radial
too, whatever the placement: a `curve: radial` label uses radially outward / tangential even when its
station sits on an axis. The two it decides are the corridor-frame pair on the New Carrollton branch
— Minnesota Av, whose `nc-diagonal` normal points at 45°, and New Carrollton, whose `nc-exit` normal
points at 67.1°. Both are close enough to the *tangent* of the ring the type stands off that the axis
frame slid the block along the ring rather than away from it, carrying Minnesota Av's off its corner
and New Carrollton's across the Beltway. Ring placements already resolve radially, so this only adds
the axis-placed radial labels to them.

The frame fixes the *direction* of the offset and the point it is measured from; how much white paper
the offset buys is `distance`'s answer (§6.2).

### 6.2 The six parameters

| key | meaning |
| --- | --- |
| `distance` | the **white gap** between the ink at the station and the leading edge of the block of type, along the frame's perpendicular (magnitude; the side supplies the sign). Optional — inherits `constants.label.distance` |
| `ds` | slide along the axis (tangentially, for a ring placement), **on top of the derived aim** — the one term that can carry a label off its own station, so the build asserts a label sits on its aim up to *exactly* the `ds` it declares and no further. A rank is aimed, not nudged: `ds` belongs on single stations |
| `side` | `"left"` \| `"right"` — declared only where the derivation of §6.3 fails |
| `align` | `text-anchor`; derived, `"start"` on the right side and `"end"` on the left — and it picks which end of the block the `aim` hugs to the station. `"middle"` is declared only, never derived |
| `rotate` | number, or `{"axis": id}` for a tangential derivation — **straight labels only**; `concentric` and `radial` derive their own turn (§6.7) |
| `curve` | `"concentric"` (default) \| `"radial"` \| `"straight"`; the shape the line of type is set on (§6.7) |

Resolution order is **corridor default → run default → station override**, each layer overriding key
by key. A `label.override` takes the same seven keys (the six above plus `wrap_dy`) and no others: an
unknown key anywhere in the chain is a build error, never a silently ignored one (§11.2).

The final point is

```
label = origin + n·perp + (aim + ds)·along + (baseline_dy − lift)·down
n     = ±(extent + distance + half·|down·perp|), signed so that the label lies
        on the declared side
lift  = extra/2, the block-centring lift; first line's optical centre is
        label − lift·down, and lift is 0 for a single-line label
half  = baseline_dy + lift, the block's own optical half-depth
extent= how far the ink at the station protrudes along ±perp (below)
down  = the label's own down direction, (−sin rotate, cos rotate) when straight,
        the inward or outward radial when concentric, the tangent when radial (§6.7)
aim   = −(origin + n·perp − lift·down − dot)·w / (along·w), the slide that hugs
        the near end of the label's FIRST LINE to its own station's dot; w = down
        for an end-anchored block, whose reading line then passes through the
        dot, and along for a start-anchored one, which then begins directly below
        the dot in the corridor frame (below)
```

**`distance` is a gap, not an offset.** Everything between the ink and the type is solved out of it
first, so one number reads as the same white paper under a plain dot, an interchange ring, a terminal
circle and a capsule alike, on a far slot as on the mean one. Two obstructions are measured along the
label's own offset direction and the **further** one wins:

| # | obstruction | measured as |
| --- | --- | --- |
| a | the station's **own marker**, at its extreme dot on the label's side | its outer edge — `r + stroke_width/2` for a dot, terminal or interchange ring, half the bar width for a capsule, all off `constants` |
| b | the **outer casing edge of the band** the station sits in | outermost slot its own lines run in, `+ casing_width/2 = 15` (= half the 26 stroke plus the 2 px casing lip) |

The **`half·|down·perp|` term** gives back what optical centring would otherwise spend: only the part
of the block's half-depth that points *across* the corridor eats into the gap. Because `down` is a
property of the point being solved for under both curved modes, the offset is a **fixed point**,
iterated (a hard contraction; `generate.py` fails the build rather than truncate the iteration).

**The `aim` term hugs the words to their own station.** `distance` fixes where the base point sits
*across* the corridor; the aim fixes where it sits *along* it, and which end of the block is the near
one is `align`'s answer:

* **end-anchored** — the block *finishes* on the base point, so aiming is putting the **reading line
  through the dot centre**: solve `(aimed − dot)·down = 0`. For type oblique to its corridor the
  slide is the drop divided by the tangent of the angle between them, so a label finishing square
  under its dot aims a half-span past it.
* **start-anchored** — the block *begins* on the base point. The reading-line rule would solve for
  the far intersection and shove the rank outward; what a start anchor wants is to begin **directly
  below its own dot in the corridor frame**: solve `(aimed − dot)·along = 0`. This form has no
  denominator that can vanish.
* **middle-anchored** — **no near end at all**; degenerate for the aim, which is the one thing a
  middle anchor is asked for: words centred on their marker rather than finishing or starting at it.
  `distance` and `ds` alone place it.

The point aimed, `aimed = base − lift·down`, is the **first line's** optical centre, not the block's.
The base point is the centre of the *whole* block, which a wrapped label carries half its extra
advance further down, and a slanted reading line taken from there lands short of the dot by that drop
divided by the tangent: on `W` the three subtitled labels — Vienna, Dunn Loring, West Falls Church —
sat 7 px back down the corridor from their own dots while the five single-line labels in the same
rank aimed true. Aiming the first line instead puts a wrapped label exactly where a single-line one
at the same station would sit, which is what a rank wants, and leaves `ds` meaning the same nudge on
either. `lift` is 0 for a single-line label, so nothing else moves.

The slide runs **along the corridor**, and `along ⊥ perp`, so it cannot touch the gap. What it does
change is which ink is nearest — a label slid out from under its own marker reads its gap off the
band instead. The gap is a **clearance under the station**, not a distance to the nearest ink.

The aim applies to the two modes whose reading line is straight, `straight` and `radial`, and needs
the type **oblique** to its corridor: both `|along·down|` and `|along·reading|` at least
`constants.label.aim_degenerate_dot`. Below that, no slide is made — the label is already placed as
well as a slide can place it: `concentric` always (a circle offset from a station never passes
through it), `align: "middle"` (no near end), type reading **along** the corridor (the slide runs
down its own reading line), and type reading **across** it (the offset carries it nowhere along the
corridor; radial type on a ring station is this by construction).

`generate.py` asserts the aim for every label that carries one, off the geometry it is about to emit
(§6.7). Because the aim is derived, `ds` is the only thing that can move a label off its own station.

Two things the rule deliberately does **not** see. A **concentric neighbour** of a ring station's own
ring is not part of its band — which is why a corridor whose stations sit in a stack of rings 28
apart has to declare a `distance` of its own. And the casing is **white paper**: the band is measured
at the slot edge, not the visible stroke edge, so neighbours in one rank keep sharing a line.

`constants.label.baseline_dy` centres the 14 px cap height on the dot line, and `extra` is the total
advance of every line after the first, so a multi-line block is lifted by half of it and stays
centred. Both terms rotate with the text; every multi-line label takes the lift by derivation.
Multi-line advance: wrapped `title` → `dy 14` (`constants.fonts.primary.wrap_dy`); first `subtitle`
line → `dy 11`; further `subtitle` lines → `dy 10`. `wrap_dy` is overridable but unused.

### 6.3 Which side a label reads — rule A

**The default is angular: a label reads *away from the centre*.** With `u` the reading direction and
`ô` the outward radial at the station,

```
side = "right" (align start) if ô·u ≥ 0 else "left" (align end)
```

For unrotated text that is simply *east of the centre reads east, west of the centre reads west*.
This is a design decision: the 1993 artwork chose sides station by station, and rule A overrides it
wherever the two disagree.

**The boundary is ±90°**, where `ô ⊥ u` and the rule carries no information.
`constants.label.rule_a_degenerate_dot` is the |cos| below which the rule is declared degenerate, and
**`generate.py` fails the build** if any such station has not been given an explicit `side`. Exact
ties resolve to `"right"`, but nothing in the spec relies on that.

**`side` is declared exactly where rule A resolves wrongly or degenerately**, on the corridor, run or
station whose scope matches the decision — and the reason is a `note` on that same object. A
*degenerate* declaration fills in for a rule with nothing to say; an *overriding* one contradicts a
rule that resolved, and its note says why. Everywhere else the side is derived.

**What `side` signs, and its own degeneracy.** `side` is not a screen direction: it is read off the
reading direction, `"right"` meaning the offset leans the way the type reads (`offset·u > 0`). Where
the type is set **along its own corridor** that reading carries no information — the sign then falls
back to the **outward radial**, `ô·perp`, so `"right"` still means the block sits away from the map
centre, and the tie breaks to `+perp` if that vanishes too, arbitrarily but totally.

### 6.4 `rotate`

* **a number** — an explicit design slant (`constants.label_diagonal` holds the two in use, one for
  ranks reading up-right and its complement-minus-90 for ranks reading down-left). A numeric `rotate`
  turns glyphs only under `curve: "straight"`, the only mode that still reaches it.
* **`{"axis": "<axis-id>"}`** — derived tangentially, so labels read along the spoke:
  `rotate = axes[axis_id].angle − 270`, normalised into **(−90, 90]**.

**Under the curved modes no `rotate` value turns a glyph** — each derives its own turn. Both forms
survive anyway, because `rotate` is still the **nominal reading direction `u`** rule A derives `side`
from: rule A cannot be re-based on the true curved reading direction, since a concentric tangent is
perpendicular to the outward radial everywhere and `ô·u` would be 0 at every station. (Under `radial`
the same test is the opposite of degenerate — `ô·u` = ±1 — and §6.7 spends it on `align` instead.)

### 6.5 Two clearance couplings, one taken and one refused

* **Marker size — coupled.** Interchanges, terminals and capsules get room in proportion to their
  outer edge, because that edge is one of the two obstructions `distance` clears (§6.2): a gap
  reading the same at every station beats one that shrinks under the big markers.
* **Station ticks — not coupled.** A tick is white on white paper: it puts down no ink a gap could be
  measured from, so `distance` ignores it.

### 6.6 The column primitive

A column of labels is never a primitive of its own: a vertical axis whose stations share a band
resolves one gap to one column by §6.2 alone. **A column stops where the band stops being
symmetrical, and that is the rule working, not failing:** a station on the corridor's **far** slot
has band and marker across the centreline, its extent collapses, and its label comes inboard by the
width of that collapse — still exactly its own gap off its own marker; the rank jogs rather than
running as one column. Nothing in the schema ever holds a label in a column.

### 6.7 Curved labels — `curve`

`constants.label.curve_default = "concentric"`. The three modes:

* **`concentric`** — the default; type curves with the rings.
* **`radial`** — a sunburst, which separates ring stations a few degrees apart that concentric type
  would pile into one another.
* **`straight`** — flat text turned by `rotate`, for the long straight runs and for the places where
  a concentric arc would stand near-vertical through the band the station belongs to.

Which corridors, runs and stations take which mode, and why, is on those objects in `spec.json`; the
live census is `--census` (§10). All three modes share §6.2 exactly: the base point is resolved in
the corridor frame first, and `curve` decides only what shape the type is set on.

**The frame does not rotate with the mode.** `distance` and `ds` are defined against the *corridor*,
not the reading direction, so what each acts across depends on the mode:

| mode | reading direction | `distance` acts | `ds` acts | aimed (§6.2) |
| --- | --- | --- | --- | --- |
| `straight` | `rotate` | across the corridor | along the corridor | yes, where the type lies oblique to its corridor |
| `concentric` | tangential | along the type, on a ring station | **across the type**, on a ring station | no |
| `radial` | radial | along the type, on a ring station | **across the type**, on a ring station | no |

A value that is a harmless nudge *along* a baseline becomes type sitting off its own station the
moment the type turns. **Re-examine `ds` and `distance` whenever a corridor or run changes `curve`.**
The generator cannot distinguish a design offset from an artefact under `concentric`; under the two
straight modes the aim assertion catches a leftover corridor `ds` as a whole rank slid off its
stations. The gap survives a mode switch untouched, because the `half·|down·perp|` factor is derived.

**The arc.** The baseline point is resolved as §6.2 says, then re-read in polar terms, and the line
is set on the circle of that radius through that point, so it runs tangentially and curves with the
rings. Emitted as a `<path>` in `<defs id="label-arcs">` (id `arc-<station-id>-<n>`, one per line of
type) and referenced by a `<textPath>`.

**Anchor mapping.** The derived `align` becomes the point of the arc the text is hung from: `start` →
`startOffset="0%"`, `middle` → `50%`, `end` → `100%`, with the matching `text-anchor`. The arc is cut
so the anchor lands exactly on that fraction, so the resolved point still means what it meant for
straight text.

**Arc length.** The span is a text-width estimate plus `constants.label.curve_pad_px`, from
`ADVANCE_PER_MILLE` in `generate.py` (AFM advances × `ADVANCE_SAFETY`; one table covers both font
tiers). The estimate deliberately runs **over**: `<textPath>` silently truncates text that outruns
its path, so an over-long arc costs nothing and an under-long one loses words.

**The upright rule.** Reading runs **with +θ above the centre line and against it below**, and the
type's `down` flips with it — a single sweep direction would stand the type on its head on one side
of the map. The boundary is `sin θ = 0`, where the type is vertical either way; it is broken towards
the upper hemisphere (`y ≤ 1000`), arbitrarily but totally.

**Multi-line blocks.** The §6.2 advances become **radial steps**: each line gets its own arc at
`r ± dy`, signed by the hemisphere so line 2 always sits below line 1 on the page. The
`baseline_dy − extra/2` centring is applied the same way, radially, before the first line.

#### `curve: "radial"` — the sunburst

A radial label is set **straight along the radius from (1000, 1000) through its own base point**. A
radius is straight, so it is emitted as a plain rotated `<text>` — the identical form `straight`
emits, from the same code path; the two differ only in where `rotate` came from. (No `<textPath>`,
so radial labels survive renderers that drop it — see **Renderers**.)

`rotate` is the station's own angle θ normalised into **(−90°, 90°]**. On the right half of the map
text reads **outward**; on the left half the normalisation reverses the reading direction, so text
reads **inward** — the price of upright glyphs, the same trade the concentric mode makes at its own
boundary.

That flip means the anchor must do the other half of the job, or left-half words would run back over
the station marker. Rule A is re-run on the *true* radial reading direction: `ô·u` is exactly ±1,
giving `align = "start"` outward and `"end"` inward, so the block always sits **outward of the base
point**. The declared or derived `side` still signs `distance`; `align` is taken over by the radial
derivation; an explicit `align` wins over both.

Multi-line blocks are unchanged from straight: `down` is the tangent, so the advances step
tangentially.

#### `curve: "straight"`

Concentric type fails wherever the arc would stand near-vertical — a station whose own radius is
near-horizontal — rising through the band it belongs to. Set straight, a corridor's numeric slant
(§6.4) hangs its labels in one diagonal rank clear below the band, and rule A derives the anchor: a
left-side rank reads up-right, end-anchored, into its stations; a right-side one down-right,
start-anchored, out of them. Which corridors and stations opt out, and why, is on those objects.

**What `distance` cannot fix.** Where a station's dot sits on top of another line's descent, its
label starts on that descent whatever the mode, and pushing `distance` further makes it worse when
the descent leans the same way the label does. Left as they are.

**Where `radial` does not help.** Radial labels separate stations in **angle**, and only in angle:
two stations on a near-radial spoke share θ, and their radial labels overlap outright — the exact
counterpart of the concentric mode's inability to separate two stations at one radius. Such a run
declares `curve: "concentric"` back and separates its labels by **side** instead, one run off each
flank of the spoke. The sunburst is left to ring stations, where the angles really differ.

**Checked.** Every curved line must span less than `checks.curve_geometry.max_span_deg` and sit
outside `min_line_radius_px`; the radius bound binds radial labels too, on the base point each reads
outward from. Two more assertions are read off the geometry the generator is **about to emit**, for
every label in every mode:

```
perp( base -> midpoint of first and last baseline ) == baseline_dy      exactly
( dot centre -> first line's optical centre )·w == ds·(along·w)         to 1e-9,
    for every aimed label; w as in §6.2
```

— the block is centred on its base point, and it hugs its station at the end its anchor makes near,
up to exactly the declared `ds`. What these watch for is a missed hemisphere flip (which would drop
every lower-hemisphere label by 2·`baseline_dy` with nothing else noticing) and the derived aim
going wrong.

**Renderers.** `<textPath>` is not universally supported: **librsvg drops it silently** —
`rsvg-convert` renders the map with every concentric label missing and exits 0. Inkscape and browsers
are fine; **every verification render must go through `inkscape --export-type=png`.** Two Inkscape
quirks shaped the output: several `<textPath>` children inside one `<text>` are mislaid, so each line
of a label is its own `<text>`; and `startOffset` is honoured both as a percentage and in user units,
so percentages are safe.

### 6.8 Rail connections

`spec.json.connections` lists the other railways a station connects to - Amtrak, MARC, VRE. Each
service is a badge: a rounded rect in the service's colour with its name in white capitals
(`services`, `badge`, `font`). An item names its `station` and its `services`, and its badges are laid
out as a `row` or a `column` centred on `at`; a column's `align` (`start`, `middle`, `end`) says which
edge its badges share. They are emitted last in the labels layer, in
`<g id="connections">`, with ids `connection-<station>-<service>`.

### 6.9 Station marks

A station's `marks` are its features on the WMATA map: `parking` (a white P on a slate square) and
`hospital` (a white cross on a red square), defined in `constants.station_marks`. The parking marks
are the 42 of WMATA's own map, compared station by station. They are not placed by
co-ordinates. The generator writes each mark INTO the station's own label line - `marks_line`,
default the first title line - as an inline `<tspan>`: after the name, or before it when the label
is end-anchored, so the marks sit at the end of the name away from the station. The renderer then
spaces the mark against the name with the font it really has, and on a `<textPath>` the mark
follows the label's orbit. The background of the P is a glyph too: Inter's filled square
(U+25A0), which is exactly one cap height, with the P drawn back over it by a negative `dx`. A
mark's `features` sets OpenType features on its letter: the hospital cross is Inter's capital-form
plus (`case`), which the font centres on the cap height. A curved line's carrier arc is lengthened by `advance` per mark so the
mark is not cut off. The tspans carry ids `mark-<station>-<mark>`.

### 6.10 Legend

`spec.json.legend` is the map's key, emitted last in its own layer, `<g id="layer-legend">`
(`--layers legend`). It uses the four corners that the circular diagram leaves free on the square
canvas: the title at the top left, the six lines at the bottom left, the symbols at the bottom right
and the north sign at the top right. The title block also carries the contact lines. Each block has an `at` and rows `row_height` apart (the symbol key has a tighter one of its own, and is set flush right: `text_anchor`, `symbol_align`). A line row
names a `line` and takes its colour from it; a symbol row has a `kind` and is drawn from the same
constants as the thing on the map it stands for - the station dot, the interchange marker, the
station marks, the connection badges, the airport apron and plane, the landmark paths - so the key
cannot drift from the map.

Three more blocks: `title.logo`, the map's own sign - a bold square M made of concentric rings over
a tunnel mouth. Its `d` is a clip outline in a `d_box`-unit box and `rings` are the bands drawn
inside it about `centre`; it is not WMATA's mark. `accessible`, the accessibility line and its
sign, under the line key. `credits`, the copyright line and the disclaimer under a thin `rule`,
below the symbol key.

---

## 7. Geography

All literal geo coordinates are in the 2000 frame. Every shape, measurement and construction —
parks, water, trails, the beltway ring that defines the grid — is recorded on its own object in
`spec.json.geo`, with its derivation in the object's `note`.

**Landmarks** are tiny filled pictograms in `geo.landmarks.items`: each carries its own path `d`,
authored about 24 across with the origin at its centre, and is moved to `at` by a translate; an optional `scale` sizes one icon about its centre. They
share one fill and are filled even-odd, so a sub-path inside the outline is a hole. They are emitted
in `<g id="geo-landmarks">`, above the Beltway and below the geography names.

**Water, park and Beltway names** are geography, not station labels: a river, basin, park or island carries
a `label` on its own object in `spec.json.geo`, and they are emitted last in the geography layer, in
`<g id="geo-labels-parks">` (set in `constants.fonts.park`), `<g id="geo-labels-water">`
(`constants.fonts.water`) and `<g id="geo-labels-beltway">` (`constants.fonts.beltway`). A straight label is a block of `lines` centred on a point and turned
about it (`at`, `rotate`), with an optional `fill` of its own; the Anacostia's and the Beltway's are type on their own ring, centred at `angle`. Their
ids start `geo-label-`, so the station-label tools and counts do not see them. A straight label's
own `size_px` and `letter_spacing` replace the font's (Roosevelt Island: 10 px; the
National Mall: 10 px, letter-spaced capitals).

**The District's boundary** is `boundary-dc`, the last entry of `geo.parks` so that it is drawn
over every park and under the water and the lines: a `trail` of round dots (`dasharray`) on the
map's own rings - one radius for all its arcs, a step out past Deanwood and Minnesota Av parallel
to the Silver line, and a step out at Congress Heights. It is not the real diamond. Two keys are
its own. `label_gap` leaves the dots out wherever the line comes within that distance of a station
label; the stations in `label_gap_except` make no gap. A `straight` segment with its own `from`
starts a new sub-path, and with it the dash pattern, which is how a dot is put exactly on a
corner.

**Jurisdiction names** are in `geo.boundary_labels` (`constants.fonts.boundary`,
`<g id="geo-labels-boundary">`): `on_ring` labels are type on a ring, `offset` off it, and
`straight` ones are plain blocks. The District's and Maryland's stand on the two sides of the
boundary, in the north-west and the south-east. Virginia has no line - the Potomac is the
boundary - so its name is on the river's Virginia bank; `VIRGINIA` and `MARYLAND` are also set
inside the Beltway beside its own name (`geo.beltway.labels`).

---

## 8. Spokes and axes

`spec.json.axes` is the complete set of straight guides, each with its own `note` giving what it
carries and where its numbers came from. The conventions they all obey:

* **Origin.** A radial spoke takes `origin = [1000,1000]`, so `s` is the radius. A non-radial axis
  puts its origin at the natural corner or at the foot of the perpendicular from the centre.
* **Slots.** Slot maps follow §3.2 — multiples of 14, named by line, with the sign fixed by the
  direction of travel. Slot **names** are load-bearing: stations, line segments and arc targets all
  reference them, so a change of which line takes which slot is expressed in the offsets, never by
  renaming.
* **Derived axes.** An axis may be derived rather than authored, when a corner has to land exactly.
  Where it is, the derivation is on the axis.
* **Angles.** Axis and station angles are multiples of **1.5°**, except where an angle is derived
  from a measurement or stepped off one — and the derivation is on the axis.

---

## 9. Verification

The spec describes a target, not a file to be diffed against, so `spec.json.checks` holds no
reference geometry. It holds one thing:

* **`curve_geometry`** — the two bounds §6.7 asserts on every curved line of type.

Everything else a spec reader can check without any SVG — every ring/axis/slot/run/corridor/line id
resolves, every line segment chain is point-continuous, every arc starts exactly on its ring, the
grid identities hold — needs no stored parameter to assert, so it is carried by the generator alone
(§11.2) and stored nowhere.

It stores **no census**: a list or count kept beside the data it describes, edited in the same
commit, can only confirm the edit it was supposed to police. The live counts come from `--census`.

**Canvas fit** is asserted, not measured by hand (§11.2): no geometry leaves the 2000 × 2000 page.

---

## 10. Counts

```
./generate.py --census
```

prints the spec census, the label census, the element census of the generated SVG and the assertion
count. The numbers are read off `spec.json` and off the SVG that same invocation would write, so they
cannot drift from the artefact the way a table here would. The invariants the numbers are evidence
of:

* **Per-station placement overrides are few.** Nearly every label resolves from corridor/run
  parameters and `constants.label` alone; each override earns a `label.note` on its own station.
* **The three curve modes split by design, not by accident** (§6.7).
* **Stations and dots differ.** A station is one entry however many circles it puts down: terminals
  contribute one dot per line and capsules two end dots each.

---

## 11. The generator

`./generate.py` — Python 3, **stdlib only**, deterministic: the same `spec.json` always yields a
byte-identical `DC-Metro.svg`.

```
./generate.py                              write DC-Metro.svg
./generate.py --check                      run every assertion, write nothing
./generate.py --census                     print the counts of §10, write nothing
./generate.py --layers geo,lines,stations  geometry-only map (no labels)
./generate.py --spec … -o …                alternate paths
./generate.py --force                      write the SVG even with checks failing
./generate.py --pdf                        also write DC-Metro.pdf, text as outlines
./generate.py --png                        also write DC-Metro.png, 4000 px wide on white
```

`--pdf` is the release step. The SVG names its font and does not carry it, so a machine without
Inter sets the type in a fallback and the labels, marks and gaps move. The PDF has every text as
outlines and looks the same everywhere. The conversion is Inkscape's (`--export-text-to-path`), so
`--pdf` needs Inkscape on the PATH and Inter installed on the machine that runs it; it is the one
step that is not stdlib-only, and the SVG stays the byte-identical build output. `--png` is the same
step for places that take an image and not a PDF: 4000 px wide, on a white background. `*.png` is in
`.gitignore`, so the PNG is not tracked.

`--layers` takes any comma-separated subset of `geo, lines, stations, labels, legend` (default: all). It only
filters which layer groups are emitted, so a geometry-only render is pixel-identical to the full one
minus the label layer. `tools/` holds the measurement scripts that answer the questions the
assertions do not (`tools/README.md`).

### 11.1 Output conventions

* Root carries `viewBox`, `width`, `height`, `version`, the font stack from
  `constants.fonts.family`, and `<title>` from `spec.title`.
* **Ids are minted, stable and human-readable, off the spec's own ids** — layers, lines, casings,
  corridor groups, per-station stop and label groups, per-line label arcs. `data-st` / `data-cor`
  are kept on stops and labels.
* **No editor metadata.** No `sodipodi:*`, no `inkscape:*`, no unused namespace declarations, no
  autogenerated ids. Editor state is not design; it is not in `spec.json` and it is not emitted.
  Layer names from `layers[].name` survive as `data-name`.
* Casings carry the **same `d` string object** as their line, so they are byte-identical by
  construction rather than by luck.
* A concentric label is a `<g>` holding one `<text><textPath></text>` per line of type, its arcs in
  one `<defs>` at the head of the label layer, in corridor order, so deleting the layer leaves
  nothing behind. A straight or radial label is a single `<text x y transform="rotate(a, x, y)">` —
  rotated about the anchor so the number is readable — with wrapped lines at their §6.2 advances.
* **Two SVG text rules the emitter honours.** A `<tspan x>` **overrides** the `<text x>` for
  anchoring, so every continuation tspan repeats its parent's `x` — that keeps a wrapped block a
  column instead of a staircase. And `style="text-anchor:…"` would override the attribute, so
  anchoring is only ever written as an attribute, never in a style string.
* **Inheritable attributes are hoisted.** `text-anchor` is written once on the corridor group when
  every label in it agrees, per label otherwise; font attributes sit on the layer.
* Numbers are formatted to at most 4 decimals with trailing zeros stripped, and `-0` normalised.

### 11.2 Checks

`run_checks` runs on **every** invocation, not just `--check`, and a failure aborts before anything
is written — unless `--force`, which prints the same report, carries on, and still exits non-zero, so
a forced SVG can never be mistaken for a clean one. The assertion classes:

1. **Grid identities** — every `basis: "grid"` ring is parsed from its `fraction_of_R` string and
   compared to R; every `basis: "derived"` ring is re-derived from its rule string; plus the standing
   identities between rings and constants and Yellow strictly inside Green.
2. **Point continuity** — every segment whose `from` is `prev` starts exactly where the previous one
   ended, every arc's start lies on its ring, and every straight's inherited start lies on its own
   slot line (each ≤ 1e-6). This is what proves the derived ring-entry angles of §4.
3. **Mean-slot rule** — every station's slot equals the mean of its lines' slots (§3.2), no
   exceptions.
4. **Counts — none.** A census kept next to the thing it counts can only ratify the edit that changed
   both. Counts are reported by `--census` and asserted nowhere.
5. **Label schema and geometry** — every station has `label.title`; every `label_default` /
   `override` key is one of the seven of §6.2 and every `curve` a known mode; every rule-A-degenerate
   station declares a `side`; every curved line and radial base point sits inside the
   `curve_geometry` bounds; and, read off the geometry about to be emitted: the block is centred
   exactly `baseline_dy` off its base point, the block edge nearest the station sits exactly
   `distance` beyond the obstruction extent, and every aimed label hugs its station up to the `ds` it
   declares and no further.
6. **Reference integrity** — every ring/axis/slot/run/corridor/line id resolves, run indices are in
   range, station ids are unique, and no ring is left unreferenced.
7. **Geography** — the one solved fillet meets its ring within 0.01 (exact tangency), and the trail
   straight is parallel to its bank edge.
8. **Canvas fit** — no geometry leaves the 2000 × 2000 page.
