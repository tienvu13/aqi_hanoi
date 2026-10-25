from datetime import datetime

import pandas as pd

from aqi_hanoi.core.settings import PROJECT_DIR, get_waqi_token
from aqi_hanoi.core.waqi_client import fetch_json
from aqi_hanoi.core.waqi_stations import HANOI_WAQI_BOUNDS, filter_hanoi_waqi_data


def main():
    token = get_waqi_token()
    result = fetch_json(
        "https://api.waqi.info/map/bounds/",
        params={"latlng": HANOI_WAQI_BOUNDS, "token": token},
        timeout=30,
    )
    if result.get("status") != "ok":
        raise RuntimeError(f"WAQI không trả về danh sách trạm hợp lệ: {result.get('data')}")

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows = [{
        "collected_at": now,
        "station_uid": station["uid"],
        "station_name": station["station"]["name"],
        "lat": station["lat"],
        "lon": station["lon"],
        "aqi": station["aqi"],
    } for station in result.get("data", [])]
    if not rows:
        raise RuntimeError("WAQI không trả về trạm nào trong khu vực đã chọn.")
    data = filter_hanoi_waqi_data(
        pd.DataFrame(rows), "lat", "lon", "station_name"
    )
    if data.empty:
        raise RuntimeError("WAQI không trả về trạm nào trong khu vực đã chọn.")

    output = PROJECT_DIR / "raw" / "aqi_hanoi.core.waqi_stations.csv"
    output.parent.mkdir(parents=True, exist_ok=True)
    data.to_csv(output, mode="a", header=not output.exists(), index=False, encoding="utf-8")
    print(f"Đã ghi {len(data)} trạm lúc {now}")


if __name__ == "__main__":
    main()