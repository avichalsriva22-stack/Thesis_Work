# Approach 2: Overture-Primary XODR Pipeline

## Architecture Overview
This project implements a pipeline to generate ASAM OpenDRIVE (XODR) maps using **Overture Maps as the primary structural backbone** and **OpenStreetMap (OSM) as a secondary semantic enrichment source**. 

The pipeline is deterministic, explicitly avoiding LLM-hallucinated geometry. LLMs are only permitted for classification tasks (e.g., parsing string tags).

## Project Phases
1. **Phase 1: Global Anchoring**
   - Ingest Overture and OSM data.
   - Project WGS84 coordinates into a shared local Cartesian (UTM) frame.
   - Output: In-memory/local unified coordinate model keyed by stable IDs.

2. **Phase 2: Overture as Backbone**
   - Overture `segment.geometry` (LineStrings) -> Continuous OpenDRIVE `<planView>` geometry using line/arc/clothoid fitting (`scipy`/`shapely`).
   - Overture `connectors` (`at` parameter) -> Topological routing graph (`<road>`, `<junction>`).
   - Overture `segment.width_rules` -> `<laneSection>` widths.

3. **Phase 3: OSM Point-Feature Enrichment**
   - Project OSM point features (`traffic_signals`, `turn:lanes`) orthogonally onto the Overture backbone.
   - Extract `(s, t)` offsets for placement in OpenDRIVE.
   - **No OSM geometry modifies the backbone.**

4. **Phase 4: Assembly & Compilation**
   - Instantiate OpenDRIVE XML using the `scenariogeneration` library in a strict, XSD-compliant order.
   - Merge Phase 2 topology and Phase 3 semantic offsets.
   - Run `lxml.etree.XMLSchema` validation and `qc-opendrive` rules.

## Core Directives & Constraints
- **Zero LLM Geometry**: All coordinates and projections must be mathematically derived using `pyproj`, `shapely`, etc.
- **ASAM Compliance**: 100% pass on official `.xsd`.
- **G2 Continuity**: Continuity is verified at every connector junction.

## Tech Stack
- **Coordinate Math**: `pyproj`, `shapely`, `scipy.interpolate`
- **Data Ingestion**: `duckdb` / `overturemaps`, `pyosmium`
- **XODR Assembly**: `scenariogeneration`
- **Quality Control**: `qc-opendrive`, `lxml`
