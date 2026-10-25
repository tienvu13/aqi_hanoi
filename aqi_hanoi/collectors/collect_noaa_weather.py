from datetime import datetime

import pandas as pd

from aqi_hanoi.core.http_utils import get_with_retry
from aqi_hanoi.core.settings import PROJECT_DIR


ICAO_ID = "VVNB"
STATION_NAME = "Noi Bai International Airport, Hanoi"
API_URL = "https://aviationweather.gov/api/data/metar"
OUTPUT_PATH = PROJECT_DIR / "raw" / "noaa_vvnb_metar.csv"
EXPECTED_COLUMNS = [
    "collected_at", "source", "station_id", "station_name",
    "latitude", "longitude", "observed_at", "temperature_c",
    "dewpoint_c", "wind_direction_deg", "wind_speed_kt",
    "visibility_sm", "pressure_hpa", "metar",
]


def parse_metar_reports(payload, collected_at):
    if not isinstance(payload, list):
        raise RuntimeError("NOAA Aviation Weather API trả về dữ liệu sai dạng.")
    rows = []
    for report in payload:
        if not isinstance(report, dict) or report.get("icaoId") != ICAO_ID:
            continue
        try:
            observed_at = pd.to_datetime(
                report["obsTime"], unit="s", utc=True
            ).tz_convert("Asia/Bangkok").tz_localize(None)
            latitude = float(report["lat"])
            longitude = float(report["lon"])
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        rows.append({
            "collected_at": collected_at,
            "source": "NOAA Aviation Weather",
            "station_id": ICAO_ID,
            "station_name": str(report.get("name") or STATION_NAME),
            "latitude": latitude,
            "longitude": longitude,
            "observed_at": observed_at,
            "temperature_c": pd.to_numeric(
                report.get("temp"), errors="coerce"
            ),
            "dewpoint_c": pd.to_numeric(
                report.get("dewp"), errors="coerce"
            ),
            "wind_direction_deg": pd.to_numeric(
                report.get("wdir"), errors="coerce"
            ),
            "wind_speed_kt": pd.to_numeric(
                report.get("wspd"), errors="coerce"
            ),
            "visibility_sm": str(report.get("visib") or ""),
            "pressure_hpa": pd.to_numeric(
                report.get("altim"), errors="coerce"
            ),
            "metar": str(report.get("rawOb") or ""),
        })
    return rows


def save_metar_history(rows, path=OUTPUT_PATH):
    if not rows:
        raise ValueError("Không có bản tin METAR hợp lệ để lưu.")
    data = pd.DataFrame(rows, columns=EXPECTED_COLUMNS)
    for column in ("collected_at", "observed_at"):
        data[column] = pd.to_datetime(data[column], errors="raise")
    if path.is_file():
        previous = pd.read_csv(path)
        if list(previous.columns) != EXPECTED_COLUMNS:
            raise ValueError(
                f"Schema {path.name} không khớp; không ghi đè lịch sử."
            )
        for column in ("collected_at", "observed_at"):
            previous[column] = pd.to_datetime(previous[column], errors="raise")
        data = pd.concat([previous, data], ignore_index=True)
    data = (
        data.drop_duplicates(["station_id", "observed_at"], keep="last")
        .sort_values("observed_at")
        .reset_index(drop=True)
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    data.to_csv(temporary, index=False, encoding="utf-8")
    temporary.replace(path)
    return data


def collect_noaa_metar(now=None, output_path=OUTPUT_PATH):
    collected_at = (
        pd.Timestamp.now(tz="Asia/Bangkok").tz_localize(None)
        if now is None else pd.Timestamp(now)
    )
    if collected_at.tzinfo is not None:
        collected_at = collected_at.tz_convert(
            "Asia/Bangkok"
        ).tz_localize(None)
    response = get_with_retry(
        API_URL,
        params={
            "ids": ICAO_ID,
            "format": "json",
            "hours": 24,
        },
        headers={"User-Agent": "aqi-hanoi/1.0 (weather observations)"},
        timeout=30,
    )
    try:
        payload = response.json()
    except ValueError as error:
        raise RuntimeError(
            "NOAA Aviation Weather API trả về JSON không hợp lệ."
        ) from error
    rows = parse_metar_reports(payload, collected_at)
    if not rows:
        raise RuntimeError(
            "NOAA không trả bản tin METAR gần đây cho trạm VVNB."
        )
    saved = save_metar_history(rows, path=output_path)
    print(
        f"NOAA METAR {ICAO_ID}: {len(rows)} báo cáo mới; "
        f"quan trắc gần nhất {saved['observed_at'].max()}."
    )
    return saved


def main():
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    collect_noaa_metar()


if __name__ == "__main__":
    main()
