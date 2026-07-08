# Issue and Fix Report: Overture Maps Data Ingestion

## 🚨 The Issue
When generating OpenDRIVE (XODR) data from Overture Maps, the pipeline was indiscriminately ingesting all types of map segments within a bounding box. This caused two main problems:
1. **Noisy Data:** Non-drivable paths such as footways, cycleways, or pedestrian zones were being included in the road network.
2. **Missing/Unknown Tags:** Specific road structures, such as viaducts (e.g., the IIT Kanpur viaduct), needed to be inspected to ensure their specific Overture `class` or `subclass` tags were being handled correctly by the OpenDRIVE pipeline.

Without filtering, the resulting XODR data was cluttered and semantically incorrect for vehicular simulation.

## 🛠️ What We Fixed

### 1. Created a Debugging Script (`debug_overture.py`)
To understand exactly how Overture tags its segments (especially structures like viaducts), we created a standalone script to fetch and inspect raw Overture data.
- **Action:** Fetched data for a specific bounding box (IIT Kanpur viaduct area).
- **Result:** Allowed us to inspect the `class`, `subclass`, `subtype`, and `road_flags` of the map segments to determine what tags are actually drivable.

### 2. Implemented a Semantic Filter in Ingestion (`src/ingestion.py`)
Based on our findings, we updated the Overture data ingestion logic to explicitly filter out non-drivable segments.
- **Action:** Added an `ALLOWED_ROAD_CLASSES` set containing only drivable classes:
  ```python
  ALLOWED_ROAD_CLASSES = {
      'motorway', 'trunk', 'primary', 'secondary', 'tertiary',
      'residential', 'unclassified', 'service', 'living_street', 'unknown'
  }
  ```
- **Action:** Applied a strict semantic filter during the segment parsing loop. Any segment with a `class` not in the allowed list is now discarded (`continue`).

### 3. Validated with Quality Control (QC)
- **Action:** Ran the pipeline with the newly filtered data.
- **Result:** Generated `test_out_qc_config.xml` and `test_out_qc_result.xml` to validate the structural integrity and semantic correctness of the output XODR file. The pipeline now successfully produces cleaner, drivable road networks.

## 📁 Files Modified/Created
- `[MODIFY]` `xodr_pipeline/src/ingestion.py`
- `[NEW]` `xodr_pipeline/debug_overture.py`
- `[NEW]` `xodr_pipeline/test_out_qc_config.xml`
- `[NEW]` `xodr_pipeline/test_out_qc_result.xml`
