from pathlib import Path
import sys

import pandas as pd

from aqi_hanoi.core.settings import PROJECT_DIR


HISTORY_COLUMNS = [
    "issue_key",
    "generated_at",
    "source_time",
    "target_time",
    "horizon_hours",
    "status",
    "probability_aqi_above_150",
    "probability_threshold",
    "model",
    "source_age_hours",
    "model_source_aqi",
    "model_source_actual",
    "model_prediction_correct",
    "station_mean_aqi",
    "station_count",
    "station_actual",
    "station_prediction_correct",
    "verification_status",
]


def _local_time(value):
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is not None:
        timestamp = timestamp.tz_convert("Asia/Bangkok").tz_localize(None)
    return timestamp


def _read_csv(path, required_columns):
    if not path.is_file():
        raise FileNotFoundError(f"Thiếu tệp cần thiết: {path}")
    frame = pd.read_csv(path)
    missing = set(required_columns).difference(frame.columns)
    if missing:
        raise ValueError(
            f"{path.name} thiếu cột: {', '.join(sorted(missing))}"
        )
    return frame


def _empty_history():
    return pd.DataFrame(columns=HISTORY_COLUMNS)


def record_and_resolve_forecasts(project_dir=PROJECT_DIR, now=None):
    project_dir = Path(project_dir)
    current = _local_time(pd.Timestamp.now(tz="Asia/Bangkok") if now is None else now)

    alerts = _read_csv(
        project_dir / "figures" / "local_alerts.csv",
        {
            "generated_at",
            "source_time",
            "target_time",
            "horizon_hours",
            "status",
            "probability_aqi_above_150",
            "probability_threshold",
            "model",
            "source_age_hours",
        },
    )
    model = _read_csv(
        project_dir / "clean" / "fact_model_hourly.csv",
        {"time", "aqi"},
    )
    station_path = project_dir / "clean" / "station_feed_hourly.csv"
    if station_path.is_file():
        stations = _read_csv(
            station_path,
            {"time", "station_uid", "aqi_tram"},
        )
    else:
        stations = pd.DataFrame(columns=["time", "station_uid", "aqi_tram"])

    model["time"] = pd.to_datetime(model["time"], errors="coerce")
    model["aqi"] = pd.to_numeric(model["aqi"], errors="coerce")
    model = model.dropna(subset=["time", "aqi"]).drop_duplicates(
        "time", keep="last"
    ).set_index("time")["aqi"]

    if not stations.empty:
        stations["time"] = pd.to_datetime(stations["time"], errors="coerce")
        stations["aqi_tram"] = pd.to_numeric(stations["aqi_tram"], errors="coerce")
        stations["station_uid"] = stations["station_uid"].astype("string")
        stations = stations.dropna(subset=["time", "station_uid", "aqi_tram"])
        station_hourly = (
            stations.groupby("time")
            .agg(
                station_mean_aqi=("aqi_tram", "mean"),
                station_count=("station_uid", "nunique"),
            )
        )
    else:
        station_hourly = pd.DataFrame(
            columns=["station_mean_aqi", "station_count"]
        )

    history_path = project_dir / "figures" / "forecast_history.csv"
    if history_path.is_file():
        history = _read_csv(history_path, set(HISTORY_COLUMNS))
        history = history[HISTORY_COLUMNS]
    else:
        history = _empty_history()

    by_key = {
        str(row["issue_key"]): row.to_dict()
        for _, row in history.iterrows()
    }
    added = 0
    eligible_alerts = alerts.loc[
        alerts["status"].isin(["ALERT", "NORMAL"])
    ]
    for alert in eligible_alerts.itertuples(index=False):
        source_time = pd.to_datetime(alert.source_time, errors="coerce")
        target_time = pd.to_datetime(alert.target_time, errors="coerce")
        if pd.isna(source_time) or pd.isna(target_time):
            continue
        if float(alert.source_age_hours) < -24 or float(alert.source_age_hours) > 6:
            continue
        horizon = int(alert.horizon_hours)
        issue_key = f"{horizon}|{source_time:%Y-%m-%d %H:%M:%S}"
        if issue_key in by_key:
            continue
        by_key[issue_key] = {
            "issue_key": issue_key,
            "generated_at": alert.generated_at,
            "source_time": source_time,
            "target_time": target_time,
            "horizon_hours": horizon,
            "status": alert.status,
            "probability_aqi_above_150": alert.probability_aqi_above_150,
            "probability_threshold": alert.probability_threshold,
            "model": alert.model,
            "source_age_hours": alert.source_age_hours,
            "verification_status": "PENDING",
        }
        added += 1

    records = pd.DataFrame(by_key.values(), columns=HISTORY_COLUMNS)
    if not records.empty:
        records["target_time"] = pd.to_datetime(
            records["target_time"], errors="coerce"
        )
        records["horizon_hours"] = pd.to_numeric(
            records["horizon_hours"], errors="coerce"
        )
        records["model_source_aqi"] = pd.to_numeric(
            records["model_source_aqi"], errors="coerce"
        )
        records["station_mean_aqi"] = pd.to_numeric(
            records["station_mean_aqi"], errors="coerce"
        )
        records["station_count"] = pd.to_numeric(
            records["station_count"], errors="coerce"
        )
        for index, row in records.iterrows():
            target_time = row["target_time"]
            if pd.isna(target_time) or target_time > current:
                records.at[index, "verification_status"] = "PENDING"
                continue
            if (
                target_time in model.index
                and pd.isna(row["model_source_aqi"])
            ):
                actual = float(model.loc[target_time])
                predicted = float(row["probability_aqi_above_150"]) >= float(
                    row["probability_threshold"]
                )
                records.at[index, "model_source_aqi"] = actual
                records.at[index, "model_source_actual"] = int(actual > 150)
                records.at[index, "model_prediction_correct"] = int(
                    predicted == (actual > 150)
                )
            if (
                target_time in station_hourly.index
                and pd.isna(row["station_mean_aqi"])
            ):
                station_actual = station_hourly.loc[target_time]
                station_mean = float(station_actual["station_mean_aqi"])
                station_count = int(station_actual["station_count"])
                predicted = float(row["probability_aqi_above_150"]) >= float(
                    row["probability_threshold"]
                )
                records.at[index, "station_mean_aqi"] = station_mean
                records.at[index, "station_count"] = station_count
                records.at[index, "station_actual"] = int(station_mean > 150)
                records.at[index, "station_prediction_correct"] = int(
                    predicted == (station_mean > 150)
                )
            if target_time in model.index:
                records.at[index, "verification_status"] = "VERIFIED_MODEL"
            elif target_time < current:
                records.at[index, "verification_status"] = "NO_MODEL_DATA"
            else:
                records.at[index, "verification_status"] = "PENDING"

    output = project_dir / "figures" / "forecast_history.csv"
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".tmp")
    records.to_csv(temporary, index=False, encoding="utf-8")
    temporary.replace(output)
    resolved = int(records["verification_status"].eq("VERIFIED_MODEL").sum()) if not records.empty else 0
    return {
        "path": output,
        "recorded": added,
        "total": len(records),
        "verified_model": resolved,
        "pending": int(records["verification_status"].eq("PENDING").sum()) if not records.empty else 0,
    }


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    result = record_and_resolve_forecasts()
    print(
        f"Đã lưu {result['recorded']} dự báo mới; "
        f"{result['verified_model']} dự báo đã đối chiếu CAMS, "
        f"{result['pending']} đang chờ. Tệp: "
        f"{result['path'].relative_to(PROJECT_DIR)}"
    )


if __name__ == "__main__":
    try:
        main()
    except (FileNotFoundError, ValueError, OSError) as error:
        print(f"Lỗi: {error}")
        raise SystemExit(1) from None
