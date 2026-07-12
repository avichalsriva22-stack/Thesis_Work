from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import os
import sys
import time
import traceback
import io

# ── Force UTF-8 stdout/stderr so pipeline arrow chars don't crash Windows terminal
if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if sys.stderr and hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# Add the project root (APPROACH 2/xodr_pipeline) to sys.path so 'src' can be imported
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.pipeline import run_pipeline

# ── Output directory (sits next to this file) ─────────────────────────────────
OUTPUT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "output"))
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ── Static frontend dir ────────────────────────────────────────────────────────
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(STATIC_DIR, exist_ok=True)

app = FastAPI(title="OpenDRIVE Pipeline Generator")

# Allow CORS for local dev
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve the frontend
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class BoundingBoxRequest(BaseModel):
    min_lon: float
    min_lat: float
    max_lon: float
    max_lat: float
    output_filename: str = "generated.xodr"  # Optional custom filename


@app.get("/", response_class=HTMLResponse)
async def get_index():
    index_path = os.path.join(STATIC_DIR, "index.html")
    if not os.path.isfile(index_path):
        return HTMLResponse("<h2>No frontend found. POST to /generate with a bbox JSON body.</h2>")
    with open(index_path, "r", encoding="utf-8") as f:
        return f.read()


@app.post("/generate", response_class=PlainTextResponse)
async def generate_xodr_endpoint(bbox: BoundingBoxRequest):
    """
    Accepts a bounding box, runs the full 6-phase pipeline, writes the XODR
    to disk (same as test_pipeline.py), runs QC OpenDRIVE validation, and
    returns the XML string.

    Body (JSON):
        {
            "min_lon": -73.987,
            "min_lat": 40.756,
            "max_lon": -73.984,
            "max_lat": 40.758,
            "output_filename": "my_area.xodr"   (optional)
        }
    """
    print(f"\n[API] Received generate request: {bbox}")

    # Sanitize filename
    filename = bbox.output_filename.strip()
    if not filename.endswith(".xodr"):
        filename += ".xodr"
    # Remove path traversal characters
    filename = os.path.basename(filename)

    output_path = os.path.join(OUTPUT_DIR, filename)
    print(f"[API] Output will be saved to: {output_path}")

    try:
        xodr_xml = run_pipeline(
            bbox.min_lon, bbox.min_lat, bbox.max_lon, bbox.max_lat,
            output_path=output_path   # ← writes file + runs QC, same as test script
        )
        print(f"[API] Pipeline succeeded. Returning XML ({len(xodr_xml)} bytes).")
        return xodr_xml
    except Exception as e:
        err_msg = str(e).encode('ascii', errors='replace').decode('ascii')
        trace_str = traceback.format_exc().encode('ascii', errors='replace').decode('ascii')
        print(f"[API ERROR] Pipeline failed: {err_msg}")
        raise HTTPException(
            status_code=500,
            detail={"message": err_msg, "traceback": trace_str}
        )


@app.get("/download/{filename}", response_class=FileResponse)
async def download_xodr(filename: str):
    """
    Download a previously generated XODR file by name.
    e.g. GET /download/generated.xodr
    """
    filename = os.path.basename(filename)  # prevent path traversal
    file_path = os.path.join(OUTPUT_DIR, filename)
    if not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail=f"File '{filename}' not found in output directory.")
    return FileResponse(file_path, media_type="application/octet-stream", filename=filename)


@app.get("/health")
async def health():
    return {"status": "ok", "output_dir": OUTPUT_DIR}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=8080, reload=True)
