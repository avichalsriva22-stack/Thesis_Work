# Implementing Approach 2 (Overture-Primary XODR Pipeline) in Google Antigravity IDE

**Based on:** Slides 13–14, "AI-Driven Generation of ASAM OpenDRIVE (XODR) Maps" — M.Tech Thesis Defense, Avichal Srivastava, IIT Kanpur / SimDaaS Autonomy

---

## 1. What Approach 2 Actually Asks For

Slide 13 replaces OSM-as-primary-source (Approach 1) with **Overture Maps as the primary structural source and OSM as a secondary, point-feature enrichment source**. The four sub-phases are:

1. **Global Anchoring** — project Overture structural data and OSM semantic nodes from WGS84 into a shared local Cartesian (UTM) grid.
2. **Overture as Backbone** — use Overture's `connector` features for node-to-node routing topology, `segment` geometry (LineStrings) for the mathematical centerline (`<planView>`), and `width_rules` for carriageway boundaries.
3. **OSM Point-Feature Enrichment** — pull OSM point data (traffic signals, stop signs) and `turn:lanes` tags, project them orthogonally onto the Overture backbone to get exact **s** (longitudinal) / **t** (lateral) OpenDRIVE offsets.
4. **Assembly & Compilation** — feed the derived geometry and offsets into the `scenariogeneration` Python library to compile a schema-validated `.xodr` file.

Slide 14 frames this as "Populating the XODR Schema" — i.e., every one of the four phases above ultimately has to terminate in a specific, XSD-legal OpenDRIVE element (`<planView>`, `<lane>`, `<roadMark>`, `<signal>`, `<junction>`, `<laneLink>`).

This document explains how to structure and drive that implementation *inside Google Antigravity IDE*, and grounds each phase in the actual field-level schema of Overture Maps and OpenStreetMap so the agent has something concrete to code against instead of guessing field names.

---

## 2. What Antigravity IDE Brings to This Problem

Antigravity is Google's agent-first IDE (a VS Code–based fork) built around autonomous coding agents rather than autocomplete. The parts of it that map directly onto this thesis pipeline:

- **Agent Manager + multiple parallel agents** — you can run one agent per pipeline phase (anchoring, backbone extraction, enrichment, assembly) concurrently, each scoped to its own files/tests, then merge.
- **Planning Mode vs. Fast Mode** — Planning Mode makes the agent produce an **Implementation Plan** artifact before writing code, which you review/approve; Fast Mode is for small, low-risk edits. For a schema-correctness-critical project like XODR generation, Planning Mode is the right default for Phases 2–4.
- **Verifiable Artifacts** — Antigravity automatically generates Markdown artifacts as it works: an **Implementation Plan**, a **Task List** (step-by-step, with completed/pending status), and a final verification summary. These become your thesis's reproducible build log — worth keeping, since your defense already emphasizes deterministic, auditable pipelines.
- **Browser-in-the-loop agents** — the agent can drive a real browser. This is directly useful here: it can open the Overture Maps Explorer / OSM's Overpass-turbo or the ASAM XSD validator web tools to visually cross-check a generated `.xodr` file or a downloaded Overture GeoParquet extract before you trust it in code.
- **MCP (Model Context Protocol) tool connections** — Antigravity can attach MCP servers (e.g., for Postgres/PostGIS, BigQuery, or a local DuckDB) directly into the agent's toolset via its MCP Store, so the agent can query a loaded Overture/OSM dataset without you hand-writing every connection snippet.
- **Multi-model switching** — you can route the deterministic/geometry-heavy phases (2, 4) to a strong reasoning model and use a faster model for boilerplate (parsers, tests, CLI plumbing).

None of this replaces the "deterministic math, not hallucinated geometry" philosophy from your Approach 1/2 slides — it's the opposite: Antigravity's planning artifacts let you *audit* that the agent didn't sneak a hallucinated curve-fit into the geometry phase, since every step is logged and diffable before it's applied.

---

## 3. Project Setup Inside Antigravity

