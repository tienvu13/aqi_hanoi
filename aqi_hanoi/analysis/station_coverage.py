import pandas as pd


FRESHNESS_HOURS = 3
STATION_COLUMNS = {
    "station_uid",
    "station_name",
    "collected_at",
    "obs_time",
    "age_min",
    "aqi",
}


def _hanoi_now(now):
    current = pd.Timestamp.now(tz="Asia/Bangkok").tz_localize(None) if now is None else pd.Timestamp(now)
    if current.tzinfo is not None:
        current = current.tz_convert("Asia/Bangkok").tz_localize(None)
    return current


def latest_station_collection_batch(feed, require_usable_aqi=True):
    """Return only AQI-bearing stations from the latest collector run."""
    required = {"collected_at", "station_uid", "aqi"}
    if missing := required.difference(feed.columns):
        raise ValueError(
            f"Thiếu cột feed WAQI: {', '.join(sorted(missing))}"
        )
    data = feed.copy()
    data["collected_at"] = pd.to_datetime(
        data["collected_at"], errors="coerce"
    )
    data["aqi"] = pd.to_numeric(data["aqi"], errors="coerce")
    data = data.dropna(subset=["collected_at"])
    if data.empty:
        return data

    latest_collection = data["collected_at"].max()
    latest = data.loc[data["collected_at"] == latest_collection].copy()
    usable = latest["aqi"].between(0, 500)
    if require_usable_aqi:
        for column in ("age_min", "obs_time"):
            if column not in latest.columns:
                raise ValueError(f"Thiếu cột feed WAQI: {column}")
        latest["age_min"] = pd.to_numeric(latest["age_min"], errors="coerce")
        latest["obs_time"] = pd.to_datetime(
            latest["obs_time"], errors="coerce"
        )
        usable &= (
            latest["age_min"].between(-10, 180)
            & latest["obs_time"].notna()
        )
    return latest.loc[usable].copy()


def build_station_coverage(feed, now=None, max_age_hours=FRESHNESS_HOURS):
    """Summarize latest usable WAQI reading and current freshness per tracked station."""
    missing = STATION_COLUMNS.difference(feed.columns)
    if missing:
        raise ValueError(
            f"Thiếu cột feed WAQI: {', '.join(sorted(missing))}"
        )
    if max_age_hours <= 0:
        raise ValueError("max_age_hours phải lớn hơn 0.")

    current = _hanoi_now(now)
    data = feed.copy()
    data["station_uid"] = data["station_uid"].astype("string")
    data["station_name"] = data["station_name"].fillna("").astype(str)
    data["collected_at"] = pd.to_datetime(data["collected_at"], errors="coerce")
    data["obs_time"] = pd.to_datetime(data["obs_time"], errors="coerce")
    data["age_min"] = pd.to_numeric(data["age_min"], errors="coerce")
    data["aqi"] = pd.to_numeric(data["aqi"], errors="coerce")
    data = data.dropna(subset=["station_uid"])
    if data.empty:
        return pd.DataFrame(columns=[
            "station_uid", "station_name", "latest_observation",
            "latest_aqi", "age_hours", "status", "detail",
        ])

    rows = []
    for uid, station_rows in data.groupby("station_uid", sort=True):
        names = station_rows.loc[
            station_rows["station_name"].ne(""), "station_name"
        ]
        name = names.iloc[-1] if not names.empty else f"Trạm {uid}"
        usable = (
            station_rows["aqi"].between(0, 500)
            & station_rows["age_min"].between(-10, 180)
            & station_rows["obs_time"].notna()
        )
        readings = station_rows.loc[usable].sort_values("obs_time")
        if not readings.empty:
            latest = readings.iloc[-1]
            age_hours = round(
                (current - latest["obs_time"]).total_seconds() / 3600, 2
            )
            fresh = -1 <= age_hours <= max_age_hours
            rows.append({
                "station_uid": uid,
                "station_name": name,
                "latest_observation": latest["obs_time"],
                "latest_aqi": latest["aqi"],
                "age_hours": age_hours,
                "status": "FRESH" if fresh else "STALE",
                "detail": (
                    "AQI hợp lệ, số đo còn mới"
                    if fresh else
                    f"Số đo hợp lệ gần nhất đã cũ hơn {max_age_hours} giờ"
                ),
            })
            continue

        valid_aqi = station_rows.loc[
            station_rows["aqi"].between(0, 500)
            & station_rows["obs_time"].notna()
        ].sort_values("obs_time")
        if not valid_aqi.empty:
            latest = valid_aqi.iloc[-1]
            age_hours = round(
                (current - latest["obs_time"]).total_seconds() / 3600, 2
            )
            latest_aqi = latest["aqi"]
            latest_observation = latest["obs_time"]
            detail = "WAQI chỉ trả về AQI cũ; không dùng làm số đo hiện tại"
            status = "STALE"
        else:
            latest_attempt = station_rows.sort_values("collected_at").iloc[-1]
            age_hours = pd.NA
            latest_aqi = pd.NA
            latest_observation = latest_attempt["obs_time"]
            detail = "Lượt đọc gần nhất không có AQI hợp lệ"
            status = "NO_USABLE_AQI"
        rows.append({
            "station_uid": uid,
            "station_name": name,
            "latest_observation": latest_observation,
            "latest_aqi": latest_aqi,
            "age_hours": age_hours,
            "status": status,
            "detail": detail,
        })

    summary = pd.DataFrame(rows)
    summary["_status_order"] = summary["status"].map({
        "FRESH": 0,
        "STALE": 1,
        "NO_USABLE_AQI": 2,
    })
    return (
        summary.sort_values(
            ["_status_order", "station_name"], kind="stable"
        )
        .drop(columns="_status_order")
        .reset_index(drop=True)
    )


