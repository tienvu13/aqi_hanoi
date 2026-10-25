import pandas as pd
import streamlit as st

from aqi_hanoi.analysis.aqi_trends import build_daily_aqi_summary, build_hourly_coverage_report
from aqi_hanoi.dashboard.dashboard_common import (
    PROJECT_DIR,
    current_hanoi_time,
    load_model,
    read_csv,
    render_pipeline_status,
)
from aqi_hanoi.core.waqi_stations import filter_hanoi_waqi_data


now = current_hanoi_time()
render_pipeline_status(now)
model = load_model()
latest_time = model.index.max()
age_hours = (now - latest_time).total_seconds() / 3600
if age_hours > 6:
    st.error(
        f"Dữ liệu mô hình gần nhất đã cũ {age_hours:.1f} giờ "
        f"(mốc {latest_time:%d/%m/%Y %H:%M}); không xem là AQI hiện tại."
    )
elif age_hours < -1:
    st.info(
        f"Mốc dữ liệu {latest_time:%d/%m/%Y %H:%M} nằm phía trước giờ hiện tại."
    )
else:
    st.success(f"Dữ liệu mô hình gần nhất: {latest_time:%d/%m/%Y %H:%M}.")

latest = model.loc[latest_time]
metric_a, metric_b, metric_c = st.columns(3)
metric_a.metric("AQI mô hình gần nhất", f"{latest['aqi']:.0f}")
metric_b.metric(
    "PM2.5 gần nhất",
    f"{latest['pm25']:.1f}" if pd.notna(latest["pm25"]) else "—",
)
metric_c.metric("Mốc dữ liệu", latest_time.strftime("%d/%m %H:%M"))
st.caption(
    "AQI CAMS là giá trị mô hình, không phải phép đo tức thời của trạm."
)

st.subheader("Quan trắc khí tượng thật — sân bay Nội Bài")
metar_path = PROJECT_DIR / "raw" / "noaa_vvnb_metar.csv"
if not metar_path.is_file():
    st.info(
        "Chưa có METAR quan trắc tại Nội Bài. Chạy "
        "`python collect_noaa_weather.py` để thu thập."
    )
else:
    metar = read_csv(
        "raw/noaa_vvnb_metar.csv",
        (
            "station_id", "station_name", "observed_at", "temperature_c",
            "dewpoint_c", "wind_direction_deg", "wind_speed_kt",
            "pressure_hpa", "visibility_sm", "metar",
        ),
    )
    metar["observed_at"] = pd.to_datetime(
        metar["observed_at"], errors="coerce"
    )
    for column in (
        "temperature_c", "dewpoint_c", "wind_direction_deg",
        "wind_speed_kt", "pressure_hpa",
    ):
        metar[column] = pd.to_numeric(metar[column], errors="coerce")
    metar = metar.dropna(subset=["observed_at"]).sort_values("observed_at")
    if metar.empty:
        st.warning("Tệp METAR chưa có thời điểm quan trắc hợp lệ.")
    else:
        latest_metar = metar.iloc[-1]
        observation_age = (
            now - latest_metar["observed_at"]
        ).total_seconds() / 3600
        if observation_age <= 3:
            st.success(
                f"METAR mới nhất lúc "
                f"{latest_metar['observed_at']:%d/%m/%Y %H:%M}."
            )
        else:
            st.warning(
                f"METAR mới nhất đã cũ {observation_age:.1f} giờ; "
                "không xem là thời tiết hiện tại."
            )
        latest_columns = st.columns(4)
        latest_columns[0].metric(
            "Nhiệt độ",
            (
                f"{latest_metar['temperature_c']:.1f} °C"
                if pd.notna(latest_metar["temperature_c"]) else "—"
            ),
        )
        latest_columns[1].metric(
            "Gió",
            (
                f"{latest_metar['wind_speed_kt']:.0f} kt"
                if pd.notna(latest_metar["wind_speed_kt"]) else "—"
            ),
        )
        latest_columns[2].metric(
            "Hướng gió",
            (
                f"{latest_metar['wind_direction_deg']:.0f}°"
                if pd.notna(latest_metar["wind_direction_deg"]) else "—"
            ),
        )
        latest_columns[3].metric(
            "Áp suất",
            (
                f"{latest_metar['pressure_hpa']:.0f} hPa"
                if pd.notna(latest_metar["pressure_hpa"]) else "—"
            ),
        )
        metar_chart = (
            metar.set_index("observed_at")[["temperature_c", "dewpoint_c"]]
            .rename(columns={
                "temperature_c": "Nhiệt độ",
                "dewpoint_c": "Điểm sương",
            })
            .tail(48)
        )
        st.line_chart(
            metar_chart,
            x_label="Giờ quan trắc",
            y_label="°C",
            alt="Nhiệt độ và điểm sương từ METAR tại sân bay Nội Bài",
        )
        st.caption(
            "Đây là quan trắc METAR tại một sân bay, không đại diện toàn Hà Nội. "
            "Gió hiển thị theo knot; dữ liệu Open-Meteo bên dưới vẫn là mô hình "
            "lưới tại tọa độ trung tâm, không phải trạm đo."
        )

st.subheader("Diễn biến AQI mô hình")
chart_days = st.selectbox(
    "Khoảng thời gian",
    [1, 3, 7, 30],
    index=2,
    format_func=lambda value: f"{value} ngày",
    key="overview_model_days",
)
chart_data = model.loc[
    model.index >= latest_time - pd.Timedelta(days=chart_days),
    ["aqi"],
]
st.line_chart(
    chart_data,
    y="aqi",
    x_label="Thời gian",
    y_label="AQI mô hình",
    alt="Diễn biến AQI mô hình theo giờ",
)