1. **Create a new workspace/project** in Antigravity and open it in the IDE surface (not just the standalone Agent Manager), since you'll want inline diffs against `planView`/`laneSection` XML templates.
2. **Seed a `PROJECT.md` / knowledge file** at the repo root describing:
   - The four phases and their exact input/output contracts (see §4–7 below).
   - The **non-negotiables**: G2 continuity, ASAM XSD validity, no LLM-generated numeric geometry (only classification/enrichment tasks may touch an LLM).
   - The library stack: `pyproj` (WGS84↔UTM), `shapely` (offsets/projection), `scipy.interpolate` (only if curve smoothing is still needed on Overture LineStrings), `scenariogeneration` (OpenDRIVE object model + XSD-compliant serialization), `duckdb` + the Overture GeoParquet reader (or `overturemaps` CLI/Python package), `osmium` or `pyosmium` / a local OSM Postgres replica for OSM extraction.
   Antigravity's agents read this file automatically as project context — it prevents each new agent session from re-deriving the architecture from scratch.
3. **Switch to Planning Mode** and prompt the agent once per phase, in order, so you get four separate Implementation Plan artifacts you can review before code is generated. Example seed prompt for Phase 1:

   > "Read `PROJECT.md`. Implement Phase 1 (Global Anchoring) only: given an Overture `segment`/`connector` extract (GeoParquet, WGS84) and an OSM extract (PBF or Postgres `current_nodes`/`current_ways`, lat/lon stored as 1e7-scaled integers), write a module that reprojects both into a shared local UTM CRS chosen from the data's centroid, and stores results in a common in-memory model keyed by Overture `id` / OSM node/way `id`. Do not calculate any curve geometry yet. Produce an Implementation Plan first."

4. **Set review level to "always" or "when agent thinks it's a good idea"** for the geometry-generation phases (2 and 4) so every diff touching coordinate math gets your eyes before it's applied — this is the manual equivalent of your "Agentic Guardrails" phase from Approach 1's architecture slide, just applied to the coding agent itself, not the runtime pipeline.

---

## 4. Phase 1 — Global Anchoring

**Goal:** one shared, metric coordinate frame for every downstream calculation.

- Overture's `connector` and `segment` features carry a `geometry` field that is GeoJSON-compatible — `connector.geometry` is a `Point`, `segment.geometry` is a `LineString` — both natively in WGS84. Have the agent write a `pyproj`-based reprojection utility that picks a local UTM zone from the bounding box of the extract and reprojects every Overture feature's geometry into it.
- For OSM, the underlying database stores `latitude`/`longitude` on `current_nodes` (and historical `nodes`) as **integers scaled by 1e7** (per the OSM Rails-port schema) — the agent needs a conversion step (`lat = raw_lat / 1e7`) before reprojection, whether it's reading a Postgres replica of that schema or a PBF export (which normalizes this for you already).
- Ask the agent to key everything by stable IDs: Overture's GERS-compatible `id` field on both `connector` and `segment`, and OSM's `id` + `version` on `current_nodes`/`current_ways`, so Phase 3's enrichment step can look up "the OSM way that best matches this Overture segment" later without re-deriving geometry.

Antigravity prompt pattern: give the agent the two schema references directly (link them in the prompt or drop the relevant field tables into a `docs/schema_notes.md` in the repo) so it stops guessing field names — Overture's `sources[].dataset` field (present on both `connector` and `segment`) is also handy here, since when it equals `"OpenStreetMap"` you already have a hint for Phase 3 matching.

---

## 5. Phase 2 — Overture as the Backbone

This is where most of `<planView>`, `<lane>`, and the road network topology gets populated.

