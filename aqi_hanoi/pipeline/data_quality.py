from pathlib import Path

import pandas as pd

from aqi_hanoi.core.settings import PROJECT_DIR
from aqi_hanoi.core.waqi_stations import filter_hanoi_waqi_data


MAX_AGE_HOURS = {
    "raw/aq_model_hanoi.csv": 24,
    "raw/weather_hanoi.csv": 168,
    "raw/waqi_feed.csv": 3,
    "clean/fact_model_hourly.csv": 168,
    "clean/fact_station_hourly.csv": 168,
    "clean/station_feed_hourly.csv": 3,
}
REQUIRED_COLUMNS = {
    "raw/aq_model_hanoi.csv": {"time", "us_aqi", "pm2_5", "pm10"},
    "raw/weather_hanoi.csv": {"time", "temperature_2m", "precipitation"},
    "raw/waqi_feed.csv": {
        "collected_at", "obs_time", "age_min", "station_uid", "aqi",
        "station_name", "lat", "lon",
    },
    "clean/fact_model_hourly.csv": {"time", "aqi", "pm25"},
    "clean/fact_station_hourly.csv": {
        "time", "station_uid", "station_name", "aqi", "lat", "lon",
    },
    "clean/station_feed_hourly.csv": {
        "time", "station_uid", "station_name", "aqi_tram", "lat", "lon",
    },
}
OPTIONAL_MEASUREMENT_FILES = {
    "raw/openaq_measurements.csv": 24,
    "raw/hanoi_portal_pm25.csv": 24,
    "raw/sensorcommunity_pm25.csv": 3,
}
OPTIONAL_STATION_FILES = {
    "raw/noaa_vvnb_metar.csv": 3,
}
TIME_COLUMNS = {
    "raw/aq_model_hanoi.csv": ("time",),
    "raw/weather_hanoi.csv": ("time",),
    "raw/waqi_feed.csv": ("collected_at", "obs_time"),
    "clean/fact_model_hourly.csv": ("time",),
    "clean/fact_station_hourly.csv": ("time",),
    "clean/station_feed_hourly.csv": ("time",),
}
KEY_COLUMNS = {
    "raw/aq_model_hanoi.csv": ["time"],
    "raw/weather_hanoi.csv": ["time"],
    "raw/waqi_feed.csv": ["collected_at", "station_uid"],
    "clean/fact_model_hourly.csv": ["time"],
    "clean/fact_station_hourly.csv": ["time", "station_uid"],
    "clean/station_feed_hourly.csv": ["time", "station_uid"],
}


def _status_for_age(age_hours, maximum_age):
    if pd.isna(age_hours) or age_hours < -24 or age_hours > maximum_age:
        return "WARN"
    return "OK"


