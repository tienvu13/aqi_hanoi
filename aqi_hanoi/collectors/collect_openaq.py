from datetime import datetime
import time

import pandas as pd

from aqi_hanoi.core.http_utils import get_with_retry
from aqi_hanoi.core.openaq_client import fetch_json
from aqi_hanoi.core.settings import PROJECT_DIR, get_openaq_api_key
from aqi_hanoi.core.waqi_stations import (
    HANOI_MAX_LAT,
    HANOI_MAX_LON,
    HANOI_MIN_LAT,
    HANOI_MIN_LON,
    HANOI_OPENAQ_BBOX,
    TARGET_STATIONS,
    _distance_km,
)


PAGE_SIZE = 100
MAX_PAGES = 50
MAX_CANDIDATES = 60
MAX_MEASUREMENT_AGE_HOURS = 24
RAW_PATH = PROJECT_DIR / "raw" / "openaq_measurements.csv"
LATEST_PATH = PROJECT_DIR / "clean" / "openaq_latest.csv"
EXPECTED_COLUMNS = [
    "collected_at", "location_id", "location_name", "provider",
    "license_names", "latitude", "longitude", "sensor_id", "parameter",
    "value", "unit", "observed_at", "age_hours",
]


def list_locations(api_key):
    locations = []
    page = 1
    while page <= MAX_PAGES:
        payload = fetch_json(
            "/locations",
            api_key,
            params={
                "iso": "VN",
                "bbox": HANOI_OPENAQ_BBOX,
                "parameters_id": "1,2",
                "limit": PAGE_SIZE,
                "page": page,
            },
            timeout=30,
        )
        if not isinstance(payload, dict) or not isinstance(
            payload.get("results"), list
        ):
            raise RuntimeError("OpenAQ trả về danh sách location không hợp lệ.")
        results = payload["results"]
        locations.extend(results)
        meta = payload.get("meta") or {}
        found = meta.get("found")
        if not results or len(results) < PAGE_SIZE:
            break
        if isinstance(found, int) and page * PAGE_SIZE >= found:
            break
        page += 1
    else:
        raise RuntimeError(
            f"Danh sách OpenAQ vượt quá giới hạn {MAX_PAGES} trang."
        )
    return locations


def select_locations(locations):
    candidates = []
    for location in locations:
        try:
            location_id = int(location["id"])
            coordinates = location["coordinates"]
            latitude = float(coordinates["latitude"])
            longitude = float(coordinates["longitude"])
        except (KeyError, TypeError, ValueError):
            continue
        if (
            not HANOI_MIN_LAT <= latitude <= HANOI_MAX_LAT
            or not HANOI_MIN_LON <= longitude <= HANOI_MAX_LON
        ):
            continue
        locality = str(location.get("locality") or "").strip().casefold()
        if (
            locality not in {"", "n/a", "na", "none"}
            and not any(name in locality for name in ("hanoi", "hà nội"))
        ):
            continue
        if location.get("isMobile", False):
            continue
        sensors = location.get("sensors")
        if not isinstance(sensors, list):
            continue
        parameters = {
            str((sensor.get("parameter") or {}).get("name", "")).lower()
            for sensor in sensors
        }
        if not parameters.intersection({"pm25", "pm10"}):
            continue
        distance = _distance_km(latitude, longitude)
        candidates.append((distance, location_id, location))
    candidates.sort(key=lambda item: (item[0], item[1]))
    return [location for _, _, location in candidates[:MAX_CANDIDATES]]


def parse_location_latest(location, payload, collected_at):
    if not isinstance(payload, dict) or not isinstance(
        payload.get("results"), list
    ):
        raise RuntimeError(
            f"OpenAQ trả về số đo mới nhất không hợp lệ cho location {location.get('id')}."
        )
    sensors = {}
    for sensor in location.get("sensors", []):
        parameter = sensor.get("parameter") or {}
        name = str(parameter.get("name", "")).lower()
        if name not in {"pm25", "pm10"}:
            continue
        try:
            sensors[int(sensor["id"])] = (
                name,
                str(parameter.get("units", "")),
            )
        except (KeyError, TypeError, ValueError):
            continue

    coordinates = location.get("coordinates") or {}
    try:
        latitude = float(coordinates["latitude"])
        longitude = float(coordinates["longitude"])
        location_id = int(location["id"])
    except (KeyError, TypeError, ValueError):
        return []
    provider = (location.get("provider") or {}).get("name", "")
    licenses = location.get("licenses") or []
    license_names = "; ".join(
        str(license_item.get("name", ""))
        for license_item in licenses
        if license_item.get("name")
    )

    rows = []
    for reading in payload["results"]:
        try:
            sensor_id = int(reading["sensorsId"])
            parameter, unit = sensors[sensor_id]
            value = float(reading["value"])
            observed_utc = pd.to_datetime(
                (reading.get("datetime") or {}).get("utc"),
                errors="coerce",
                utc=True,
            )
        except (KeyError, TypeError, ValueError):
            continue
        if not 0 <= value <= 1000 or pd.isna(observed_utc):
            continue
        observed_at = (
            observed_utc.tz_convert("Asia/Bangkok").tz_localize(None)
        )
        age_hours = (collected_at - observed_at).total_seconds() / 3600
        if not 0 <= age_hours <= MAX_MEASUREMENT_AGE_HOURS:
            continue
        rows.append({
            "collected_at": collected_at.strftime("%Y-%m-%d %H:%M:%S"),
            "location_id": location_id,
            "location_name": str(location.get("name", "")),
            "provider": str(provider),
            "license_names": license_names,
            "latitude": latitude,
            "longitude": longitude,
            "sensor_id": sensor_id,
            "parameter": parameter,
            "value": value,
            "unit": unit,
            "observed_at": observed_at.strftime("%Y-%m-%d %H:%M:%S"),
            "age_hours": round(age_hours, 2),
        })
    return rows