def count_overlap_hours(model_times, station_times):
    """Count distinct hourly timestamps present in both CAMS and WAQI data."""
    model = pd.to_datetime(pd.Series(model_times), errors="coerce").dropna()
    stations = pd.to_datetime(pd.Series(station_times), errors="coerce").dropna()
    model_hours = set(model.dt.floor("h"))
    station_hours = set(stations.dt.floor("h"))
    return len(model_hours & station_hours)


def summarize_station_collection(feed, now=None, window_hours=24):
    """Summarize recent collection attempts and explain unusable latest results."""
    required = {
        "station_uid", "station_name", "collected_at", "obs_time", "age_min", "aqi",
    }
    if missing := required.difference(feed.columns):
        raise ValueError(
            f"Thiếu cột lượt thu thập WAQI: {', '.join(sorted(missing))}"
        )
    if window_hours <= 0:
        raise ValueError("window_hours phải lớn hơn 0.")

    current = _hanoi_now(now)
    data = feed.copy()
    data["station_uid"] = data["station_uid"].astype("string")
    data["station_name"] = data["station_name"].fillna("").astype(str)
    data["collected_at"] = pd.to_datetime(data["collected_at"], errors="coerce")
    data["obs_time"] = pd.to_datetime(data["obs_time"], errors="coerce")
    data["age_min"] = pd.to_numeric(data["age_min"], errors="coerce")
    data["aqi"] = pd.to_numeric(data["aqi"], errors="coerce")
    data = data.dropna(subset=["station_uid"])
    data["_has_aqi"] = data["aqi"].between(0, 500)
    data["_usable"] = (
        data["_has_aqi"]
        & data["age_min"].between(-10, 180)
        & data["obs_time"].notna()
    )
    window_start = current - pd.Timedelta(hours=window_hours)
    recent = data.loc[data["collected_at"].between(
        window_start, current, inclusive="both"
    )]

    rows = []
    for station_uid, station_rows in data.groupby("station_uid", sort=True):
        recent_rows = recent.loc[recent["station_uid"] == station_uid]
        attempts = len(recent_rows)
        aqi_count = int(recent_rows["_has_aqi"].sum())
        usable_count = int(recent_rows["_usable"].sum())
        latest = station_rows.sort_values("collected_at").iloc[-1]
        latest_collection = latest["collected_at"]
        latest_obs_time = latest["obs_time"]

        if attempts == 0:
            latest_result = "NO_RECENT_COLLECTION"
        elif pd.isna(latest_collection) or latest_collection < window_start:
            latest_result = "NO_RECENT_COLLECTION"
        elif not latest["_has_aqi"]:
            latest_result = "NO_AQI"
        elif pd.isna(latest_obs_time) or pd.isna(latest["age_min"]):
            latest_result = "INVALID_OBSERVATION_TIME"
        elif latest["age_min"] < -10:
            latest_result = "FUTURE_OBSERVATION"
        elif latest["age_min"] > 180:
            latest_result = "STALE_OBSERVATION"
        else:
            latest_result = "USABLE"

        names = station_rows.loc[
            station_rows["station_name"].ne(""), "station_name"
        ]
        rows.append({
            "station_uid": station_uid,
            "station_name": (
                names.iloc[-1] if not names.empty else f"Trạm {station_uid}"
            ),
            "attempts": attempts,
            "valid_aqi_count": aqi_count,
            "usable_count": usable_count,
            "usable_pct": (
                round(usable_count / attempts * 100, 1)
                if attempts else pd.NA
            ),
            "latest_collection": latest_collection,
            "latest_observation": latest_obs_time,
            "latest_aqi": latest["aqi"] if latest["_has_aqi"] else pd.NA,
            "latest_result": latest_result,
        })
    return pd.DataFrame(rows).sort_values(
        ["attempts", "station_name"],
        ascending=[False, True],
        kind="stable",
    ).reset_index(drop=True)


