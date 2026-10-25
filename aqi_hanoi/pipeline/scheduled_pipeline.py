from datetime import datetime
import json
from pathlib import Path
import subprocess
import sys
import time

from aqi_hanoi.core.settings import PROJECT_DIR


STEPS = [
    "aqi_hanoi.collectors.collect_aq_model",
    "aqi_hanoi.collectors.collect_weather",
    "aqi_hanoi.collectors.collect_waqi",
    "aqi_hanoi.collectors.collect_waqi_feed",
    "aqi_hanoi.pipeline.etl_feed",
    "aqi_hanoi.pipeline.etl",
    "aqi_hanoi.collectors.collect_optional_sources",
    "aqi_hanoi.pipeline.data_quality",
    "aqi_hanoi.alerts.generate_alerts",
    "aqi_hanoi.pipeline.forecast_monitoring",
    "aqi_hanoi.alerts.notify_alerts",
    "aqi_hanoi.analysis.compare_stations",
]
STATUS_PATH = PROJECT_DIR / "figures" / "pipeline_status.json"


def _timestamp():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _write_status(path, status):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(status, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def _previous_success(path):
    path = Path(path)
    if not path.is_file():
        return None
    try:
        previous = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(
            f"Không đọc được trạng thái pipeline trước đó {path}: {error}"
        ) from error
    if not isinstance(previous, dict):
        raise ValueError(f"Tệp trạng thái pipeline không hợp lệ: {path}")
    if previous.get("status") == "SUCCEEDED":
        return previous.get("finished_at")
    return previous.get("last_success_at")


def run_pipeline(project_dir=PROJECT_DIR, status_path=None, run_step=None):
    project_dir = Path(project_dir)
    status_path = (
        Path(status_path)
        if status_path is not None
        else project_dir / "figures" / "pipeline_status.json"
    )
    last_success_at = _previous_success(status_path)
    execute = run_step or (
        lambda step: subprocess.run(
            [sys.executable, "-m", step],
            cwd=project_dir,
            check=False,
        ).returncode
    )
    started = time.monotonic()
    status = {
        "status": "RUNNING",
        "started_at": _timestamp(),
        "finished_at": None,
        "last_success_at": last_success_at,
        "duration_seconds": None,
        "failed_step": None,
        "exit_code": None,
        "steps": [],
        "error": None,
    }
    _write_status(status_path, status)

    for step in STEPS:
        step_status = {
            "name": step,
            "status": "RUNNING",
            "started_at": _timestamp(),
            "finished_at": None,
            "exit_code": None,
        }
        status["steps"].append(step_status)
        _write_status(status_path, status)
        print(f"\n===== {step} =====", flush=True)
        try:
            exit_code = execute(step)
        except OSError as error:
            exit_code = 1
            status["error"] = f"{type(error).__name__}: {error}"
        step_status["finished_at"] = _timestamp()
        step_status["exit_code"] = int(exit_code)
        step_status["status"] = "SUCCEEDED" if exit_code == 0 else "FAILED"
        if exit_code != 0:
            status.update({
                "status": "FAILED",
                "finished_at": _timestamp(),
                "duration_seconds": round(time.monotonic() - started, 2),
                "failed_step": step,
                "exit_code": int(exit_code),
            })
            _write_status(status_path, status)
            return int(exit_code)
        _write_status(status_path, status)

    finished_at = _timestamp()
    status.update({
        "status": "SUCCEEDED",
        "finished_at": finished_at,
        "last_success_at": finished_at,
        "duration_seconds": round(time.monotonic() - started, 2),
        "exit_code": 0,
    })
    _write_status(status_path, status)
    print("Hourly pipeline completed successfully.", flush=True)
    return 0


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    return run_pipeline()


if __name__ == "__main__":
    raise SystemExit(main())