def build_quality_report(project_dir=PROJECT_DIR, now=None):
    project_dir = Path(project_dir)
    now = pd.Timestamp.now(tz="Asia/Bangkok").tz_localize(None) if now is None else pd.Timestamp(now)
    rows = []

    for relative_path, required_columns in REQUIRED_COLUMNS.items():
        path = project_dir / relative_path
        row = {
            "source": relative_path,
            "entity": "all",
            "rows": 0,
            "stations": 0,
            "latest_data": pd.NaT,
            "age_hours": pd.NA,
            "duplicate_keys": pd.NA,
            "missing_pct": pd.NA,
            "status": "ERROR",
            "detail": "",
        }
        if not path.is_file():
            row["detail"] = "Missing input file"
            rows.append(row)
            continue

        try:
            data = pd.read_csv(path)
        except (OSError, pd.errors.ParserError, UnicodeError) as error:
            row["detail"] = f"Could not read CSV: {type(error).__name__}"
            rows.append(row)
            continue

        missing_columns = sorted(required_columns.difference(data.columns))
        if missing_columns:
            row["detail"] = f"Missing required columns: {', '.join(missing_columns)}"
            rows.append(row)
            continue
        if relative_path in {
            "raw/waqi_feed.csv",
            "clean/fact_station_hourly.csv",
            "clean/station_feed_hourly.csv",
        }:
            data = filter_hanoi_waqi_data(
                data, "lat", "lon", "station_name"
            )
        row["rows"] = len(data)
        if data.empty:
            row["detail"] = "No data rows"
            rows.append(row)
            continue

        time_columns = TIME_COLUMNS[relative_path]
        parsed_times = {
            column: pd.to_datetime(data[column], errors="coerce")
            for column in time_columns
        }
        key_columns = KEY_COLUMNS[relative_path]
        duplicate_mask = data.duplicated(key_columns, keep=False)
        row["duplicate_keys"] = int(duplicate_mask.sum())
        row["missing_pct"] = round(float(data.isna().mean().mean() * 100), 2)
        if "station_uid" in data.columns:
            row["stations"] = int(data["station_uid"].nunique(dropna=True))

        if relative_path == "raw/waqi_feed.csv":
            observations = parsed_times["obs_time"]
            valid = (
                pd.to_numeric(data["aqi"], errors="coerce").between(0, 500)
                & pd.to_numeric(data["age_min"], errors="coerce").between(-10, 180)
                & observations.notna()
            )
            latest_time = observations[valid].max()
        else:
            latest_time = parsed_times[time_columns[0]].max()

        row["latest_data"] = latest_time
        row["age_hours"] = (
            round((now - latest_time).total_seconds() / 3600, 2)
            if pd.notna(latest_time) else pd.NA
        )
        row["status"] = _status_for_age(
            row["age_hours"], MAX_AGE_HOURS[relative_path]
        )
        details = []
        invalid_time_count = int(parsed_times[time_columns[0]].isna().sum())
        if invalid_time_count:
            details.append(f"{invalid_time_count} invalid timestamp(s)")
            row["status"] = "WARN"
        if row["duplicate_keys"]:
            details.append(f"{row['duplicate_keys']} duplicate key row(s)")
            row["status"] = "WARN"
        if row["status"] == "WARN":
            details.append("Latest data is outside the freshness window")
        if relative_path == "raw/waqi_feed.csv" and row["stations"] < 2:
            details.append("Fewer than 2 stations in feed")
            row["status"] = "WARN"
        row["detail"] = "; ".join(details) if details else "Fresh"
        rows.append(row)

        if "station_uid" in data.columns and {"age_min", "aqi"}.issubset(data.columns):
            age_minutes = pd.to_numeric(data["age_min"], errors="coerce")
            aqi = pd.to_numeric(data["aqi"], errors="coerce")
            usable = aqi.between(0, 500) & age_minutes.between(-10, 180)
            for uid, indices in data.groupby("station_uid", dropna=True).groups.items():
                station_rows = data.loc[indices]
                station_usable = usable.loc[indices]
                latest_station_time = parsed_times["obs_time"].loc[indices][station_usable].max()
                station_age = (
                    round((now - latest_station_time).total_seconds() / 3600, 2)
                    if pd.notna(latest_station_time) else pd.NA
                )
                duplicate_station_rows = int(
                    station_rows.duplicated(
                        ["collected_at", "station_uid"], keep=False
                    ).sum()
                )
                status = _status_for_age(
                    station_age, MAX_AGE_HOURS["raw/waqi_feed.csv"]
                )
                if duplicate_station_rows:
                    status = "WARN"
                rows.append({
                    "source": relative_path,
                    "entity": f"station:{uid}",
                    "rows": len(station_rows),
                    "stations": 1,
                    "latest_data": latest_station_time,
                    "age_hours": station_age,
                    "duplicate_keys": duplicate_station_rows,
                    "missing_pct": round(float(station_rows.isna().mean().mean() * 100), 2),
                    "status": status,
                    "detail": "; ".join(filter(None, [
                        (
                            "No usable AQI observation within the freshness window"
                            if _status_for_age(
                                station_age, MAX_AGE_HOURS["raw/waqi_feed.csv"]
                            ) == "WARN" else ""
                        ),
                        (
                            f"{duplicate_station_rows} duplicate key row(s)"
                            if duplicate_station_rows else ""
                        ),
                    ])) or "Fresh",
                })

    for relative_path, maximum_age in OPTIONAL_MEASUREMENT_FILES.items():
        path = project_dir / relative_path
        if not path.is_file():
            continue
        row = {
            "source": relative_path,
            "entity": "all",
            "rows": 0,
            "stations": 0,
            "latest_data": pd.NaT,
            "age_hours": pd.NA,
            "duplicate_keys": pd.NA,
            "missing_pct": pd.NA,
            "status": "ERROR",
            "detail": "",
        }
        try:
            data = pd.read_csv(path)
        except (OSError, pd.errors.ParserError, UnicodeError) as error:
            row["detail"] = (
                f"Could not read CSV: {type(error).__name__}"
            )
            rows.append(row)
            continue
        required = {
            "location_id", "parameter", "value", "unit", "observed_at",
        }
        missing_columns = sorted(required.difference(data.columns))
        if missing_columns:
            row["detail"] = (
                f"Missing required columns: {', '.join(missing_columns)}"
            )
            rows.append(row)
            continue
        row["rows"] = len(data)
        if data.empty:
            row["status"] = "WARN"
            row["detail"] = "No data rows"
            rows.append(row)
            continue
        observed_at = pd.to_datetime(data["observed_at"], errors="coerce")
        latest_time = observed_at.max()
        row["latest_data"] = latest_time
        row["age_hours"] = (
            round((now - latest_time).total_seconds() / 3600, 2)
            if pd.notna(latest_time) else pd.NA
        )
        row["stations"] = int(data["location_id"].nunique(dropna=True))
        duplicate_keys = [
            column for column in (
                "source", "location_id", "parameter", "observed_at"
            ) if column in data
        ]
        row["duplicate_keys"] = int(
            data.duplicated(duplicate_keys, keep=False).sum()
        )
        row["missing_pct"] = round(
            float(data.isna().mean().mean() * 100), 2
        )
        row["status"] = _status_for_age(row["age_hours"], maximum_age)
        details = []
        if observed_at.isna().any():
            details.append(
                f"{int(observed_at.isna().sum())} invalid timestamp(s)"
            )
            row["status"] = "WARN"
        if row["duplicate_keys"]:
            details.append(f"{row['duplicate_keys']} duplicate key row(s)")
            row["status"] = "WARN"
        if row["status"] == "WARN" and not details:
            details.append("Latest data is outside the freshness window")
        row["detail"] = "; ".join(details) if details else "Fresh"
        rows.append(row)

    for relative_path, maximum_age in OPTIONAL_STATION_FILES.items():
        path = project_dir / relative_path
        if not path.is_file():
            continue
        row = {
            "source": relative_path,
            "entity": "all",
            "rows": 0,
            "stations": 0,
            "latest_data": pd.NaT,
            "age_hours": pd.NA,
            "duplicate_keys": pd.NA,
            "missing_pct": pd.NA,
            "status": "ERROR",
            "detail": "",
        }
        try:
            data = pd.read_csv(path)
        except (OSError, pd.errors.ParserError, UnicodeError) as error:
            row["detail"] = (
                f"Could not read CSV: {type(error).__name__}"
            )
            rows.append(row)
            continue
        required = {"station_id", "observed_at"}
        missing_columns = sorted(required.difference(data.columns))
        if missing_columns:
            row["detail"] = (
                f"Missing required columns: {', '.join(missing_columns)}"
            )
            rows.append(row)
            continue
        row["rows"] = len(data)
        if data.empty:
            row["status"] = "WARN"
            row["detail"] = "No data rows"
            rows.append(row)
            continue
        observed_at = pd.to_datetime(data["observed_at"], errors="coerce")
        latest_time = observed_at.max()
        row["latest_data"] = latest_time
        row["age_hours"] = (
            round((now - latest_time).total_seconds() / 3600, 2)
            if pd.notna(latest_time) else pd.NA
        )
        row["stations"] = int(data["station_id"].nunique(dropna=True))
        row["duplicate_keys"] = int(
            data.duplicated(["station_id", "observed_at"], keep=False).sum()
        )
        row["missing_pct"] = round(
            float(data.isna().mean().mean() * 100), 2
        )
        row["status"] = _status_for_age(row["age_hours"], maximum_age)
        details = []
        if observed_at.isna().any():
            details.append(
                f"{int(observed_at.isna().sum())} invalid timestamp(s)"
            )
            row["status"] = "WARN"
        if row["duplicate_keys"]:
            details.append(f"{row['duplicate_keys']} duplicate key row(s)")
            row["status"] = "WARN"
        if row["status"] == "WARN" and not details:
            details.append("Latest data is outside the freshness window")
        row["detail"] = "; ".join(details) if details else "Fresh"
        rows.append(row)

    return pd.DataFrame(rows)


def main():
    report = build_quality_report()
    output = PROJECT_DIR / "figures" / "data_quality_report.csv"
    output.parent.mkdir(parents=True, exist_ok=True)
    report.to_csv(output, index=False, encoding="utf-8")
    print(report.to_string(index=False))
    print(f"\nĐã lưu báo cáo chất lượng dữ liệu: {output.relative_to(PROJECT_DIR)}")
    if (report["status"] == "ERROR").any():
        raise SystemExit(1)
    if (report["status"] == "WARN").any():
        print("Cảnh báo: hãy xem các dòng WARN trước khi diễn giải kết quả.")


if __name__ == "__main__":
    main()