def build_station_map_data(coverage, feed):
    """Attach each tracked station's latest valid coordinates to its status."""
    coverage_required = {
        "station_uid", "station_name", "latest_observation", "latest_aqi",
        "status",
    }
    feed_required = {"station_uid", "collected_at", "lat", "lon"}
    if missing := coverage_required.difference(coverage.columns):
        raise ValueError(
            f"Thiếu cột trạng thái trạm: {', '.join(sorted(missing))}"
        )
    if missing := feed_required.difference(feed.columns):
        raise ValueError(
            f"Thiếu cột tọa độ trạm: {', '.join(sorted(missing))}"
        )

    locations = feed[["station_uid", "collected_at", "lat", "lon"]].copy()
    locations["station_uid"] = locations["station_uid"].astype("string")
    locations["collected_at"] = pd.to_datetime(
        locations["collected_at"], errors="coerce"
    )
    locations["lat"] = pd.to_numeric(locations["lat"], errors="coerce")
    locations["lon"] = pd.to_numeric(locations["lon"], errors="coerce")
    locations = locations.dropna(
        subset=["station_uid", "collected_at", "lat", "lon"]
    )
    locations = locations.loc[
        locations["lat"].between(-90, 90)
        & locations["lon"].between(-180, 180)
    ]
    locations = (
        locations.sort_values("collected_at")
        .drop_duplicates("station_uid", keep="last")
        .drop(columns="collected_at")
    )

    status_data = coverage[
        [
            "station_uid", "station_name", "latest_observation",
            "latest_aqi", "status",
        ]
    ].copy()
    status_data["station_uid"] = status_data["station_uid"].astype("string")
    map_data = status_data.merge(
        locations,
        on="station_uid",
        how="inner",
        validate="one_to_one",
    )
    map_data["map_color"] = map_data["status"].map({
        "FRESH": "#2E8B57",
        "STALE": "#D18B00",
        "NO_USABLE_AQI": "#777777",
    })
    return map_data


def build_station_history(stations, station_uid, days=7, now=None):
    """Return one station's valid hourly observations in the requested window."""
    required = {"time", "station_uid", "aqi_tram"}
    if missing := required.difference(stations.columns):
        raise ValueError(
            f"Thiếu cột lịch sử trạm: {', '.join(sorted(missing))}"
        )
    if days <= 0:
        raise ValueError("days phải lớn hơn 0.")
    current = _hanoi_now(now)
    data = stations.copy()
    data["time"] = pd.to_datetime(data["time"], errors="coerce")
    data["station_uid"] = data["station_uid"].astype("string")
    data["aqi_tram"] = pd.to_numeric(data["aqi_tram"], errors="coerce")
    data = data.loc[
        data["station_uid"].eq(str(station_uid))
        & data["aqi_tram"].between(0, 500)
    ].dropna(subset=["time"])
    data["time"] = data["time"].dt.floor("h")

    value_columns = ["aqi_tram"]
    for column in ("pm25_aqi", "pm10_aqi"):
        if column in data:
            data[column] = pd.to_numeric(data[column], errors="coerce")
            data.loc[~data[column].between(0, 500), column] = pd.NA
            value_columns.append(column)
    aggregations = {column: (column, "mean") for column in value_columns}
    if "station_name" in data:
        aggregations["station_name"] = ("station_name", "last")
    hourly = (
        data.groupby("time", as_index=False)
        .agg(**aggregations)
        .sort_values("time")
    )
    start_time = current - pd.Timedelta(days=days)
    return hourly.loc[
        hourly["time"].between(start_time, current, inclusive="both")
    ].reset_index(drop=True)


