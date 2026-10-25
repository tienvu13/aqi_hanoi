from pathlib import Path

import pandas as pd


def _local_time(value):
    current = (
        pd.Timestamp.now(tz="Asia/Bangkok").tz_localize(None)
        if value is None else pd.Timestamp(value)
    )
    if current.tzinfo is not None:
        current = current.tz_convert("Asia/Bangkok").tz_localize(None)
    return current


def _summarize_missing_hours(observed_hours, expected_hours):
    missing = expected_hours.difference(observed_hours).sort_values()
    if missing.empty:
        return 0, 0
    gap_ids = missing.to_series().diff().ne(pd.Timedelta(hours=1)).cumsum()
    gap_sizes = missing.to_series().groupby(gap_ids).size()
    return len(gap_sizes), int(gap_sizes.max())


def build_hourly_coverage_report(
    model, stations=None, days=7, now=None, tracked_stations=None
):
    """Report complete-hour coverage and consecutive gaps for CAMS and WAQI."""
    if days <= 0:
        raise ValueError("days phải lớn hơn 0.")
    if missing := {"time", "aqi"}.difference(model.columns):
        raise ValueError(
            f"Thiếu cột AQI mô hình: {', '.join(sorted(missing))}"
        )
    current = _local_time(now)
    last_complete_hour = current.floor("h") - pd.Timedelta(hours=1)
    expected_hours = pd.date_range(
        end=last_complete_hour,
        periods=days * 24,
        freq="h",
    )
    rows = []

    model_data = model[["time", "aqi"]].copy()
    model_data["time"] = pd.to_datetime(model_data["time"], errors="coerce")
    model_data["aqi"] = pd.to_numeric(model_data["aqi"], errors="coerce")
    model_data = model_data.dropna(subset=["time", "aqi"])
    model_hours = pd.DatetimeIndex(
        model_data["time"].dt.floor("h").drop_duplicates()
    ).intersection(expected_hours)
    gap_count, longest_gap = _summarize_missing_hours(
        model_hours, expected_hours
    )
    rows.append({
        "source": "CAMS (mô hình)",
        "station_uid": "",
        "expected_hours": len(expected_hours),
        "observed_hours": len(model_hours),
        "missing_hours": len(expected_hours) - len(model_hours),
        "coverage_pct": round(len(model_hours) / len(expected_hours) * 100, 1),
        "gap_count": gap_count,
        "longest_gap_hours": longest_gap,
        "latest_observation": model_hours.max() if len(model_hours) else pd.NaT,
    })

    if stations is not None:
        required = {"time", "station_uid", "aqi_tram"}
        if missing := required.difference(stations.columns):
            raise ValueError(
                f"Thiếu cột AQI trạm: {', '.join(sorted(missing))}"
            )
        station_data = stations[["time", "station_uid", "aqi_tram"]].copy()
        station_data["time"] = pd.to_datetime(
            station_data["time"], errors="coerce"
        )
        station_data["station_uid"] = station_data["station_uid"].astype("string")
        station_data["aqi_tram"] = pd.to_numeric(
            station_data["aqi_tram"], errors="coerce"
        )
        if "station_name" in stations:
            station_data["station_name"] = stations["station_name"]
        station_data = station_data.dropna(
            subset=["time", "station_uid", "aqi_tram"]
        )
        station_data = station_data.loc[
            station_data["aqi_tram"].between(0, 500)
        ]
        names = {}
        for uid, group in station_data.groupby("station_uid", sort=True):
            name_values = group.get("station_name", pd.Series(dtype="string"))
            name_values = name_values.dropna().astype(str)
            names[uid] = (
                name_values.iloc[-1] if not name_values.empty else f"Trạm {uid}"
            )
        if tracked_stations is not None:
            tracked_required = {"station_uid", "station_name"}
            if missing := tracked_required.difference(tracked_stations.columns):
                raise ValueError(
                    "Thiếu cột danh sách trạm: "
                    f"{', '.join(sorted(missing))}"
                )
            for _, tracked in tracked_stations.iterrows():
                uid = str(tracked["station_uid"])
                if pd.isna(tracked["station_uid"]):
                    continue
                tracked_name = (
                    str(tracked["station_name"])
                    if pd.notna(tracked["station_name"])
                    else f"Trạm {uid}"
                )
                names.setdefault(uid, tracked_name)

        grouped_stations = {
            uid: group
            for uid, group in station_data.groupby("station_uid", sort=True)
        }
        for uid in sorted(names):
            group = grouped_stations.get(uid)
            observed = (
                pd.DatetimeIndex(
                    group["time"].dt.floor("h").drop_duplicates()
                ).intersection(expected_hours)
                if group is not None
                else pd.DatetimeIndex([])
            )
            gap_count, longest_gap = _summarize_missing_hours(
                observed, expected_hours
            )
            rows.append({
                "source": f"WAQI · {names[uid]}",
                "station_uid": uid,
                "expected_hours": len(expected_hours),
                "observed_hours": len(observed),
                "missing_hours": len(expected_hours) - len(observed),
                "coverage_pct": round(
                    len(observed) / len(expected_hours) * 100, 1
                ),
                "gap_count": gap_count,
                "longest_gap_hours": longest_gap,
                "latest_observation": (
                    observed.max() if len(observed) else pd.NaT
                ),
            })
    return pd.DataFrame(rows)


def build_daily_aqi_summary(model, days=30, now=None):
    """Summarize historical hourly model AQI by local calendar day."""
    required = {"time", "aqi"}
    if missing := required.difference(model.columns):
        raise ValueError(
            f"Thiếu cột AQI mô hình: {', '.join(sorted(missing))}"
        )
    if days <= 0:
        raise ValueError("days phải lớn hơn 0.")

    current = _local_time(now)

    data = model[["time", "aqi"]].copy()
    data["time"] = pd.to_datetime(data["time"], errors="coerce")
    data["aqi"] = pd.to_numeric(data["aqi"], errors="coerce")
    data = data.dropna(subset=["time", "aqi"])
    data = data.loc[
        data["aqi"].between(0, 500)
        & data["time"].le(current)
        & data["time"].ge(current - pd.Timedelta(days=days))
    ]
    if data.empty:
        return pd.DataFrame(columns=[
            "date", "aqi_mean", "aqi_max", "hours_available",
            "hours_above_100", "hours_above_150", "hours_above_200",
            "coverage_pct",
        ])

    data["time"] = data["time"].dt.floor("h")
    hourly = (
        data.groupby("time", as_index=False)
        .agg(aqi=("aqi", "mean"))
    )
    hourly["date"] = hourly["time"].dt.normalize()
    daily = (
        hourly.groupby("date", as_index=False)
        .agg(
            aqi_mean=("aqi", "mean"),
            aqi_max=("aqi", "max"),
            hours_available=("aqi", "size"),
            hours_above_100=("aqi", lambda values: int(values.gt(100).sum())),
            hours_above_150=("aqi", lambda values: int(values.gt(150).sum())),
            hours_above_200=("aqi", lambda values: int(values.gt(200).sum())),
        )
        .sort_values("date")
        .reset_index(drop=True)
    )
    daily["coverage_pct"] = (daily["hours_available"] / 24 * 100).round(1)
    return daily


def load_daily_aqi_summary(path, days=30, now=None):
    """Load the project model time series and summarize it by day."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Không tìm thấy dữ liệu AQI mô hình: {path}")
    model = pd.read_csv(path, usecols=["time", "aqi"])
    return build_daily_aqi_summary(model, days=days, now=now)
