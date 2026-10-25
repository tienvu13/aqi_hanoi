import pandas as pd

from aqi_hanoi.core.http_utils import get_with_retry
from aqi_hanoi.core.settings import PROJECT_DIR
from aqi_hanoi.core.source_measurements import save_measurement_history
from aqi_hanoi.core.waqi_stations import (
    HANOI_CENTER,
    HANOI_MAX_LAT,
    HANOI_MAX_LON,
    HANOI_MIN_LAT,
    HANOI_MIN_LON,
)


BASE_URL = "https://data.sensor.community/airrohr/v1/filter/area"
SEARCH_RADIUS_KM = 25
USER_AGENT = "aqi-hanoi/1.0 (Hanoi air-quality research)"
OUTPUT_PATH = PROJECT_DIR / "raw" / "sensorcommunity_pm25.csv"
MAX_PM25_UG_M3 = 1000


def fetch_measurements():
    latitude, longitude = HANOI_CENTER
    response = get_with_retry(
        f"{BASE_URL}={latitude},{longitude},{SEARCH_RADIUS_KM}",
        timeout=30,
        headers={"User-Agent": USER_AGENT},
    )
    try:
        payload = response.json()
    except ValueError as error:
        raise RuntimeError(
            "Sensor.Community trả về JSON không hợp lệ."
        ) from error
    if not isinstance(payload, list):
        raise RuntimeError(
            "Sensor.Community trả về danh sách cảm biến sai dạng."
        )
    return payload


def parse_pm25_measurements(payload, collected_at):
    if not isinstance(payload, list):
        raise ValueError("Dữ liệu cảm biến phải là một danh sách.")
    rows = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        location = item.get("location") or {}
        sensor = item.get("sensor") or {}
        sensor_type = sensor.get("sensor_type") or {}
        if not isinstance(location, dict) or not isinstance(sensor_type, dict):
            continue
        if location.get("country") != "VN" or location.get("indoor") != 0:
            continue
        try:
            latitude = float(location["latitude"])
            longitude = float(location["longitude"])
            sensor_id = int(sensor["id"])
        except (KeyError, TypeError, ValueError):
            continue
        if (
            not HANOI_MIN_LAT <= latitude <= HANOI_MAX_LAT
            or not HANOI_MIN_LON <= longitude <= HANOI_MAX_LON
        ):
            continue
        timestamp = pd.to_datetime(
            item.get("timestamp"), errors="coerce", utc=True
        )
        if pd.isna(timestamp):
            continue
        observed_at = (
            timestamp.tz_convert("Asia/Bangkok").tz_localize(None)
        )
        for reading in item.get("sensordatavalues") or []:
            if not isinstance(reading, dict) or reading.get("value_type") != "P2":
                continue
            value = pd.to_numeric(reading.get("value"), errors="coerce")
            if pd.isna(value) or not 0 <= value <= MAX_PM25_UG_M3:
                continue
            sensor_name = str(sensor_type.get("name") or "Unknown sensor")
            rows.append({
                "collected_at": collected_at,
                "source": "Sensor.Community",
                "location_id": sensor_id,
                "location_name": f"Sensor {sensor_id} ({sensor_name})",
                "provider": "Sensor.Community",
                "latitude": latitude,
                "longitude": longitude,
                "parameter": "pm25",
                "value": float(value),
                "unit": "µg/m³",
                "observed_at": observed_at,
            })
    return rows


def collect_sensorcommunity():
    collected_at = pd.Timestamp.now(tz="Asia/Bangkok").tz_localize(None)
    rows = parse_pm25_measurements(fetch_measurements(), collected_at)
    if not rows:
        raise RuntimeError(
            "Sensor.Community không trả PM2.5 ngoài trời hợp lệ trong phạm vi Hà Nội."
        )
    saved = save_measurement_history(OUTPUT_PATH, rows)
    print(
        f"Sensor.Community: {len(rows)} phép đo mới, "
        f"{saved['location_id'].nunique()} cảm biến trong dữ liệu lưu."
    )
    return saved


def main():
    collect_sensorcommunity()


if __name__ == "__main__":
    main()