def build_station_comparison_history(
    stations, station_uids, days=7, now=None
):
    """Return a sparse hourly AQI table for selected stations without filling gaps."""
    required = {"time", "station_uid", "aqi_tram"}
    if missing := required.difference(stations.columns):
        raise ValueError(
            f"Thiếu cột so sánh trạm: {', '.join(sorted(missing))}"
        )
    if days <= 0:
        raise ValueError("days phải lớn hơn 0.")
    selected_uids = [str(uid) for uid in dict.fromkeys(station_uids)]
    if not selected_uids:
        return pd.DataFrame()

    current = _hanoi_now(now)
    data = stations[["time", "station_uid", "aqi_tram"]].copy()
    if "station_name" in stations:
        data["station_name"] = stations["station_name"]
    data["time"] = pd.to_datetime(data["time"], errors="coerce")
    data["station_uid"] = data["station_uid"].astype("string")
    data["aqi_tram"] = pd.to_numeric(data["aqi_tram"], errors="coerce")
    data = data.loc[
        data["station_uid"].isin(selected_uids)
        & data["aqi_tram"].between(0, 500)
        & data["time"].le(current)
        & data["time"].ge(current - pd.Timedelta(days=days))
    ].dropna(subset=["time"])
    if data.empty:
        return pd.DataFrame()
    data["time"] = data["time"].dt.floor("h")

    names = {}
    for uid, rows in data.groupby("station_uid", sort=False):
        station_names = rows.get("station_name", pd.Series(dtype="string"))
        station_names = station_names.dropna().astype(str)
        station_name = station_names.iloc[-1] if not station_names.empty else ""
        names[uid] = f"{station_name} ({uid})" if station_name else f"Trạm {uid}"
    data["station_label"] = data["station_uid"].map(names)
    hourly = (
        data.groupby(["time", "station_label"], as_index=False)
        .agg(aqi_tram=("aqi_tram", "mean"))
    )
    return (
        hourly.pivot(index="time", columns="station_label", values="aqi_tram")
        .sort_index()
        .reindex(columns=[names[uid] for uid in selected_uids if uid in names])
    )


