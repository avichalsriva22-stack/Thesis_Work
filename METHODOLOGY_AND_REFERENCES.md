# Methodologies, Design Decisions, and References

This document details the architectural decisions, methodologies, and reference materials that guided the development of the OpenDRIVE (XODR) generation pipeline, specifically focusing on data ingestion, semantic filtering, and map topology generation.

---

## 1. Decision: Utilizing Overture Maps in Conjunction with OpenStreetMap (OSM)
### Reasoning
While OSM provides a vast repository of crowd-sourced geospatial data, it is prone to tagging inconsistencies and topological errors. Overture Maps Foundation (OMF) addresses this by conflating data from multiple sources (Meta, TomTom, Microsoft, OSM) into a highly standardized Global Entity Reference System (GERS). 

For our pipeline, directly reading Overture's `segment` and `connector` parquet records via AWS S3 provides a more robust and uniform schema for road topologies compared to raw OSM nodes and ways.

### Methodology
- **Bounding Box Extraction:** We implemented a geospatial query system that fetches map tiles for specific coordinates (e.g., the IIT Kanpur viaduct) in Parquet format.
- **Topology Mapping:** Overture's `connector` layer inherently defines intersection points, significantly reducing the algorithmic complexity required to calculate road junctions from scratch.

### References
- **Overture Maps Foundation:** *Schema and Global Entity Reference System (GERS)*. Documentation on how segments and connectors are defined globally.
- **Overpass API Documentation:** Used as the fallback/primary source for raw OSM data ingestion.

---

## 2. Decision: Strict Semantic Filtering (`ALLOWED_ROAD_CLASSES`)
### Reasoning
When converting map data to ASAM OpenDRIVE (XODR) format for autonomous vehicle (AV) simulations (such as CARLA or VTD), the inclusion of non-drivable paths (e.g., pedestrian footways, cycleways, staircases) introduces severe topological noise. Simulators require clean vehicular road networks.

### Methodology
We applied a strict semantic filter based on the standard highway tagging taxonomy. By iterating through the dataset and discarding any `class` not in our allowed set, we ensure the generated XODR file is lightweight and simulator-ready.
- **Allowed Classes:** `motorway`, `trunk`, `primary`, `secondary`, `tertiary`, `residential`, `unclassified`, `service`, `living_street`, `unknown`.
- The `unknown` tag was intentionally preserved to ensure custom viaducts or newly mapped roads lacking strict categorizations are not inadvertently deleted during pipeline execution.

### References
- **OpenStreetMap Wiki:** *Key:highway*. The definitive taxonomy for road classification which heavily influenced our `ALLOWED_ROAD_CLASSES` filter.
- **Academic Literature:** *Map Generation for Autonomous Driving Simulators*. (e.g., Althoff et al., focusing on the necessity of filtering raw map data for AV simulation environments).

---

## 3. Decision: WKB Parsing via Shapely and GEOS
### Reasoning
Geospatial data from Overture is transmitted in Well-Known Binary (WKB) format. To convert this into mathematical primitives (lines and points) required for XODR parametric curves, a robust computational geometry engine is required.

### Methodology
We utilized `shapely.wkb` to parse the byte streams into `LineString` (for road segments) and `Point` (for road connectors). `Shapely` serves as a Python wrapper around GEOS (Geometry Engine - Open Source), guaranteeing precision in planar geometric coordinate extraction, which is critical for calculating heading, curvature, and length in OpenDRIVE.

### References
- **OGC (Open Geospatial Consortium):** *Simple Feature Access - Part 1: Common Architecture*. The standard defining WKT and WKB geometries.
- **GEOS (Geometry Engine, Open Source):** The underlying C++ library used for planar geometry processing.

---

## 4. Decision: OpenDRIVE (XODR) Quality Control (QC)
### Reasoning
Map generation pipelines are prone to logical errors (e.g., overlapping geometries or broken connector references). Generating XODR files without validation leads to immediate crashes when imported into simulators.

### Methodology
We introduced an automated QC step that generates `test_out_qc_config.xml` and runs structural tests on the resulting XODR XML (`test_out_qc_result.xml`). This ensures that for every `<road>`, the internal `<link>` and `<planView>` definitions strictly adhere to the OpenDRIVE XML schema.

### References
- **ASAM OpenDRIVE Specification (Version 1.6 / 1.7):** *Association for Standardization of Automation and Measuring Systems*. The primary technical standard dictating the XML schema, road linkage rules, and geometric representations (e.g., Euler spirals, cubic polynomials) required for the output.

---

## Summary of Relevant Academic / Technical Literature for Thesis Work
If expanding this work into a formal thesis, the following themes and papers form the foundation of this methodology:
1. **Generating OpenDRIVE from Open Data:** Research demonstrating the algorithmic pipeline from node/way mapping to parametric cubic splines/clothoids used in OpenDRIVE.
2. **Geospatial Data Conflation:** Literature surrounding the Overture Maps Foundation's approach to merging OSM with proprietary datasets.
3. **Simulation Verification:** Studies on the validation of digital twin road networks for ADAS (Advanced Driver Assistance Systems) testing.
