import sys
import xml.etree.ElementTree as ET
from pathlib import Path

report, label, summary = sys.argv[1:]
root = ET.parse(report).getroot()
cases = list(root.iter("testcase"))
if not cases:
    raise SystemExit(f"No test cases found in {report}")
failed = sum(c.find("failure") is not None or c.find("error") is not None for c in cases)
skipped = sum(c.find("skipped") is not None for c in cases)
message = f"{label}: {len(cases)} tests, {failed} failed, {skipped} skipped."
print(message)
output = Path(summary)
output.parent.mkdir(parents=True, exist_ok=True)
with output.open("a") as stream:
    stream.write(message + "\n\n")
if failed:
    raise SystemExit(1)
