# Schema Notes

## Overture Maps Foundation
Reference: https://docs.overturemaps.org/schema/reference/transportation/

### `segment` feature
- **`id`**: GERS-compatible ID string.
- **`geometry`**: LineString (WGS84).
- **`connectors[]`**: Array of connection points. Crucially contains `at` (float `[0, 1]`), representing normalized position along the segment.
- **`width_rules[]`**: Array of carriageway widths, optionally scoped with `between` (e.g., `[0.2, 0.8]`).
- **`subtype` / `class` / `subclass`**: Used for default lane count/road type initialization.
- **`sources[]`**: e.g., `{"dataset": "OpenStreetMap", "record_id": "way/12345"}` - highly useful for matching in Phase 3.

### `connector` feature
- **`id`**: GERS-compatible ID string.
- **`geometry`**: Point (WGS84).

## OpenStreetMap (OSM)
Reference: https://wiki.openstreetmap.org/wiki/Openstreetmap-website/Database_schema

- **Coordinates (`current_nodes`)**: Depending on the extraction method (raw DB dump vs PBF), raw `latitude` and `longitude` fields may be stored as integers scaled by `1e7`. `lat = raw_lat / 1e7`.
- **Semantic Tags**: `current_node_tags` (e.g., `k='highway', v='traffic_signals'`) and `current_way_tags` (e.g., `k='turn:lanes', v='left|through;right'`).
- **Matching Keys**: `id` + `version`.