| Overture field | XODR target | Notes for the agent |
|---|---|---|
| `segment.geometry` (LineString) | `<planView>` geometry blocks (line/arc/spiral fit) | Overture LineStrings are still discrete points (per the slide's own "Drawbacks" callout), so a `scipy`/`shapely`-based line/arc/clothoid decomposition is still required — Overture removes the *topological* noise, not the *discretization* problem. |
| `segment.connectors[]` (list of `{connector_id, at}`, `at` ∈ [0,1] normalized position along the segment) | node-to-node routing graph → `<road>` predecessor/successor + `<junction>`/`<laneLink>` | This `at` value is exactly the fractional-position semantic OpenDRIVE's own `s`-coordinate system wants — the agent should treat each `connector` as a routing/decision node, and each `segment` between two connectors as one OpenDRIVE `<road>` (or lane-section boundary). |
| `segment.width_rules[]` (edge-to-edge carriageway width in meters, optionally scoped with `between`) | `<laneSection>` boundary widths | These can be scoped to a sub-range of the segment via `between`, so the agent must handle width that changes partway along a road, not just a single scalar per segment — this is the schema-level fix for Approach 1's "width from VLMs" drawback. |
| `segment.subtype` / `class` / `subclass` (road classification) | lane count / road type defaults, `<road><type>` | Use to pick sane defaults (e.g., lane count) before OSM `turn:lanes` refines them in Phase 3. |
| `segment.road_flags`, `road_surface`, `speed_limits` | `<road>` attributes, `<roadMark>` hints | Optional enrichment, same "rules array with optional range" pattern as width. |

Prompt the agent to build this as pure, testable functions: `overture_segment_to_planview(segment) -> PlanView`, `build_topology(connectors, segments) -> RoadGraph`, with unit tests asserting G2 continuity at every connector junction (heading + curvature continuity, not just position continuity) — this directly encodes your "geometric inconsistency" failure mode from Slide 3 as an automated check rather than a manual review.

Have Antigravity's agent fetch the field-level schema pages for `connector` and `segment` (`docs.overturemaps.org/schema/reference/transportation/connector` and `.../segment`) as part of its own research step before writing the parser — this is a case where the browser-in-the-loop capability is genuinely useful: let it open the live doc, extract the exact field list/types, and paste that into its own plan artifact instead of you hand-copying it into the prompt.

---

## 6. Phase 3 — OSM Point-Feature Enrichment

**Goal:** attach point-like semantics (signals, signs) and lane-turn semantics that Overture's road-network schema doesn't model as richly, without letting OSM back into the *geometry* path.

- Source tables (per the OSM Rails-port / `openstreetmap-website` schema, `db/structure.sql`): point features live as `current_nodes` rows with matching `current_node_tags` (`k`/`v` pairs, e.g. `k='highway', v='traffic_signals'` or `k='highway', v='stop'`). Lane-turn metadata lives as `current_way_tags` on the parent way (e.g., `k='turn:lanes'`), joined through `current_way_nodes` to get the node ordering.
- The agent's job: for each tagged OSM node, do a **nearest-line projection** (Shapely's `.project()` / `.interpolate()` against the LineString) onto the *already-anchored* Overture backbone segment, producing:
  - `s` = longitudinal distance along the segment from its start connector,
  - `t` = signed perpendicular offset from the centerline.
- These `(s, t)` pairs map directly onto OpenDRIVE's native road-relative coordinate system, so a `<signal s="..." t="..." .../>` or lane-attribute element can be emitted with no further geometric reasoning — this is the "no CV noise" objective from the slide, enforced structurally rather than by a vision model.
- Matching OSM ways to Overture segments (needed before projection) can lean on the `sources[].dataset == "OpenStreetMap"` / `sources[].record_id` hints on Overture `segment` records where present, falling back to nearest-geometry matching otherwise — call this out explicitly as an open risk in the plan, since the slide itself flags "Dataset Fusion Challenge" as an unresolved drawback.
- `turn:lanes` values are semicolon/pipe-delimited strings (e.g. `left|through|through;right`) — have the agent write a small, well-tested parser for the OSM turn-lane tagging grammar rather than improvising regex ad hoc; this is a good candidate for an LLM-assisted *classification* pass (parsing free-form tag text) precisely because it's not touching numeric geometry.

Antigravity task-list tip: split this into two Task artifacts — "point-feature ETL" and "line-projection math" — so the agent (and you, reviewing) can tell tagging/parsing bugs apart from geometric-projection bugs.

---

## 7. Phase 4 — Assembly & Compilation with `scenariogeneration`

