from math import asin, cos, radians, sin, sqrt

import pandas as pd


HANOI_MIN_LAT = 20.55
HANOI_MAX_LAT = 21.65
HANOI_MIN_LON = 105.28
HANOI_MAX_LON = 106.05
HANOI_WAQI_BOUNDS = (
    f"{HANOI_MIN_LAT},{HANOI_MIN_LON},"
    f"{HANOI_MAX_LAT},{HANOI_MAX_LON}"
)
HANOI_OPENAQ_BBOX = (
    f"{HANOI_MIN_LON},{HANOI_MIN_LAT},"
    f"{HANOI_MAX_LON},{HANOI_MAX_LAT}"
)
HANOI_CENTER = (21.0285, 105.8542)
TARGET_STATIONS = 10
MAX_STATION_CANDIDATES = 60


def _distance_km(latitude, longitude):
    center_latitude, center_longitude = map(radians, HANOI_CENTER)
    latitude, longitude = radians(latitude), radians(longitude)
    delta_latitude = latitude - center_latitude
    delta_longitude = longitude - center_longitude
    haversine = (
        sin(delta_latitude / 2) ** 2
        + cos(center_latitude) * cos(latitude) * sin(delta_longitude / 2) ** 2
    )
    return 6371 * 2 * asin(sqrt(haversine))


def filter_hanoi_coordinates(data, latitude_column, longitude_column):
    required = {latitude_column, longitude_column}
    if missing := required.difference(data.columns):
        raise ValueError(
            f"Thiếu cột tọa độ Hà Nội: {', '.join(sorted(missing))}"
        )
    latitude = pd.to_numeric(data[latitude_column], errors="coerce")
    longitude = pd.to_numeric(data[longitude_column], errors="coerce")
    in_hanoi = (
        latitude.between(HANOI_MIN_LAT, HANOI_MAX_LAT)
        & longitude.between(HANOI_MIN_LON, HANOI_MAX_LON)
    )
    return data.loc[in_hanoi].copy()


def filter_hanoi_waqi_data(data, latitude_column, longitude_column, name_column):
    if name_column not in data:
        raise ValueError(f"Thiếu cột tên trạm WAQI: {name_column}")
    in_hanoi = data[name_column].astype("string").str.contains(
        r"hanoi|hà nội", case=False, na=False, regex=True
    )
    return filter_hanoi_coordinates(
        data.loc[in_hanoi], latitude_column, longitude_column
    )


def select_station_uids(
    stations,
    max_stations=MAX_STATION_CANDIDATES,
):
    """Select nearest in-range stations with a numeric WAQI AQI."""
    if max_stations <= 0:
        raise ValueError("max_stations phải lớn hơn 0.")

    candidates = {}
    for station in stations:
        try:
            uid = int(station["uid"])
            latitude = float(station["lat"])
            longitude = float(station["lon"])
            aqi = float(station["aqi"])
            station_name = str((station.get("station") or {}).get("name", ""))
        except (AttributeError, KeyError, TypeError, ValueError):
            continue
        if (
            not 0 <= aqi <= 500
            or not HANOI_MIN_LAT <= latitude <= HANOI_MAX_LAT
            or not HANOI_MIN_LON <= longitude <= HANOI_MAX_LON
            or not any(
                name in station_name.casefold() for name in ("hanoi", "hà nội")
            )
        ):
            continue
        distance = _distance_km(latitude, longitude)
        candidates[uid] = min(distance, candidates.get(uid, distance))

    return sorted(candidates, key=lambda uid: (candidates[uid], uid))[
        :max_stations
    ]
