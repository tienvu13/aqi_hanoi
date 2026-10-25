from pathlib import Path

import pandas as pd


def update_hourly_csv(path, hourly):
    """Merge downloaded hourly rows into a CSV without losing earlier history."""
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if "time" not in hourly.columns or hourly.empty:
        raise ValueError("Dữ liệu tải về phải có cột time và ít nhất một dòng.")

    new_data = hourly.copy()
    new_data["time"] = pd.to_datetime(new_data["time"], errors="raise")
    if output_path.exists():
        previous = pd.read_csv(output_path, parse_dates=["time"])
        new_data = pd.concat([previous, new_data], ignore_index=True)

    merged = (new_data.drop_duplicates("time", keep="last")
              .sort_values("time")
              .reset_index(drop=True))
    temp_path = output_path.with_name(output_path.name + ".tmp")
    merged.to_csv(temp_path, index=False, encoding="utf-8")
    temp_path.replace(output_path)
    return merged
