import numpy as np
import pandas as pd


def build_model_pm25_comparison(model, measurements, default_source="OpenAQ"):
    model_required = {"time", "pm25"}
    measurement_required = {
        "location_id", "location_name", "parameter", "value", "unit",
        "observed_at",
    }
    missing_model = model_required.difference(model.columns)
    missing_measurements = measurement_required.difference(measurements.columns)
    if missing_model or missing_measurements:
        missing = sorted(missing_model | missing_measurements)
        raise ValueError(f"Thiếu cột so sánh nguồn: {', '.join(missing)}")

    cams = model[["time", "pm25"]].copy()
    cams["time"] = pd.to_datetime(cams["time"], errors="coerce").dt.floor("h")
    cams["cams_pm25"] = pd.to_numeric(cams.pop("pm25"), errors="coerce")
    cams = (
        cams.dropna(subset=["time", "cams_pm25"])
        .loc[lambda frame: frame["cams_pm25"].between(0, 1000)]
        .groupby("time", as_index=False)["cams_pm25"].mean()
    )

    observed = measurements.loc[
        measurements["parameter"].astype("string").str.casefold().eq("pm25")
    ].copy()
    if "source" not in observed:
        observed["source"] = default_source
    normalized_units = (
        observed["unit"].astype("string").str.casefold()
        .str.replace("μ", "µ", regex=False)
        .str.replace("³", "3", regex=False)
        .str.replace("^", "", regex=False)
        .str.replace(" ", "", regex=False)
    )
    observed = observed.loc[normalized_units.isin({"µg/m3", "ug/m3"})].copy()
    observed["time"] = pd.to_datetime(
        observed["observed_at"], errors="coerce"
    ).dt.floor("h")
    observed["openaq_pm25"] = pd.to_numeric(
        observed["value"], errors="coerce"
    )
    observed = observed.dropna(
        subset=["location_id", "location_name", "time", "openaq_pm25"]
    )
    observed = observed.loc[observed["openaq_pm25"].between(0, 1000)]
    observed = (
        observed.groupby(
            ["source", "location_id", "location_name", "time"], as_index=False
        )["openaq_pm25"].mean()
    )
    comparison = observed.merge(cams, on="time", how="inner")
    comparison["cams_minus_openaq"] = (
        comparison["cams_pm25"] - comparison["openaq_pm25"]
    )
    return comparison.sort_values(
        ["source", "location_id", "time"]
    ).reset_index(drop=True)


def summarize_pm25_model_agreement(comparison):
    required = {
        "location_id", "location_name", "cams_pm25", "openaq_pm25",
        "cams_minus_openaq",
    }
    missing = required.difference(comparison.columns)
    if missing:
        raise ValueError(
            f"Thiếu cột tổng hợp nguồn: {', '.join(sorted(missing))}"
        )
    rows = []
    group_columns = ["location_id", "location_name"]
    if "source" in comparison:
        group_columns.insert(0, "source")
    for group_key, group in comparison.groupby(group_columns, sort=True):
        source, location_id, location_name = (
            group_key
            if len(group_columns) == 3
            else ("OpenAQ", *group_key)
        )
        difference = group["cams_minus_openaq"]
        rows.append({
            "source": source,
            "location_id": location_id,
            "location_name": location_name,
            "overlap_hours": len(group),
            "mean_bias": difference.mean(),
            "mae": difference.abs().mean(),
            "rmse": np.sqrt((difference ** 2).mean()),
            "correlation": (
                group["cams_pm25"].corr(group["openaq_pm25"])
                if len(group) >= 2 else np.nan
            ),
        })
    return pd.DataFrame(rows, columns=[
        "source", "location_id", "location_name", "overlap_hours",
        "mean_bias", "mae", "rmse", "correlation",
    ])
