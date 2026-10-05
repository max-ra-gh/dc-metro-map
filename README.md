# DC Metro concentric map

A map of the Washington, DC Metro rail system drawn as concentric circles. Every line is built from
rings and spokes around one centre, and the Capital Beltway is the outer ring.

- **[DC-Metro.pdf](DC-Metro.pdf)**: the map, ready to print. All text is outlines, so it needs no font.
- **[DC-Metro.svg](DC-Metro.svg)**: the same map as SVG. It looks right only where the Inter font is
  installed.

The map shows all 98 stations and 6 lines, the sections that every other train serves, parking and
hospitals, the Amtrak, MARC and VRE connections, the two airports, the District boundary, rivers and
parks, and about 30 landmark icons.

This map is not official. It is not affiliated with or endorsed by WMATA.

## How it is made

The map is generated. Nothing is drawn by hand.

| File | Role |
| --- | --- |
| `spec.json` | all the data: the grid, the lines, the stations, the labels, the geography and the legend |
| `generate.py` | reads `spec.json` and writes the map |
| `SPEC.md` | what each part of `spec.json` means |
| `tools/` | scripts that measure the result (label collisions, clearances) |

```sh
./generate.py               # write DC-Metro.svg
./generate.py --check       # run the checks, write nothing
./generate.py --pdf --png   # also write DC-Metro.pdf and DC-Metro.png
```

`generate.py` needs Python 3 and nothing else. The `--pdf` and `--png` options also need
[Inkscape](https://inkscape.org) and the [Inter](https://rsms.me/inter/) font on your computer.

## License

- The map and its artwork: [CC BY-NC-SA 4.0](LICENSE-MAP). You may share and adapt it with credit, for
  non-commercial use, under the same license.
- The code and data: [MIT](LICENSE).

Copyright (c) 2026 Max Rakhimov
