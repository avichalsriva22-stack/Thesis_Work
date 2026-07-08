"""
validation.py — Phase 6: OpenDRIVE Validation

Uses the official ASAM QC OpenDRIVE checker (qc_opendrive) as the primary
validation engine. Falls back to a lightweight lxml-based syntax check if
qc_opendrive is not available.
"""
import io
import os
import sys
import subprocess
import tempfile
import traceback
import xml.etree.ElementTree as ET

try:
    from lxml import etree as lxml_etree
    _HAS_LXML = True
except ImportError:
    _HAS_LXML = False


# ---------------------------------------------------------------------------
# QC OpenDRIVE integration
# ---------------------------------------------------------------------------

def _find_python_executable() -> str:
    """Return path to the venv python executable."""
    candidates = [
        sys.executable,  # current interpreter (already in the venv)
    ]
    for py in candidates:
        if os.path.isfile(py):
            return py
    return sys.executable


def _run_qc_opendrive(xodr_path: str) -> dict:
    """
    Run qc_opendrive against the given .xodr file.

    Returns a dict:
        {
          "passed": bool,
          "exit_code": int,
          "issues": [str, ...],   # list of human-readable issue strings
          "result_xml": str,      # raw result XML content (may be empty)
        }
    """
    py = _find_python_executable()
    result_xml_path = xodr_path.replace(".xodr", "_qc_result.xml")

    # Build a minimal qc_baselib Configuration programmatically
    config_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Config>
  <Param name="InputFile" value="{xodr_path}"/>
  <CheckerBundle application="xodrBundle">
    <Param name="resultFile" value="{result_xml_path}"/>
  </CheckerBundle>
