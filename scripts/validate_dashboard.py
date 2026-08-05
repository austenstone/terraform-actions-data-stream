#!/usr/bin/env python3
"""Validate a KQLDashboard JSON against the official Azure Data Explorer schema.

The dashboard schemas are published at
https://dataexplorer.azure.com/static/d/schema/<version>/dashboard.json
and split across sibling files ($ref'd relatively), so we fetch the whole set.

Run this BEFORE `fab import`. The Fabric API validates almost nothing, so a
successful write tells you nothing about whether the browser will render it.

    python3 validate_dashboard.py path/to/RealTimeDashboard.json

Exit code 0 = valid, 1 = invalid.
"""

import json
import os
import re
import ssl
import sys
import urllib.request
from pathlib import Path

try:
    import certifi

    SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    SSL_CTX = None

CACHE = Path.home() / ".cache" / "adx-dashboard-schema"


def fetch_schema(version):
    base = f"https://dataexplorer.azure.com/static/d/schema/{version}/"
    out = CACHE / str(version)
    out.mkdir(parents=True, exist_ok=True)
    todo, done = {"dashboard.json"}, set()
    while todo:
        name = todo.pop()
        done.add(name)
        dest = out / name
        if dest.exists():
            raw = dest.read_bytes()
        else:
            raw = urllib.request.urlopen(base + name, timeout=30, context=SSL_CTX).read()
            dest.write_bytes(raw)
        for ref in re.findall(r'"\$ref"\s*:\s*"([^"#]+)', raw.decode()):
            f = os.path.basename(ref)
            if f.endswith(".json") and f not in done:
                todo.add(f)
    return out


def lint_refs(doc):
    """Cross-references the schema cannot express.

    The schema validates each object in isolation, so it happily accepts a tile
    pointing at a page that does not exist, a parameter whose dropdown query was
    dropped, or a query using a variable no parameter declares. All fail at render
    time -- the middle one with "Query id <uuid> in parameter not found", the last
    with "Failed to resolve scalar expression named '_startTime'".
    """
    bad = []

    pages = {p.get("id") for p in doc.get("pages", [])}
    queries = {q.get("id") for q in doc.get("queries", [])}
    declared = set()
    for p in doc.get("parameters", []):
        if p.get("kind") == "duration":
            # schema 77 spells the lower bound "beginVariableName"; "startVariableName"
            # is accepted too so a doc authored against either spelling still lints.
            for key in ("beginVariableName", "startVariableName", "endVariableName"):
                if p.get(key):
                    declared.add(p[key])
        elif p.get("variableName"):
            declared.add(p["variableName"])

    # A parameter's dropdown query is referenced only from parameters[], never from a
    # tile. Anything that decides which queries are live by scanning tile queryRefs will
    # garbage-collect it, and the client then refuses the whole dashboard with
    # "Query id <uuid> in parameter not found".
    for i, p in enumerate(doc.get("parameters", [])):
        name = p.get("displayName") or f"parameters[{i}]"
        qid = ((p.get("dataSource") or {}).get("queryRef") or {}).get("queryId")
        if qid is not None and qid not in queries:
            bad.append(f"parameter {name!r} -> queryId {qid!r} is not a declared query")

    for i, t in enumerate(doc.get("tiles", [])):
        name = t.get("title") or f"tiles[{i}]"
        if t.get("pageId") not in pages:
            bad.append(f"tile {name!r} -> pageId {t.get('pageId')!r} is not a declared page")
        qid = (t.get("queryRef") or {}).get("queryId")
        if qid is not None and qid not in queries:
            bad.append(f"tile {name!r} -> queryId {qid!r} is not a declared query")

    seen = {}
    for q in doc.get("queries", []):
        qid = q.get("id")
        if qid in seen:
            bad.append(f"query id {qid!r} is used by more than one query (each tile needs its own)")
        seen[qid] = True
        # The lookbehind matters: without it the tail of any identifier or string
        # literal reads as a variable, so a query mentioning "workflow_run" gets
        # flagged for omitting a parameter named _run that it never referenced.
        pattern = r"(?<![A-Za-z0-9_])_[A-Za-z][A-Za-z0-9_]*"
        for var in sorted(set(re.findall(pattern, q.get("text", "")))):
            if var in declared and var not in (q.get("usedVariables") or []):
                bad.append(f"query {qid!r} references {var} but omits it from usedVariables")

    for line in bad:
        print(f"  {line}")
    return not bad


