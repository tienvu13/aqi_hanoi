from io import BytesIO
import json
from pathlib import Path
import zipfile

import pandas as pd

from aqi_hanoi.analysis.aqi_trends import (
    build_daily_aqi_summary,
    build_hourly_coverage_report,
)
from aqi_hanoi.analysis.station_coverage import build_hourly_comparison, build_station_coverage
from aqi_hanoi.analysis.station_coverage import build_daily_station_summary
from aqi_hanoi.core.waqi_stations import filter_hanoi_waqi_data


EXPORT_WINDOW_DAYS = 30


def _read_export_csv(project_dir, relative_path, required_columns):
    path = project_dir / relative_path
    if not path.is_file():
        return None
    frame = pd.read_csv(path)
    missing = set(required_columns).difference(frame.columns)
    if missing:
        raise ValueError(
            f"{relative_path} thiếu cột bắt buộc: {', '.join(sorted(missing))}"
        )
    return frame


def _recent_rows(frame, time_column, start_time):
    recent = frame.copy()
    recent[time_column] = pd.to_datetime(recent[time_column], errors="coerce")
    return recent.loc[recent[time_column] >= start_time].sort_values(time_column)


def build_dashboard_export(project_dir, now=None):
    """Build a ZIP of safe, summarized dashboard data for local download."""
    project_dir = Path(project_dir)
    current = (
        pd.Timestamp.now(tz="Asia/Bangkok").tz_localize(None)
        if now is None else pd.Timestamp(now)
    )
    if current.tzinfo is not None:
        current = current.tz_convert("Asia/Bangkok").tz_localize(None)
    start_time = current - pd.Timedelta(days=EXPORT_WINDOW_DAYS)
    summary = []
    files = {}

    model = _read_export_csv(
        project_dir,
        "clean/fact_model_hourly.csv",
        {"time", "aqi", "pm25"},
    )
    if model is not None:
        model["aqi"] = pd.to_numeric(model["aqi"], errors="coerce")
        model_recent = _recent_rows(model, "time", start_time)
        model_recent = model_recent.dropna(subset=["aqi"])
        files["aqi_model_hourly_last_30_days.csv"] = model_recent.to_csv(
            index=False, encoding="utf-8-sig"
        )
        daily_summary = build_daily_aqi_summary(
            model,
            days=EXPORT_WINDOW_DAYS,
            now=current,
        )
        files["cams_daily_aqi_summary_last_30_days.csv"] = (
            daily_summary.to_csv(index=False, encoding="utf-8-sig")
        )
        if not model_recent.empty:
            latest = model_recent.iloc[-1]
            summary.extend([
                ("AQI model latest time", latest["time"], ""),
                ("AQI model latest value", latest["aqi"], ""),
            ])
    else:
        summary.append(("AQI model", "Unavailable", "Missing model data file"))

    station_feed = _read_export_csv(
        project_dir,
        "clean/station_feed_hourly.csv",
        {
            "time", "station_uid", "station_name", "aqi_tram", "lat", "lon",
        },
    )
    if station_feed is not None:
        station_feed = filter_hanoi_waqi_data(
            station_feed, "lat", "lon", "station_name"
        )
        station_recent = _recent_rows(station_feed, "time", start_time)
        station_recent["aqi_tram"] = pd.to_numeric(
            station_recent["aqi_tram"], errors="coerce"
        )
        station_recent = station_recent.dropna(
            subset=["time", "station_uid", "aqi_tram"]
        )
        files["waqi_station_hourly_last_30_days.csv"] = station_recent.to_csv(
            index=False, encoding="utf-8-sig"
        )
    else:
        summary.append(("WAQI station history", "Unavailable", "Missing feed file"))

    raw_feed = _read_export_csv(
        project_dir,
        "raw/waqi_feed.csv",
        {
            "station_uid", "station_name", "collected_at", "obs_time",
            "age_min", "aqi", "lat", "lon",
        },
    )
    if raw_feed is not None:
        raw_feed = filter_hanoi_waqi_data(
            raw_feed, "lat", "lon", "station_name"
        )
        coverage = build_station_coverage(raw_feed, now=current)
        files["aqi_hanoi.analysis.station_coverage.csv"] = coverage.to_csv(
            index=False, encoding="utf-8-sig"
        )
        fresh_count = int(coverage["status"].eq("FRESH").sum())
        summary.extend([
            ("Fresh WAQI stations", fresh_count, f"of {len(coverage)} tracked"),
        ])
    else:
        summary.append(("WAQI station coverage", "Unavailable", "Missing feed file"))

    if station_feed is not None:
        tracked_for_summary = (
            raw_feed[["station_uid", "station_name"]]
            .drop_duplicates("station_uid", keep="last")
            if raw_feed is not None else None
        )
        files["waqi_daily_station_summary_last_30_days.csv"] = (
            build_daily_station_summary(
                station_feed,
                days=EXPORT_WINDOW_DAYS,
                now=current,
                tracked_stations=tracked_for_summary,
            ).to_csv(index=False, encoding="utf-8-sig")
        )

    if model is not None and station_feed is not None:
        files["hourly_coverage_last_30_days.csv"] = (
            build_hourly_coverage_report(
                model,
                stations=station_feed,
                days=EXPORT_WINDOW_DAYS,
                now=current,
                tracked_stations=(
                    raw_feed[["station_uid", "station_name"]]
                    .drop_duplicates("station_uid", keep="last")
                    if raw_feed is not None else None
                ),
            ).to_csv(index=False, encoding="utf-8-sig")
        )
        comparison = build_hourly_comparison(model, station_feed)
        comparison_recent = comparison.loc[
            comparison["time"] >= start_time
        ]
        files["cams_waqi_hourly_overlap_last_30_days.csv"] = (
            comparison_recent.to_csv(index=False, encoding="utf-8-sig")
        )
        overlap_count = len(comparison_recent)
        summary.append((
            "WAQI-CAMS overlapping hours",
            overlap_count,
            "Below 24-hour minimum" if overlap_count < 24 else "At or above minimum",
        ))

    history = _read_export_csv(
        project_dir,
        "figures/forecast_history.csv",
        {"generated_at", "target_time", "horizon_hours", "status"},
    )
    if history is not None:
        history_recent = _recent_rows(history, "generated_at", start_time)
        files["forecast_history_last_30_days.csv"] = history_recent.to_csv(
            index=False, encoding="utf-8-sig"
        )

    quality = _read_export_csv(
        project_dir,
        "figures/data_quality_report.csv",
        {"source", "entity", "status", "detail"},
    )
    if quality is not None:
        files["data_quality_report.csv"] = quality.to_csv(
            index=False, encoding="utf-8-sig"
        )
        problems = int(quality["status"].isin(["WARN", "ERROR"]).sum())
        summary.append(("Data-quality WARN/ERROR rows", problems, ""))
    else:
        summary.append((
            "Data-quality report",
            "Unavailable",
            "Missing report file",
        ))

    status_path = project_dir / "figures" / "pipeline_status.json"
    if status_path.is_file():
        pipeline_status = json.loads(status_path.read_text(encoding="utf-8"))
        summary.append((
            "Latest pipeline status",
            pipeline_status.get("status", "UNKNOWN"),
            pipeline_status.get("finished_at", ""),
        ))

    summary_frame = pd.DataFrame(
        [("Report generated", current.isoformat(), ""),
         ("Included time window", f"{start_time} to {current}", ""),
         *summary],
        columns=["metric", "value", "details"],
    )
    files["summary.csv"] = summary_frame.to_csv(
        index=False, encoding="utf-8-sig"
    )

    output = BytesIO()
    with zipfile.ZipFile(output, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for filename, content in files.items():
            archive.writestr(filename, content)
    return output.getvalue()
