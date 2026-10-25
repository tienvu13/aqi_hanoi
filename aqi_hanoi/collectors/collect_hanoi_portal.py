import pandas as pd

from aqi_hanoi.core.http_utils import get_with_retry
from aqi_hanoi.core.settings import PROJECT_DIR
from aqi_hanoi.core.source_measurements import save_measurement_history
from aqi_hanoi.core.waqi_stations import (
    HANOI_MAX_LAT,
    HANOI_MAX_LON,
    HANOI_MIN_LAT,
    HANOI_MIN_LON,
)


BASE_URL = "https://moitruongthudo.vn"
USER_AGENT = "aqi-hanoi/1.0 (Hanoi air-quality research)"
OUTPUT_PATH = PROJECT_DIR / "raw" / "hanoi_portal_pm25.csv"
MAX_PM25_UG_M3 = 1000


def fetch_json(path):
    response = get_with_retry(
        f"{BASE_URL}{path}",
        timeout=30,
        headers={"User-Agent": USER_AGENT},
    )
    try:
        return response.json()
    except ValueError as error:
        raise RuntimeError(
            f"Cổng quan trắc Hà Nội trả về JSON không hợp lệ tại {path}."
        ) from error


def parse_sites(payload):
    if not isinstance(payload, list):
        raise RuntimeError("Cổng quan trắc Hà Nội trả về danh sách trạm sai dạng.")
    sites = []
    for site in payload:
        if not isinstance(site, dict) or site.get("status") != 1:
            continue
        try:
            site_id = int(site["id"])
            latitude = float(site["latitude"])
            longitude = float(site["longtitude"])
        except (KeyError, TypeError, ValueError):
            continue
        if (
            HANOI_MIN_LAT <= latitude <= HANOI_MAX_LAT
            and HANOI_MIN_LON <= longitude <= HANOI_MAX_LON
        ):
            sites.append({
                "id": site_id,
                "name": str(site.get("name") or f"Trạm Hà Nội {site_id}"),
                "latitude": latitude,
                "longitude": longitude,
            })
    return sites


def parse_pm25_history(site, payload, collected_at):
    if not isinstance(payload, dict):
        raise RuntimeError(
            f"Cổng quan trắc trả lịch sử sai dạng cho trạm {site['id']}."
        )
    series = payload.get("PM2.5")
    if not isinstance(series, list):
        return []
    rows = []
    for item in series:
        if not isinstance(item, dict):
            continue
        observed_at = pd.to_datetime(item.get("time"), errors="coerce")
        value = pd.to_numeric(item.get("value"), errors="coerce")
        if (
            pd.isna(observed_at)
            or pd.isna(value)
            or not 0 <= value <= MAX_PM25_UG_M3
        ):
            continue
        rows.append({
            "collected_at": collected_at,
            "source": "Hanoi monitoring portal",
            "location_id": site["id"],
            "location_name": site["name"],
            "provider": "moitruongthudo.vn",
            "latitude": site["latitude"],
            "longitude": site["longitude"],
            "parameter": "pm25",
            "value": float(value),
            "unit": "µg/m³",
            "observed_at": observed_at,
        })
    return rows


def collect_hanoi_portal():
    collected_at = pd.Timestamp.now(tz="Asia/Bangkok").tz_localize(None)
    sites = parse_sites(fetch_json("/api/site"))
    if not sites:
        raise RuntimeError(
            "Cổng quan trắc không trả trạm hoạt động trong khu vực Hà Nội."
        )
    rows = []
    for site in sites:
        history = fetch_json(f"/public/dailystat/{site['id']}")
        rows.extend(parse_pm25_history(site, history, collected_at))
    if not rows:
        raise RuntimeError(
            "Cổng quan trắc Hà Nội không trả phép đo PM2.5 hợp lệ."
        )
    saved = save_measurement_history(OUTPUT_PATH, rows)
    print(
        f"Cổng quan trắc Hà Nội: {len(rows)} phép đo mới, "
        f"{saved['location_id'].nunique()} trạm trong dữ liệu lưu."
    )
    return saved


def main():
    collect_hanoi_portal()


if __name__ == "__main__":
    main()
