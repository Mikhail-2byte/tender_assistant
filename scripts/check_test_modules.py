"""Run only tests/ modules in isolated processes, with bounded execution."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
import xml.etree.ElementTree as ET


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    results = []
    for module in sorted((root / "tests").glob("test_*.py")):
        started = time.monotonic()
        report = output / (module.stem + ".xml")
        with (output / (module.stem + ".log")).open("w", encoding="utf-8") as log:
            try:
                process = subprocess.run(
                    [sys.executable, "-X", "faulthandler", "-m", "pytest", str(module),
                     "-q", "-o", "faulthandler_timeout=60", f"--junitxml={report}"], cwd=root,
                    stdout=log, stderr=subprocess.STDOUT, timeout=args.timeout,
                )
                code = process.returncode
            except subprocess.TimeoutExpired:
                code = "timeout"
        result = {"module": module.name, "exit_code": code,
                  "seconds": round(time.monotonic() - started, 2)}
        if report.exists():
            suites = ET.parse(report).getroot().iter("testsuite")
            counts = [dict(suite.attrib) for suite in suites]
            for name in ("tests", "failures", "errors", "skipped"):
                result[name] = sum(int(suite.get(name, 0)) for suite in counts)
        results.append(result)
        (output / "summary.json").write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"{module.name}: {code}", flush=True)
    return int(any(result["exit_code"] != 0 for result in results))


if __name__ == "__main__":
    raise SystemExit(main())