st.subheader("Tổng hợp AQI mô hình theo ngày")
trend_days = st.selectbox(
    "Khoảng tổng hợp theo ngày",
    [7, 30, 90],
    index=1,
    format_func=lambda value: f"{value} ngày",
    key="overview_trend_days",
)
daily = build_daily_aqi_summary(
    model.reset_index()[["time", "aqi"]],
    days=trend_days,
    now=now,
)
if daily.empty:
    st.info(f"Chưa có dữ liệu trong {trend_days} ngày đã qua.")
else:
    highest_mean = daily.loc[daily["aqi_mean"].idxmax()]
    highest_peak = daily.loc[daily["aqi_max"].idxmax()]
    mean_a, mean_b, mean_c = st.columns(3)
    mean_a.metric(
        "Ngày AQI trung bình cao nhất",
        f"{highest_mean['aqi_mean']:.1f}",
        delta=highest_mean["date"].strftime("%d/%m/%Y"),
    )
    mean_b.metric(
        "Ngày có AQI giờ cao nhất",
        f"{highest_peak['aqi_max']:.0f}",
        delta=highest_peak["date"].strftime("%d/%m/%Y"),
    )
    mean_c.metric(
        "Độ phủ giờ trung bình",
        f"{daily['coverage_pct'].mean():.1f}%",
    )
    daily_chart = daily.set_index("date")[
        ["aqi_mean", "aqi_max"]
    ].rename(columns={
        "aqi_mean": "AQI trung bình",
        "aqi_max": "AQI giờ cao nhất",
    })
    st.line_chart(
        daily_chart,
        x_label="Ngày",
        y_label="AQI mô hình",
        alt="AQI trung bình và cao nhất theo ngày",
    )
    threshold_chart = daily.set_index("date")[
        ["hours_above_100", "hours_above_150", "hours_above_200"]
    ].rename(columns={
        "hours_above_100": "Giờ AQI > 100",
        "hours_above_150": "Giờ AQI > 150",
        "hours_above_200": "Giờ AQI > 200",
    })
    st.bar_chart(
        threshold_chart,
        x_label="Ngày",
        y_label="Số giờ",
        alt="Số giờ AQI mô hình vượt ngưỡng theo ngày",
    )
    display = daily.rename(columns={
        "date": "Ngày",
        "aqi_mean": "AQI TB mô hình",
        "aqi_max": "AQI giờ cao nhất",
        "hours_available": "Số giờ có dữ liệu",
        "hours_above_100": "Giờ AQI > 100",
        "hours_above_150": "Giờ AQI > 150",
        "hours_above_200": "Giờ AQI > 200",
        "coverage_pct": "Độ phủ giờ (%)",
    })
    st.dataframe(
        display,
        width="stretch",
        hide_index=True,
        alt="AQI mô hình và độ phủ theo ngày",
    )

st.subheader("Liên tục dữ liệu theo giờ")
coverage_days = st.selectbox(
    "Khoảng kiểm tra độ phủ",
    [1, 3, 7, 30],
    index=2,
    format_func=lambda value: f"{value} ngày",
    key="overview_coverage_days",
)
station_path = PROJECT_DIR / "clean" / "station_feed_hourly.csv"
stations = None
if station_path.is_file():
    stations = read_csv(
        "clean/station_feed_hourly.csv",
        ("time", "station_uid", "aqi_tram"),
    )
    if "station_name" in stations.columns:
        stations = stations[
            ["time", "station_uid", "station_name", "aqi_tram"]
        ]
raw_path = PROJECT_DIR / "raw" / "waqi_feed.csv"
tracked = None
if raw_path.is_file():
    raw_stations = read_csv(
        "raw/waqi_feed.csv",
        ("station_uid", "station_name", "lat", "lon"),
    )
    raw_stations = filter_hanoi_waqi_data(
        raw_stations, "lat", "lon", "station_name"
    )
    tracked = raw_stations[
        ["station_uid", "station_name"]
    ].drop_duplicates("station_uid", keep="last")
coverage = build_hourly_coverage_report(
    model.reset_index()[["time", "aqi"]],
    stations=stations,
    days=coverage_days,
    now=now,
    tracked_stations=tracked,
)
coverage_display = coverage.rename(columns={
    "source": "Nguồn",
    "station_uid": "Mã trạm",
    "expected_hours": "Số giờ kỳ vọng",
    "observed_hours": "Số giờ có dữ liệu",
    "missing_hours": "Số giờ thiếu",
    "coverage_pct": "Độ phủ (%)",
    "gap_count": "Số khoảng trống",
    "longest_gap_hours": "Khoảng thiếu dài nhất (giờ)",
    "latest_observation": "Mốc mới nhất",
})
st.dataframe(
    coverage_display,
    width="stretch",
    hide_index=True,
    alt="Số giờ có dữ liệu và khoảng trống theo nguồn",
)
st.bar_chart(
    coverage.set_index("source")[["coverage_pct"]].rename(
        columns={"coverage_pct": "Độ phủ giờ (%)"}
    ),
    x_label="Nguồn",
    y_label="Độ phủ (%)",
    alt="Tỷ lệ giờ có dữ liệu theo nguồn",
)