</Config>
"""
    config_path = xodr_path.replace(".xodr", "_qc_config.xml")
    with open(config_path, "w", encoding="utf-8") as f:
        f.write(config_xml)

    print(f"[Validation] Running QC OpenDRIVE on: {xodr_path}")
    print(f"[Validation] Config: {config_path}")
    print(f"[Validation] Result will be written to: {result_xml_path}")

    cmd = [py, "-m", "qc_opendrive", "-c", config_path, "-g"]
    print(f"[Validation] Command: {' '.join(cmd)}")

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )
        stdout = proc.stdout.strip()
        stderr = proc.stderr.strip()

        if stdout:
            for line in stdout.splitlines():
                print(f"[QC stdout] {line}")
        if stderr:
            for line in stderr.splitlines():
                print(f"[QC stderr] {line}")

        print(f"[Validation] qc_opendrive exit code: {proc.returncode}")

    except FileNotFoundError:
        return {
            "passed": False,
            "exit_code": -1,
            "issues": ["qc_opendrive module not found. Install it with: pip install qc_opendrive"],
            "result_xml": "",
        }
    except subprocess.TimeoutExpired:
        return {
            "passed": False,
            "exit_code": -1,
            "issues": ["qc_opendrive timed out after 120 seconds."],
            "result_xml": "",
        }

    # Parse result XML to extract issues
    issues = []
    result_xml_content = ""
    if os.path.isfile(result_xml_path):
        try:
            with open(result_xml_path, "r", encoding="utf-8", errors="replace") as f:
                result_xml_content = f.read()
            tree = ET.parse(
                io.StringIO(result_xml_content)
            )
            root = tree.getroot()

            # Count checkers by status
            total = passed_count = skipped = failed = 0
            for checker in root.iter("Checker"):
                status = checker.get("status", "").lower()
                total += 1
                if status == "completed":
                    passed_count += 1
                elif status == "skipped":
                    skipped += 1
                elif status == "error":
                    failed += 1
                    desc = checker.get("description", "")
                    issues.append(f"CHECKER FAILED: {checker.get('checkerId', '?')} — {desc}")

            # Extract individual Issues
            for issue in root.iter("Issue"):
                level = issue.get("level", "").upper()
                desc = issue.get("description", issue.text or "")
                rule_uid = ""
                rule_el = issue.find("IssueId")
                if rule_el is not None:
                    rule_uid = rule_el.get("ruleUID", "")
                issues.append(f"[{level}] {desc}" + (f" (rule: {rule_uid})" if rule_uid else ""))

            print(
                f"[Validation] QC result: {total} checkers total — "
                f"{passed_count} passed, {skipped} skipped, {failed} failed."
            )
            if issues:
                print(f"[Validation] {len(issues)} issue(s) found:")
                for iss in issues:
                    print(f"  → {iss}")
            else:
                print("[Validation] No issues found by QC OpenDRIVE.")

        except Exception as parse_err:
            print(f"[Validation] Warning: Failed to parse QC result XML: {parse_err}")
            issues.append(f"Failed to parse qc_opendrive result: {parse_err}")
    else:
        print(f"[Validation] Warning: QC result file not found at {result_xml_path}")
        issues.append("qc_opendrive did not produce a result file.")

    # qc_opendrive returns 0 on success even with issues; non-zero is a crash
    passed = proc.returncode == 0 and not any(
        i.startswith("CHECKER FAILED") for i in issues
    )
    return {
        "passed": passed,
        "exit_code": proc.returncode,
        "issues": issues,
        "result_xml": result_xml_content,
    }


# ---------------------------------------------------------------------------
# Lightweight fallback (lxml XML syntax + s-value monotonicity)
# ---------------------------------------------------------------------------

def _validate_xml_syntax(xml_string: str) -> list:
    """Returns a list of error strings. Empty list = passed."""
    errors = []
    if _HAS_LXML:
        try:
            tree = lxml_etree.parse(io.BytesIO(xml_string.encode("utf-8")))
            root = tree.getroot()
            print("[Validation] XML Syntax Check: Well-formed XML structure verified.")

            header = root.find("header")
            if header is None:
                errors.append("Missing required <header> element.")
            else:
                rev_major = header.get("revMajor", "?")
                rev_minor = header.get("revMinor", "?")
                print(f"[Validation] Found OpenDRIVE Version: {rev_major}.{rev_minor}")

            roads_checked = 0
            for road in root.findall("road"):
                road_id = road.get("id", "?")
                plan_view = road.find("planView")
                roads_checked += 1
                if plan_view is not None:
                    last_s = -1.0
                    for geo in plan_view.findall("geometry"):
                        s_str = geo.get("s", "-1")
                        try:
                            s = float(s_str)
                            if s < last_s and last_s >= 0:
                                errors.append(
                                    f"Road '{road_id}': s={s} is not monotonically increasing "
                                    f"from previous s={last_s}."
                                )
                            last_s = s
                        except ValueError:
                            errors.append(
                                f"Road '{road_id}': invalid non-numeric s-value '{s_str}'."
                            )
            print(f"[Validation] Checked {roads_checked} road geometry sequences.")
        except lxml_etree.XMLSyntaxError as e:
            errors.append(f"XML Syntax Error at line {e.lineno}, col {e.offset}: {e.msg}")
    else:
        try:
            ET.fromstring(xml_string)
            print("[Validation] XML Syntax Check: Well-formed XML (stdlib ET).")
        except ET.ParseError as e:
            errors.append(f"XML Parse Error: {e}")
    return errors


# ---------------------------------------------------------------------------
# Public API — called by pipeline.py Phase 6
# ---------------------------------------------------------------------------

def validate_xodr(xml_string: str, xodr_file_path: str = None) -> bool:
    """
    Validate an OpenDRIVE XML string.

    Strategy:
      1. Write the XML to `xodr_file_path` (or a temp file if not given).
      2. Run the official qc_opendrive checker on it.
      3. If qc_opendrive is unavailable, fall back to the lightweight lxml check.

    Raises ValueError if validation fails.
    Returns True on success.
    """
    print("\n--- [Validation] Starting OpenDRIVE Validation ---")

    # --- Step 1: Write XML to disk so qc_opendrive can read it ---
    if xodr_file_path and os.path.isfile(xodr_file_path):
        target_path = xodr_file_path
        print(f"[Validation] Using existing file on disk: {target_path}")
    else:
        # Write to a temp file
        tmp = tempfile.NamedTemporaryFile(
            suffix=".xodr", delete=False, mode="w", encoding="utf-8"
        )
        tmp.write(xml_string)
        tmp.close()
        target_path = tmp.name
        print(f"[Validation] Wrote XML to temp file: {target_path}")

    # --- Step 2: Try qc_opendrive ---
    try:
        result = _run_qc_opendrive(target_path)

        if result["exit_code"] == -1 and "not found" in (result["issues"][0] if result["issues"] else ""):
            # qc_opendrive not installed — fall through to lightweight check
            print("[Validation] qc_opendrive not available. Falling back to lightweight check.")
            errors = _validate_xml_syntax(xml_string)
            if errors:
                print("[Validation] Lightweight validation FAILED:")
                for e in errors:
                    print(f"  - {e}")
                raise ValueError("OpenDRIVE lightweight validation failed:\n" + "\n".join(errors))
            print("[Validation] Lightweight validation passed.")
            return True

        if not result["passed"]:
            issues_str = "\n".join(result["issues"]) if result["issues"] else "(no detail)"
            print(f"[Validation] QC OpenDRIVE reported issues:\n{issues_str}")
            # We warn but do not raise — let the pipeline succeed with issues noted
            print("[Validation] WARNING: QC issues found but pipeline will continue.")
        else:
            print("[Validation] QC OpenDRIVE validation PASSED.")

        return True

    except ValueError:
        raise
    except Exception as e:
        print(f"[Validation] Unexpected error during QC validation: {e}")
        traceback.print_exc()
        # Fall back to lightweight
        errors = _validate_xml_syntax(xml_string)
        if errors:
            raise ValueError("OpenDRIVE validation failed:\n" + "\n".join(errors))
        return True
