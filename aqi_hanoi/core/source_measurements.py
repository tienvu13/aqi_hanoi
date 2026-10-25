from pathlib import Path

import pandas as pd


MEASUREMENT_COLUMNS = [
    "collected_at", "source", "location_id", "location_name", "provider",
    "latitude", "longitude", "parameter", "value", "unit", "observed_at",
]
MEASUREMENT_KEYS = [
    "source", "location_id", "parameter", "observed_at",
]


def save_measurement_history(path, rows):
    path = Path(path)
    data = pd.DataFrame(rows)
    if data.empty:
        raise ValueError("Không có phép đo hợp lệ để lưu.")
    missing = set(MEASUREMENT_COLUMNS).difference(data.columns)
    if missing:
        raise ValueError(
            f"Thiếu cột phép đo: {', '.join(sorted(missing))}"
        )
    data = data[MEASUREMENT_COLUMNS].copy()
    for column in ("collected_at", "observed_at"):
        data[column] = pd.to_datetime(data[column], errors="raise")
    if data[MEASUREMENT_KEYS].isna().any().any():
        raise ValueError("Khóa phép đo nguồn không được để trống.")
    for column in ("source", "location_id", "parameter"):
        data[column] = data[column].astype("string")
    if path.is_file():
        previous = pd.read_csv(path)
        missing_previous = set(MEASUREMENT_COLUMNS).difference(previous.columns)
        if missing_previous:
            raise ValueError(
                f"Schema {path.name} không hợp lệ; thiếu cột: "
                f"{', '.join(sorted(missing_previous))}"
            )
        previous = previous[MEASUREMENT_COLUMNS].copy()
        for column in ("collected_at", "observed_at"):
            previous[column] = pd.to_datetime(previous[column], errors="raise")
        for column in ("source", "location_id", "parameter"):
            previous[column] = previous[column].astype("string")
        data = pd.concat([previous, data], ignore_index=True)
    data = (
        data.drop_duplicates(MEASUREMENT_KEYS, keep="last")
        .sort_values(["source", "location_id", "observed_at"])
        .reset_index(drop=True)
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    data.to_csv(temporary, index=False, encoding="utf-8")
    temporary.replace(path)
    return data