- `scenariogeneration`'s `xodr` submodule gives you Python objects (`Road`, `PlanView`, `Lane`, `LaneSection`, `Junction`, etc.) that serialize to ASAM-schema-ordered XML, which is the direct fix for Slide 10's "a single misplaced tag completely invalidates the file — it should follow a proper order" requirement.
- Feed in, per road: the `PlanView` geometry sequence built in Phase 2, lane widths from `width_rules`, and the `(s, t)`-anchored signals/lane-turn info from Phase 3.
- Have the agent write an **assembly orchestrator** that:
  1. Walks the `RoadGraph` from Phase 2, instantiating one `scenariogeneration.xodr.Road` per Overture segment.
  2. Wires `predecessor`/`successor`/`Junction` objects using the `connector`-derived topology (this is your `<junction>`/`<laneLink>` population step).
  3. Attaches signals/lane marks from Phase 3 at their `(s, t)` offsets.
  4. Calls the library's own `write_xml()` and then **independently** validates the output against the official ASAM `.xsd` (don't just trust the library's serializer — add a real `lxml.etree.XMLSchema` validation step as a CI gate, matching your Slide 10 "100% pass on ASAM official XSD schema" requirement).
- Ask Antigravity to generate this as a pytest suite with at least one synthetic fixture per topology type your defense needs to demonstrate (simple road, T-junction, four-way junction, opposing-lane trap case) — the last one specifically tests the "illegal connections" failure mode named on Slide 10.

---

## 8. Suggested Antigravity Workflow, End to End

1. One agent session, Planning Mode: draft `PROJECT.md` + `docs/schema_notes.md` from the three references (§9) — review the Implementation Plan before accepting.
2. One agent session per phase (1→4), Planning Mode, each reading the previous phase's output contract from `PROJECT.md`; approve each Task List before execution; set review level to "always" for Phases 2 and 4.
3. Use the browser-in-the-loop agent to pull a small real-world Overture + OSM extract for one test intersection (e.g., via `overturemaps` CLI + Overpass API) and commit it as a fixture — don't let the agent invent synthetic coordinates for your core test case.
4. Final agent session, Fast Mode: wire a CLI entry point (`generate_xodr.py --bbox ... --out result.xodr`) and an XSD-validation report generator, since your defense will want a one-command reproducible demo.
5. Keep every Implementation Plan / Task List artifact Antigravity produces — they double as your methodology documentation and make the "deterministic, auditable pipeline" claim in your thesis verifiable by your committee.

---

## 8b. Quality Control: Should You Use QC-OpenDRIVE?

**Short answer: yes — adopt ASAM's own `qc-opendrive` checker bundle as your real QC layer for Phase 4, but don't treat it as a complete solution out of the box.**

### What it is, and why it fits

- `qc-opendrive` is the **official ASAM checker bundle for ASAM OpenDRIVE**, built on top of the ASAM Quality Checker (QC) Framework — the same organization that owns the OpenDRIVE standard itself. It is not a third-party or academic tool; it's the de facto reference implementation.
- It goes beyond plain XSD validation. Formal XML Schema correctness is checked as one part of it, but the framework is explicitly designed so developers can write **semantic rule sets** on top of the schema — the exact "topological validity beyond just well-formed XML" requirement from Slide 10.
- Concrete rules already in the bundle that map directly onto your Slide 10 failure modes:
  - No mixed access rules (allow/deny) at the same `sOffset` on a lane.
  - Lanes that continue across lane-section boundaries must be connected in both directions.
  - Two roads must not be linked directly if the relationship between them is ambiguous — a `<junction>` must be used instead (this is your "illegal/ambiguous connections" check).
  - A lane with zero width at the start/end of a lane section must not have a predecessor/successor there (this is your "dangling successor" check).
- It's installable via `pip install asam-qc-opendrive`, runnable as a standalone CLI (`qc_opendrive -c config.xml`) or wired into the full Docker-based QC-Framework pipeline with machine-readable XML results — straightforward to drop into a CI gate right after your `scenariogeneration` assembly step in Phase 4.

### Where it falls short for your specific requirements

