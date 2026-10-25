from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys

from aqi_hanoi.core.settings import PROJECT_DIR


COLLECTORS = [
    ("aqi_hanoi.collectors.collect_noaa_weather", "NOAA METAR VVNB"),
    ("aqi_hanoi.collectors.collect_hanoi_portal", "Hanoi monitoring portal"),
    ("aqi_hanoi.collectors.collect_sensorcommunity", "Sensor.Community"),
    ("aqi_hanoi.collectors.collect_openaq", "OpenAQ"),
]
STATUS_PATH = PROJECT_DIR / "figures" / "source_collection_status.json"
LOG_PATH = PROJECT_DIR / "raw" / "source_collection.log"


def _timestamp():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _write_status(status, status_path):
    status_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = status_path.with_name(status_path.name + ".tmp")
    temporary.write_text(
        json.dumps(status, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(status_path)


def collect_optional_sources(project_dir=PROJECT_DIR, run_collector=None):
    project_dir = Path(project_dir)
    status_path = project_dir / STATUS_PATH.relative_to(PROJECT_DIR)
    log_path = project_dir / LOG_PATH.relative_to(PROJECT_DIR)
    execute = run_collector or (
        lambda script: subprocess.run(
            [sys.executable, "-m", script],
            cwd=project_dir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, "PYTHONUTF8": "1"},
            check=False,
        )
    )
    status = {
        "started_at": _timestamp(),
        "finished_at": None,
        "sources": [],
    }
    _write_status(status, status_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    with log_path.open("a", encoding="utf-8") as log:
        for script, source in COLLECTORS:
            if (
                source == "OpenAQ"
                and not os.environ.get("OPENAQ_API_KEY", "").strip()
            ):
                result = {
                    "source": source,
                    "script": script,
                    "status": "SKIPPED",
                    "started_at": _timestamp(),
                    "finished_at": _timestamp(),
                    "exit_code": None,
                    "detail": "OPENAQ_API_KEY is not configured.",
                }
                print(f"{source}: skipped; OPENAQ_API_KEY is not configured.")
            else:
                result = {
                    "source": source,
                    "script": script,
                    "status": "RUNNING",
                    "started_at": _timestamp(),
                    "finished_at": None,
                    "exit_code": None,
                    "detail": "",
                }
                status["sources"].append(result)
                _write_status(status, status_path)
                print(f"\n===== {script} =====", flush=True)
                log.write(f"\n===== {source} | {result['started_at']} =====\n")
                try:
                    completed = execute(script)
                    if isinstance(completed, int):
                        exit_code = completed
                        output = ""
                    else:
                        exit_code = completed.returncode
                        output = (completed.stdout or "") + (completed.stderr or "")
                except OSError as error:
                    exit_code = 1
                    output = f"{type(error).__name__}: {error}\n"
                log.write(output)
                log.flush()
                result.update({
                    "finished_at": _timestamp(),
                    "exit_code": int(exit_code),
                    "status": "SUCCEEDED" if exit_code == 0 else "FAILED",
                    "detail": (
                        "" if exit_code == 0
                        else f"Collector exited with code {exit_code}; see raw/source_collection.log."
                    ),
                })
                _write_status(status, status_path)
                if exit_code == 0:
                    print(f"{source}: collected successfully.", flush=True)
                else:
                    print(
                        f"WARNING: {source} collection failed with code "
                        f"{exit_code}; see raw/source_collection.log.",
                        flush=True,
                    )

            if result["status"] == "SKIPPED":
                status["sources"].append(result)
                _write_status(status, status_path)

    status["finished_at"] = _timestamp()
    _write_status(status, status_path)
    failures = sum(item["status"] == "FAILED" for item in status["sources"])
    skips = sum(item["status"] == "SKIPPED" for item in status["sources"])
    print(
        f"Optional data sources finished: "
        f"{len(status['sources']) - failures - skips} succeeded, "
        f"{failures} failed, {skips} skipped.",
        flush=True,
    )
    return status


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    collect_optional_sources()


if __name__ == "__main__":
    main()