def build_daily_station_summary(
    stations, days=7, now=None, tracked_stations=None
):
    """Summarize daily station AQI while retaining days and stations without data."""
    required = {"time", "station_uid", "aqi_tram"}
    if missing := required.difference(stations.columns):
        raise ValueError(
            f"Thiếu cột AQI trạm: {', '.join(sorted(missing))}"
        )
    if days <= 0:
        raise ValueError("days phải lớn hơn 0.")

    current = _hanoi_now(now)
    last_complete_hour = current.floor("h") - pd.Timedelta(hours=1)
    expected_hours = pd.date_range(
        end=last_complete_hour,
        periods=days * 24,
        freq="h",
    )
    expected_by_date = pd.Series(
        1, index=expected_hours.normalize()
    ).groupby(level=0).sum()

    data = stations[["time", "station_uid", "aqi_tram"]].copy()
    if "station_name" in stations:
        data["station_name"] = stations["station_name"]
    data["time"] = pd.to_datetime(data["time"], errors="coerce")
    data["station_uid"] = data["station_uid"].astype("string")
    data["aqi_tram"] = pd.to_numeric(data["aqi_tram"], errors="coerce")
    data = data.dropna(subset=["time", "station_uid", "aqi_tram"])
    data = data.loc[data["aqi_tram"].between(0, 500)]
    data = data.loc[
        data["time"].dt.floor("h").between(
            expected_hours.min(), expected_hours.max(), inclusive="both"
        )
    ]
    data["time"] = data["time"].dt.floor("h")
    hourly = (
        data.groupby(["station_uid", "time"], as_index=False)
        .agg(aqi_tram=("aqi_tram", "mean"))
    )
    hourly["date"] = hourly["time"].dt.normalize()
    daily = (
        hourly.groupby(["station_uid", "date"], as_index=False)
        .agg(
            aqi_mean=("aqi_tram", "mean"),
            aqi_max=("aqi_tram", "max"),
            hours_available=("aqi_tram", "size"),
            hours_above_100=("aqi_tram", lambda values: int(values.gt(100).sum())),
            hours_above_150=("aqi_tram", lambda values: int(values.gt(150).sum())),
            hours_above_200=("aqi_tram", lambda values: int(values.gt(200).sum())),
        )
    )

    station_names = {}
    if "station_name" in data:
        for uid, group in data.groupby("station_uid", sort=True):
            names = group["station_name"].dropna().astype(str)
            if not names.empty:
                station_names[uid] = names.iloc[-1]
    if tracked_stations is not None:
        tracked_required = {"station_uid", "station_name"}
        if missing := tracked_required.difference(tracked_stations.columns):
            raise ValueError(
                f"Thiếu cột danh sách trạm: {', '.join(sorted(missing))}"
            )
        for _, station in tracked_stations.iterrows():
            if pd.isna(station["station_uid"]):
                continue
            uid = str(station["station_uid"])
            station_names.setdefault(
                uid,
                (
                    str(station["station_name"])
                    if pd.notna(station["station_name"])
                    else f"Trạm {uid}"
                ),
            )
    for uid in hourly["station_uid"].unique():
        station_names.setdefault(uid, f"Trạm {uid}")

    all_dates = expected_by_date.index
    rows = []
    for uid in sorted(station_names):
        station_daily = daily.loc[daily["station_uid"] == uid].set_index("date")
        for date in all_dates:
            expected_count = int(expected_by_date.loc[date])
            observed = (
                station_daily.loc[date]
                if date in station_daily.index
                else None
            )
            if isinstance(observed, pd.DataFrame):
                observed = observed.iloc[-1]
            available = int(observed["hours_available"]) if observed is not None else 0
            rows.append({
                "station_uid": uid,
                "station_name": station_names[uid],
                "date": date,
                "aqi_mean": observed["aqi_mean"] if observed is not None else pd.NA,
                "aqi_max": observed["aqi_max"] if observed is not None else pd.NA,
                "hours_available": available,
                "expected_hours": expected_count,
                "hours_missing": expected_count - available,
                "coverage_pct": round(available / expected_count * 100, 1),
                "hours_above_100": (
                    int(observed["hours_above_100"])
                    if observed is not None else 0
                ),
                "hours_above_150": (
                    int(observed["hours_above_150"])
                    if observed is not None else 0
                ),
                "hours_above_200": (
                    int(observed["hours_above_200"])
                    if observed is not None else 0
                ),
            })
    return pd.DataFrame(rows)


def build_hourly_comparison(model, stations):
    """Join hourly CAMS values with the mean of available WAQI station readings."""
    model_required = {"time", "aqi"}
    station_required = {"time", "station_uid", "aqi_tram"}
    if missing := model_required.difference(model.columns):
        raise ValueError(
            f"Thiếu cột CAMS: {', '.join(sorted(missing))}"
        )
    if missing := station_required.difference(stations.columns):
        raise ValueError(
            f"Thiếu cột WAQI: {', '.join(sorted(missing))}"
        )

    model_data = model[["time", "aqi"]].copy()
    model_data["time"] = pd.to_datetime(model_data["time"], errors="coerce")
    model_data["aqi"] = pd.to_numeric(model_data["aqi"], errors="coerce")
    model_data = model_data.dropna(subset=["time", "aqi"])
    model_data["time"] = model_data["time"].dt.floor("h")
    model_hourly = (
        model_data.groupby("time", as_index=False)
        .agg(aqi_cams=("aqi", "mean"))
    )

    station_data = stations[["time", "station_uid", "aqi_tram"]].copy()
    station_data["time"] = pd.to_datetime(
        station_data["time"], errors="coerce"
    )
    station_data["aqi_tram"] = pd.to_numeric(
        station_data["aqi_tram"], errors="coerce"
    )
    station_data = station_data.dropna(
        subset=["time", "station_uid", "aqi_tram"]
    )
    station_data["time"] = station_data["time"].dt.floor("h")
    station_hourly = (
        station_data.groupby("time", as_index=False)
        .agg(
            aqi_waqi_mean=("aqi_tram", "mean"),
            station_count=("station_uid", "nunique"),
        )
    )
    return (
        model_hourly.merge(
            station_hourly,
            on="time",
            how="inner",
            validate="one_to_one",
        )
        .sort_values("time")
        .reset_index(drop=True)
    )