- **Coverage isn't exhaustive.** There are no first-class, out-of-the-box rules for **carriageway width-rule consistency** (making sure the Overture `width_rules`-derived lane widths stay coherent across a road) or **junction kinematic/steering-angle limits** — both of which are requirements you named on Slide 10. Budget engineering time to add these as custom rules.
- **The framework is built to be extended, not just consumed.** Checker bundles are just executables registered with the QC-Framework via a manifest file, so writing your own rule (in Python, using the provided `qc-baselib-py` interface) and registering it alongside the stock bundle is a supported, documented workflow — not a hack.
- **A good implementation reference for exactly this:** the paper *"Integrating LLMs with QC-OpenDRIVE: Ensuring Normative Correctness in Autonomous Driving Scenarios"* (Möhlmann et al.) contributed a new rule, **E.5.9.1 (`road.geometry.contact_point`)** — checking that connected roads' reference-line endpoints geometrically coincide — directly into `qc-opendrive` via a real, merged pull request (`github.com/asam-ev/qc-opendrive/pull/126`). This is your G2/geometric-continuity check, already implemented and mergeable into your pipeline, and it's also the cleanest template for how to write and submit your own width/junction rules the same way.
- **Don't borrow their scale claims.** That paper's own evaluation never went past 1–2 roads with no complex junctions — they explicitly avoided harder cases because their LLM-generation approach couldn't handle them. That's a limitation of *their* LLM-generates-raw-geometry method, not of the checker itself, but it means you should validate coverage/performance on your own junction-heavy fixtures rather than citing their results as proof the tool scales.

### How this fits your thesis narrative

- Cite `qc-opendrive` / the ASAM QC-Framework as your adopted QC tool for Phase 4.
- Cite the Möhlmann et al. paper narrowly and correctly: as the source of the contact-point continuity rule you're reusing, as a real example of extending the checker, and — usefully for your own motivation section — as evidence that even state-of-the-art LLMs generating OpenDRIVE geometry directly still fail continuity checks, reinforcing why Approach 2 deliberately keeps geometry generation deterministic (Overture/Shapely/`scenariogeneration`) and restricts any LLM involvement to semantic/classification tasks (e.g., parsing OSM `turn:lanes` tags in Phase 3).
- In Antigravity, this is a good candidate for its own agent session: "install and wire `qc-opendrive` into the Phase 4 CI gate; implement two custom rules — carriageway width consistency across a road, and junction steering-angle limit — following the pattern of the merged `road.geometry.contact_point` rule in PR #126 of `asam-ev/qc-opendrive`." Use Planning Mode and review level "always," since these rules are your actual proof-of-correctness for the defense.

---

## 9. References Used

- Overture Maps Foundation — Schema Reference (overview, Pydantic-based schema philosophy): https://docs.overturemaps.org/schema/
- Overture Maps Foundation — Transportation `Connector` schema (fields, GERS `id`, `geometry`, `sources`): https://docs.overturemaps.org/schema/reference/transportation/connector
- Overture Maps Foundation — Transportation `Segment` schema (`geometry`, `connectors[]`, `width_rules[]`, `subtype`/`class`/`subclass`, `road_flags`, `speed_limits`): https://docs.overturemaps.org/schema/reference/transportation/segment
- OpenStreetMap Wiki — `Openstreetmap-website/Database_schema` (current vs. master geodata tables, 1e7-scaled lat/lon integers): https://wiki.openstreetmap.org/wiki/Openstreetmap-website/Database_schema
- OpenStreetMap-website — `db/structure.sql` (authoritative table definitions: `current_nodes`, `current_node_tags`, `current_ways`, `current_way_nodes`, `current_way_tags`, `current_relations`, `current_relation_members`): https://github.com/openstreetmap/openstreetmap-website/blob/master/db/structure.sql
- Google Antigravity IDE — product overview, Agent Manager, Implementation Plan / Task List artifacts, Planning vs. Fast Mode, browser-in-the-loop, MCP Store: https://antigravity.google/product/antigravity-ide and https://codelabs.developers.google.com/getting-started-google-antigravity
- ASAM e.V. — `qc-opendrive` official OpenDRIVE checker bundle (installation, rule structure, CLI usage): https://github.com/asam-ev/qc-opendrive and https://pypi.org/project/asam-qc-opendrive/
- ASAM e.V. — `qc-framework` (ASAM Quality Checker Framework architecture, extensibility, checker bundle model): https://github.com/asam-ev/qc-framework
- Möhlmann et al. — "Integrating LLMs with QC-OpenDRIVE: Ensuring Normative Correctness in Autonomous Driving Scenarios" (source of rule E.5.9.1 `road.geometry.contact_point`; evaluated only on 1–2 road toy networks): https://link.springer.com/chapter/10.1007/978-3-032-07132-3_7, merged rule PR: https://github.com/asam-ev/qc-opendrive/pull/126