def collect_openaq(api_key, now=None):
    collected_at = (
        pd.Timestamp.now(tz="Asia/Bangkok").tz_localize(None)
        if now is None else pd.Timestamp(now)
    )
    if collected_at.tzinfo is not None:
        collected_at = collected_at.tz_convert(
            "Asia/Bangkok"
        ).tz_localize(None)
    locations = select_locations(list_locations(api_key))
    if not locations:
        raise RuntimeError(
            "OpenAQ không có trạm PM2.5/PM10 trong phạm vi tọa độ Hà Nội."
        )
    print(
        f"OpenAQ: kiểm tra {len(locations)} trạm trong khu vực Hà Nội để tìm "
        f"tối đa {TARGET_STATIONS} trạm có số đo trong "
        f"{MAX_MEASUREMENT_AGE_HOURS} giờ."
    )
    rows = []
    accepted_locations = set()
    for location in locations:
        if len(accepted_locations) >= TARGET_STATIONS:
            break
        location_id = int(location["id"])
        try:
            payload = fetch_json(
                f"/locations/{location_id}/latest",
                api_key,
                params={"limit": 100},
                timeout=30,
            )
        except RuntimeError as error:
            print(f"OpenAQ location {location_id}: {error}")
            continue
        station_rows = parse_location_latest(
            location, payload, collected_at
        )
        if station_rows:
            rows.extend(station_rows)
            accepted_locations.add(location_id)
            print(
                f"OpenAQ {location_id} ({location.get('name', 'trạm')}): "
                f"{len(station_rows)} phép đo PM mới."
            )
        time.sleep(0.2)
    if not rows:
        raise RuntimeError(
            "Không tìm thấy phép đo PM2.5/PM10 mới trong dữ liệu OpenAQ."
        )
    if len(accepted_locations) < TARGET_STATIONS:
        print(
            f"Cảnh báo: OpenAQ chỉ có {len(accepted_locations)}/"
            f"{TARGET_STATIONS} trạm có số đo mới."
        )
    return pd.DataFrame(rows, columns=EXPECTED_COLUMNS)


def save_openaq_data(data):
    if data.empty:
        raise ValueError("Không thể lưu bộ dữ liệu OpenAQ rỗng.")
    missing = set(EXPECTED_COLUMNS).difference(data.columns)
    if missing:
        raise ValueError(
            f"Thiếu cột dữ liệu OpenAQ: {', '.join(sorted(missing))}"
        )
    RAW_PATH.parent.mkdir(parents=True, exist_ok=True)
    LATEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing = None
    if RAW_PATH.is_file():
        existing = pd.read_csv(RAW_PATH)
        if list(existing.columns) != EXPECTED_COLUMNS:
            raise ValueError(
                f"Schema {RAW_PATH.name} không khớp; không ghi đè lịch sử."
            )
    history = (
        pd.concat([existing, data], ignore_index=True)
        if existing is not None else data.copy()
    ).drop_duplicates(
        ["location_id", "sensor_id", "observed_at"],
        keep="last",
    ).sort_values("observed_at")
    history.to_csv(RAW_PATH, index=False, encoding="utf-8")
    data.to_csv(LATEST_PATH, index=False, encoding="utf-8")


def main():
    api_key = get_openaq_api_key()
    data = collect_openaq(api_key, now=datetime.now().astimezone())
    save_openaq_data(data)
    print(
        f"Đã lưu {data['location_id'].nunique()} trạm OpenAQ và "
        f"{len(data)} phép đo PM mới."
    )


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError) as error:
        print(f"Lỗi: {error}")
        raise SystemExit(1) from None
