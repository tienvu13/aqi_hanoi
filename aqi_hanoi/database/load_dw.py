from uuid import uuid4

import pandas as pd
from sqlalchemy import text

from aqi_hanoi.core.settings import PROJECT_DIR


AREA = {
    "1583": "Long Biên",
    "8688": "Tây Hồ",
    "13026": "Đống Đa",
    "13251": "Bắc Từ Liêm",
}
FACT_COLUMNS = [
    "time_key", "station_key", "source_key", "aqi_level_key", "aqi",
    "pm25", "pm10", "temp", "humidity", "precipitation", "wind_speed",
    "wind_dir", "pressure",
]


def upsert_table(connection, frame, table, conflict_columns, update_columns):
    staging_table = f"_stg_{table}_{uuid4().hex}"
    frame.to_sql(staging_table, connection, if_exists="fail", index=False,
                 chunksize=5000, method="multi")
    columns = list(frame.columns)
    column_list = ", ".join(columns)
    conflict_list = ", ".join(conflict_columns)
    updates = ", ".join(
        f"{column} = EXCLUDED.{column}" for column in update_columns
    )
    statement = (
        f"INSERT INTO {table} ({column_list}) "
        f"SELECT {column_list} FROM {staging_table} WHERE 1 = 1 "
        f"ON CONFLICT ({conflict_list}) DO UPDATE SET {updates}"
    )
    connection.execute(text(statement))
    connection.execute(text(f"DROP TABLE {staging_table}"))


def load_data(connection):
    model_path = PROJECT_DIR / "clean" / "fact_model_hourly.csv"
    station_path = PROJECT_DIR / "clean" / "fact_station_hourly.csv"
    model = pd.read_csv(model_path, parse_dates=["time"])
    stations = pd.read_csv(station_path, parse_dates=["time"])
    model["station_uid"], model["source_name"] = "MODEL_CAMS", "Open-Meteo CAMS"
    stations["station_uid"] = stations["station_uid"].astype(str)
    stations["source_name"] = "WAQI"

    dimensions = (stations[["station_uid", "station_name", "lat", "lon"]]
                  .drop_duplicates("station_uid")
                  .copy())
    dimensions["area"] = dimensions["station_uid"].map(AREA)
    dimensions["is_model"] = False
    dimensions = pd.concat([
        pd.DataFrame([{
            "station_uid": "MODEL_CAMS",
            "station_name": "Hà Nội (mô hình CAMS)",
            "area": "Toàn Hà Nội",
            "lat": 21.0285,
            "lon": 105.8542,
            "is_model": True,
        }]),
        dimensions,
    ], ignore_index=True)

    columns = [
        "time", "station_uid", "source_name", "aqi", "pm25", "pm10",
        "temp", "humidity", "precipitation", "wind_speed", "wind_dir",
        "pressure",
    ]
    fact = pd.concat(
        [model.reindex(columns=columns), stations.reindex(columns=columns)],
        ignore_index=True,
    )
    fact = fact.drop_duplicates(["time", "station_uid", "source_name"], keep="last")

    timestamps = pd.DataFrame({
        "full_datetime": sorted(fact["time"].dropna().unique())
    })
    dt = timestamps["full_datetime"].dt
    timestamps["time_key"] = dt.strftime("%Y%m%d%H").astype(int)
    timestamps["date"], timestamps["year"], timestamps["month"] = dt.date, dt.year, dt.month
    timestamps["day"], timestamps["hour"], timestamps["dow"] = dt.day, dt.hour, dt.dayofweek
    timestamps["dow_name"] = dt.dayofweek.map(dict(enumerate(
        ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật"]
    )))
    timestamps["season"] = timestamps["month"].map(
        lambda month: "Xuân" if month in (2, 3, 4) else
        "Hè" if month in (5, 6, 7) else
        "Thu" if month in (8, 9, 10) else "Đông"
    )
    timestamps["is_weekend"] = timestamps["dow"] >= 5
    timestamps["is_rush_hour"] = timestamps["hour"].isin([7, 8, 9, 17, 18, 19])

    upsert_table(
        connection, dimensions, "dim_station", ["station_uid"],
        ["station_name", "area", "lat", "lon", "is_model"],
    )
    upsert_table(
        connection, timestamps, "dim_time", ["time_key"],
        [column for column in timestamps.columns if column != "time_key"],
    )

    station_keys = pd.read_sql(
        "SELECT station_key, station_uid FROM dim_station", connection
    )
    source_keys = pd.read_sql(
        "SELECT source_key, source_name FROM dim_source", connection
    )
    fact = fact.merge(station_keys, on="station_uid", validate="many_to_one")
    fact = fact.merge(source_keys, on="source_name", validate="many_to_one")
    fact["time_key"] = fact["time"].dt.strftime("%Y%m%d%H").astype(int)
    fact["aqi_level_key"] = pd.cut(
        fact["aqi"],
        bins=[-1, 50, 100, 150, 200, 300, 1000],
        labels=[1, 2, 3, 4, 5, 6],
    ).astype("Int64")
    fact = fact.reindex(columns=FACT_COLUMNS)
    fact = fact.drop_duplicates(
        ["time_key", "station_key", "source_key"], keep="last"
    )
    upsert_table(
        connection, fact, "fact_air_quality",
        ["time_key", "station_key", "source_key"],
        [column for column in FACT_COLUMNS
         if column not in {"time_key", "station_key", "source_key"}],
    )
    return len(fact), len(timestamps)


def main():
    from db import engine

    schema_path = PROJECT_DIR / "sql" / "schema.sql"
    with engine.begin() as connection:
        connection.exec_driver_sql(schema_path.read_text(encoding="utf-8"))
        fact_count, time_count = load_data(connection)
    print(f"Đã nạp/cập nhật: {fact_count} dòng fact | {time_count} dòng dim_time")


if __name__ == "__main__":
    main()