def lint_layout(doc):
    """Client-side rules the JSON Schema does not encode.

    The schema allows width>=2, height>=1, but the client refuses to render a
    tile smaller than 12x6 ("Current tile size (24, 5) is smaller than the
    minimum supported tile size (12, 6)"). On a 24-column grid that caps you at
    two tiles per row. Dashboards authored under older clients have smaller
    tiles and appear to be grandfathered.

    The minimum is per visualType, not global: a multistat reports a minimum of
    (6, 9), so it is narrower but three rows taller than everything else. A
    12x6 multistat passes the generic check and still fails to render.
    """
    DEFAULT_MIN = (12, 6)
    BY_VISUAL = {"multistat": (6, 9)}
    bad = []
    for t in doc.get("tiles", []):
        lay = t.get("layout", {})
        w, h = lay.get("width", 0), lay.get("height", 0)
        min_w, min_h = BY_VISUAL.get(t.get("visualType"), DEFAULT_MIN)
        if w < min_w or h < min_h:
            bad.append((t.get("title") or f"<{t.get('visualType')}>", w, h, min_w, min_h))
    for title, w, h, min_w, min_h in bad:
        print(f"  tile {title!r}\n      size ({w}, {h}) is below the client minimum ({min_w}, {min_h})")
    return not bad


def lint_visuals(doc):
    """Schema-valid visualOptions combinations that break the renderer.

    On a table tile, colorRulesDisabled=false with an empty colorRules list
    turns conditional formatting on with no rules to apply, and that kills the
    whole grid during column setup: no headers, no rows, just the "No Rows To
    Show" overlay -- while the identical query returns rows when run directly.
    Every working table tile observed in the wild sets colorRulesDisabled=true.
    Chart tiles (multistat, line, pie, ...) tolerate the same combination and
    render normally, so this check is scoped to tables.
    """
    bad = []
    for t in doc.get("tiles", []):
        if t.get("visualType") != "table":
            continue
        vo = t.get("visualOptions") or {}
        if "colorRulesDisabled" in vo and not vo["colorRulesDisabled"] and not vo.get("colorRules"):
            bad.append(t.get("title") or "<table>")
    for title in bad:
        print(
            f"  tile {title!r}\n"
            "      table with colorRulesDisabled=false and an empty colorRules "
            "list renders an empty grid even though the query returns rows. "
            "Set colorRulesDisabled=true."
        )
    return not bad


def validate(path):
    doc = json.loads(Path(path).read_text())
    version = doc.get("schema_version")
    if not isinstance(version, int):
        print(
            f"FAIL {path}\n  /schema_version\n      must be the integer "
            f"{version!r} -> {version}, not a string. This is the single most "
            "common cause of 'Missing migration for dashboard version N'."
        )
        return False

    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012

    d = fetch_schema(version)
    pairs = []
    for p in d.glob("*.json"):
        r = Resource.from_contents(json.loads(p.read_text()), default_specification=DRAFT202012)
        pairs += [(p.name, r), (f"/static/d/schema/{version}/{p.name}", r)]

    v = Draft202012Validator(
        json.loads((d / "dashboard.json").read_text()),
        registry=Registry().with_resources(pairs),
        format_checker=Draft202012Validator.FORMAT_CHECKER,
    )
    errors = sorted(v.iter_errors(doc), key=lambda e: list(map(str, e.path)))
    if errors:
        print(f"FAIL {path} — {len(errors)} schema error(s) against version {version}")
        for e in errors:
            print(f"  /{'/'.join(str(x) for x in e.path)}\n      {e.message[:300]}")
        return False

    if not lint_refs(doc):
        print(f"FAIL {path} — schema-valid but has broken internal references")
        return False

    if not lint_layout(doc):
        print(f"FAIL {path} — schema-valid but violates client layout rules")
        return False

    if not lint_visuals(doc):
        print(f"FAIL {path} — schema-valid but has a visualOptions combination that renders empty")
        return False

    missing = {"date-time", "uri"} - set(Draft202012Validator.FORMAT_CHECKER.checkers)
    note = f"  (no checker for {', '.join(sorted(missing))} — pip install 'jsonschema[format]')" if missing else ""
    print(f"OK   {path} (schema {version}, {len(doc.get('tiles', []))} tiles){note}")
    return True


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    sys.exit(0 if all([validate(p) for p in sys.argv[1:]]) else 1)
