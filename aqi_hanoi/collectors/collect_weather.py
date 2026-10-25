import pandas as pd
import sys

from aqi_hanoi.core.http_utils import get_with_retry
from aqi_hanoi.core.pipeline_utils import update_hourly_csv
from aqi_hanoi.core.settings import PROJECT_DIR


HOURLY_FIELDS = (
    "temperature_2m,relative_humidity_2m,precipitation,"
    "wind_speed_10m,wind_direction_10m,surface_pressure"
)


def fetch_hourly(url, params, source):
    response = get_with_retry(url, params=params, timeout=60)
    result = response.json()
    if result.get("error") or not isinstance(result.get("hourly"), dict):
        raise RuntimeError(
            f"Open-Meteo {source} API lỗi: {result.get('reason', result)}"
        )
    hourly = pd.DataFrame(result["hourly"])
    if hourly.empty or "time" not in hourly:
        raise RuntimeError(f"Open-Meteo {source} API không trả dữ liệu theo giờ.")
    return hourly


def main():
    now = pd.Timestamp.now(tz="Asia/Bangkok").tz_localize(None)
    end_date = (pd.Timestamp.now(tz="Asia/Bangkok").normalize()
                - pd.Timedelta(days=5)).strftime("%Y-%m-%d")
    archive_end = pd.Timestamp(end_date) + pd.Timedelta(hours=23)
    output = PROJECT_DIR / "raw" / "weather_hanoi.csv"
    start_date = pd.Timestamp("2024-01-01")
    if output.is_file():
        previous = pd.read_csv(output, usecols=["time"], parse_dates=["time"])
        available = previous.loc[
            previous["time"] <= archive_end, "time"
        ]
        if not available.empty:
            start_date = max(
                start_date,
                available.max().normalize() - pd.Timedelta(days=2),
            )
    archive_params = {
        "latitude": 21.0285,
        "longitude": 105.8542,
        "start_date": start_date.strftime("%Y-%m-%d"),
        "end_date": end_date,
        "hourly": HOURLY_FIELDS,
        "timezone": "Asia/Bangkok",
    }
    archive_hourly = fetch_hourly(
        "https://archive-api.open-meteo.com/v1/archive",
        params=archive_params,
        source="Archive",
    )
    forecast_hourly = fetch_hourly(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": 21.0285,
            "longitude": 105.8542,
            "hourly": HOURLY_FIELDS,
            "timezone": "Asia/Bangkok",
            "past_days": max(1, min(7, (now.date() - start_date.date()).days)),
            "forecast_days": 1,
        },
        source="Forecast",
    )
    hourly = pd.concat([archive_hourly, forecast_hourly], ignore_index=True)
    merged = update_hourly_csv(output, hourly)
    print(
        f"Đã cập nhật thời tiết: {len(merged)} giờ | archive từ "
        f"{archive_params['start_date']} | đến {merged.time.max()}"
    )


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    main()