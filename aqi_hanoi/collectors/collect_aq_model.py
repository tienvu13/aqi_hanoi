import pandas as pd
import sys

from aqi_hanoi.core.http_utils import get_with_retry
from aqi_hanoi.core.pipeline_utils import update_hourly_csv
from aqi_hanoi.core.settings import PROJECT_DIR


def main():
    now = pd.Timestamp.now(tz="Asia/Bangkok").tz_localize(None)
    today = now.strftime("%Y-%m-%d")
    output = PROJECT_DIR / "raw" / "aq_model_hanoi.csv"
    start_date = pd.Timestamp("2024-01-01")
    if output.is_file():
        previous = pd.read_csv(output, usecols=["time"], parse_dates=["time"])
        available = previous.loc[previous["time"] <= now, "time"]
        if not available.empty:
            start_date = max(
                start_date,
                available.max().normalize() - pd.Timedelta(days=2),
            )
    params = {
        "latitude": 21.0285,
        "longitude": 105.8542,
        "start_date": start_date.strftime("%Y-%m-%d"),
        "end_date": today,
        "hourly": "pm10,pm2_5,us_aqi",
        "timezone": "Asia/Bangkok",
    }
    response = get_with_retry(
        "https://air-quality-api.open-meteo.com/v1/air-quality",
        params=params,
        timeout=60,
    )
    result = response.json()
    if result.get("error") or not isinstance(result.get("hourly"), dict):
        raise RuntimeError(f"Open-Meteo Air Quality API lỗi: {result.get('reason', result)}")

    hourly = pd.DataFrame(result["hourly"])
    if hourly.empty or "time" not in hourly:
        raise RuntimeError("Open-Meteo Air Quality API không trả dữ liệu theo giờ.")
    merged = update_hourly_csv(output, hourly)
    print(
        f"Đã cập nhật CAMS: {len(merged)} giờ | tải từ "
        f"{params['start_date']} | đến {merged.time.max()}"
    )


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    main()