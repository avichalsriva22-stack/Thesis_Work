import sys
import os
import traceback

def test_live():
    from src.pipeline import run_pipeline

    print("Testing live pipeline...")
    # Small bounding box around Times Square, NYC
    min_lon = -73.987
    min_lat = 40.756
    max_lon = -73.984
    max_lat = 40.758

    # Output path — written by pipeline before QC validation so qc_opendrive can read it
    output_path = os.path.abspath("test_out.xodr")

    try:
        xml = run_pipeline(min_lon, min_lat, max_lon, max_lat, output_path=output_path)
        print("Pipeline succeeded!")
        print(f"Generated XML length: {len(xml)}")
        # File is already written by pipeline.py Phase 6
        if os.path.isfile(output_path):
            print(f"Output file: {output_path}")
        else:
            # Fallback: write it ourselves
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(xml)
            print(f"Wrote to {output_path}")
    except Exception as e:
        print("Pipeline failed!")
        traceback.print_exc()

if __name__ == '__main__':
    test_live()
