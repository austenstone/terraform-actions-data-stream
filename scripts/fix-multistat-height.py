#!/usr/bin/env python3
"""Raise every multistat tile to the client's minimum height and push the rest down.

The dashboard client enforces a per-visualType minimum tile size. Tables and
charts floor at (12, 6), but a multistat floors at (6, 9) -- narrower, three
rows taller. A 12x6 multistat passes every schema check, saves cleanly, and
then renders as "An error occurred" with the real reason buried behind the
tile's Details button. Banners authored at height 6 were never rendering.
"""
import json
import pathlib

MIN_H = 9
DASH = pathlib.Path(__file__).resolve().parents[1] / "modules/azure-kusto/dashboard/RealTimeDashboard.json"


def main():
    doc = json.loads(DASH.read_text())
    fixed = 0
    for page in doc["pages"]:
        tiles = [t for t in doc["tiles"] if t.get("pageId") == page["id"]]
        for tile in sorted(tiles, key=lambda t: t["layout"]["y"]):
            if tile.get("visualType") != "multistat":
                continue
            lay = tile["layout"]
            delta = MIN_H - lay["height"]
            if delta <= 0:
                continue
            bottom = lay["y"] + lay["height"]
            lay["height"] = MIN_H
            for other in tiles:
                if other is not tile and other["layout"]["y"] >= bottom:
                    other["layout"]["y"] += delta
            fixed += 1
            print(f"{page['name']}: {tile.get('title')!r} +{delta} rows")
    DASH.write_text(json.dumps(doc, indent=2) + "\n")
    print(f"{fixed} multistat tiles raised to height {MIN_H}")


if __name__ == "__main__":
    main()
