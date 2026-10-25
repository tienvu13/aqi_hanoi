import pandas as pd
import streamlit as st

from aqi_hanoi.core.aqi_guidance import interpret_aqi
from aqi_hanoi.dashboard.dashboard_common import (
    PROJECT_DIR,
    current_hanoi_time,
    load_station_feed,
    read_csv,
)
from aqi_hanoi.analysis.station_coverage import (
    build_daily_station_summary,
    build_station_comparison_history,
    build_station_history,
    build_station_map_data,
    build_station_coverage,
    latest_station_collection_batch,
    summarize_station_collection,
)
from aqi_hanoi.core.waqi_stations import (
    TARGET_STATIONS,
    filter_hanoi_coordinates,
    filter_hanoi_waqi_data,
)


now = current_hanoi_time()
raw_path = PROJECT_DIR / "raw" / "waqi_feed.csv"
if not raw_path.is_file():
    st.info("Chưa có feed WAQI; hãy chạy collector và ETL feed.")
    st.stop()

raw_feed = read_csv(
    "raw/waqi_feed.csv",
    (
        "station_uid", "station_name", "collected_at", "obs_time",
        "age_min", "aqi", "lat", "lon",
    ),
)
raw_feed = filter_hanoi_waqi_data(
    raw_feed, "lat", "lon", "station_name"
)
active_feed = latest_station_collection_batch(raw_feed)
if active_feed.empty:
    st.warning(
        "Lượt thu thập WAQI mới nhất không có trạm nào trả về AQI hợp lệ và "
        "đủ mới. Danh sách trạng thái hiện tại trống; dữ liệu lịch sử vẫn "
        "được giữ bên dưới."
    )

coverage = build_station_coverage(active_feed, now=now)
fresh_count = int(coverage["status"].eq("FRESH").sum())
st.metric(
    "Trạm có AQI mới và hợp lệ",
    f"{fresh_count}/{TARGET_STATIONS} mục tiêu",
    border=True,
)
if fresh_count < TARGET_STATIONS:
    st.info(
        f"Hiện có {fresh_count}/{TARGET_STATIONS} trạm đạt freshness. "
        "Số trạm thực tế phụ thuộc dữ liệu WAQI trong phạm vi Hà Nội."
    )
st.dataframe(
    coverage.rename(columns={
        "station_uid": "Mã trạm",
        "station_name": "Trạm",
        "latest_observation": "Giờ đo gần nhất",
        "latest_aqi": "AQI hợp lệ gần nhất",
        "age_hours": "Tuổi số đo (giờ)",
        "status": "Trạng thái",
        "detail": "Chi tiết",
    }),
    width="stretch",
    hide_index=True,
    alt="Độ phủ và trạng thái AQI mới nhất của từng trạm",
)
st.caption(
    "Danh sách trạng thái chỉ gồm trạm có AQI hợp lệ trong lượt thu thập gần "
    "nhất; trạm không có AQI mới bị ẩn khỏi danh sách này, không xóa lịch sử. "
    f"Mục tiêu là {TARGET_STATIONS} trạm trong khu vực Hà Nội. "
    "Mới = giờ đo không quá 3 giờ."
)

st.subheader("OpenAQ — phép đo PM từ nguồn riêng")
openaq_path = PROJECT_DIR / "clean" / "openaq_latest.csv"
if not openaq_path.is_file():
    st.info(
        "Chưa có dữ liệu OpenAQ. Cấu hình `OPENAQ_API_KEY`, rồi chạy "
        "`python collect_openaq.py`."
    )
else:
    openaq = read_csv(
        "clean/openaq_latest.csv",
        (
            "location_id", "location_name", "provider", "latitude",
            "longitude", "parameter", "value", "unit", "observed_at",
            "age_hours", "license_names",
        ),
    )
    openaq = filter_hanoi_coordinates(openaq, "latitude", "longitude")
    openaq["observed_at"] = pd.to_datetime(
        openaq["observed_at"], errors="coerce"
    )
    openaq["age_hours"] = pd.to_numeric(
        openaq["age_hours"], errors="coerce"
    )
    openaq["value"] = pd.to_numeric(openaq["value"], errors="coerce")
    openaq = openaq.dropna(
        subset=["observed_at", "age_hours", "value", "location_id"]
    )
    if openaq.empty:
        st.warning("Tệp OpenAQ hiện không có phép đo hợp lệ.")
    else:
        openaq_locations = openaq["location_id"].nunique()
        latest_measurements = int(openaq["age_hours"].between(0, 24).sum())
        openaq_a, openaq_b = st.columns(2)
        openaq_a.metric("Trạm OpenAQ có số đo mới", openaq_locations)
        openaq_b.metric("Phép đo trong 24 giờ", latest_measurements)
        openaq_display = openaq.rename(columns={
            "location_name": "Trạm",
            "provider": "Nhà cung cấp",
            "parameter": "Chất ô nhiễm",
            "value": "Nồng độ",
            "unit": "Đơn vị",
            "observed_at": "Thời điểm đo",
            "age_hours": "Tuổi số đo (giờ)",
            "license_names": "Giấy phép",
        })
        st.dataframe(
            openaq_display[
                [
                    "Trạm", "Nhà cung cấp", "Chất ô nhiễm", "Nồng độ",
                    "Đơn vị", "Thời điểm đo", "Tuổi số đo (giờ)", "Giấy phép",
                ]
            ],
            width="stretch",
            hide_index=True,
            alt="Nồng độ PM2.5 và PM10 từ các trạm OpenAQ",
        )
        openaq_map = openaq.drop_duplicates("location_id").rename(columns={
            "latitude": "lat",
            "longitude": "lon",
        })
        st.map(
            openaq_map,
            latitude="lat",
            longitude="lon",
            zoom=6,
            height=320,
            alt="Vị trí các trạm OpenAQ có số đo PM",
        )
        attributions = sorted(
            openaq["provider"].dropna().astype(str).unique()
        )
        st.caption(
            "Đây là nồng độ PM2.5/PM10 theo đơn vị nguồn cung cấp, không phải "
            "AQI và không gộp với WAQI. Nhà cung cấp: "
            + (", ".join(attributions) if attributions else "không rõ")
            + ". Kiểm tra giấy phép/nguồn trước khi tái phân phối."
        )

st.subheader("Chất lượng lượt thu thập trong 24 giờ")
health = summarize_station_collection(raw_feed, now=now)
attempts = int(health["attempts"].sum())
usable = int(health["usable_count"].sum())
health_a, health_b, health_c = st.columns(3)
health_a.metric("Tổng lượt thu thập", attempts)
health_b.metric("Lượt có AQI dùng được", f"{usable}/{attempts}")
health_c.metric(
    "Tỷ lệ dùng được",
    f"{usable / attempts:.1%}" if attempts else "—",
)
st.dataframe(
    health.rename(columns={
        "station_uid": "Mã trạm",
        "station_name": "Trạm",
        "attempts": "Lượt thu thập",
        "valid_aqi_count": "Lượt có AQI hợp lệ",
        "usable_count": "Lượt có AQI mới",
        "usable_pct": "Tỷ lệ dùng được (%)",
        "latest_collection": "Lượt thu thập gần nhất",
        "latest_observation": "Giờ đo WAQI gần nhất",
        "latest_aqi": "AQI lượt gần nhất",
        "latest_result": "Kết quả lượt gần nhất",
    }),
    width="stretch",
    hide_index=True,
    alt="Chất lượng lượt thu thập theo trạm trong 24 giờ",
)
st.caption(
    "NO_AQI: lượt đọc không trả AQI; STALE_OBSERVATION: số đo đã cũ; "
    "NO_RECENT_COLLECTION: không có lượt thu thập trong 24 giờ."
)

map_data = build_station_map_data(coverage, raw_feed)
st.subheader("Bản đồ trạng thái các trạm")
if map_data.empty:
    st.info("Chưa có tọa độ hợp lệ để hiển thị.")
else:
    st.map(
        map_data,
        latitude="lat",
        longitude="lon",
        color="map_color",
        zoom=10,
        height=380,
        alt="Vị trí trạm WAQI và tình trạng số đo mới nhất",
    )
    st.caption(
        "Xanh: AQI mới; vàng: AQI cũ; xám: chưa có AQI hợp lệ. Không nội suy "
        "giữa các vị trí trạm."
    )

st.subheader("Hướng dẫn theo AQI trạm mới nhất")
fresh = coverage.loc[coverage["status"] == "FRESH"]
if fresh.empty:
    st.warning(
        "Không có số đo đủ mới để đưa hướng dẫn. Không dùng AQI cũ làm "
        "tình trạng hiện tại."
    )
else:
    fresh["station_uid"] = fresh["station_uid"].astype(str)
    names = dict(zip(fresh["station_uid"], fresh["station_name"]))
    selected_uid = st.selectbox(
        "Chọn trạm có AQI mới",
        fresh["station_uid"].tolist(),
        format_func=lambda uid: f"{names[uid]} ({uid})",
    )
    selected = fresh.loc[fresh["station_uid"] == selected_uid].iloc[0]
    guidance = interpret_aqi(selected["latest_aqi"])
    if guidance is not None:
        st.metric(
            f"AQI tại {selected['station_name']}",
            f"{guidance['value']:.0f} · {guidance['level']}",
        )
        st.info(guidance["advice"])
        st.caption(
            f"Số đo lúc {selected['latest_observation']:%d/%m/%Y %H:%M}; "
            "chỉ đại diện cho trạm được chọn."
        )
st.caption(
    "Hướng dẫn là thông tin chung, không thay thế cảnh báo chính thức hoặc "
    "tư vấn y tế."
)

stations = load_station_feed()
st.subheader("Lịch sử và so sánh giữa các trạm")
if stations is None or stations.empty:
    st.info("Chưa có quan trắc WAQI hợp lệ theo giờ.")
    st.stop()
stations = filter_hanoi_waqi_data(
    stations, "lat", "lon", "station_name"
)
if stations.empty:
    st.info("Chưa có quan trắc WAQI hợp lệ trong khu vực Hà Nội.")
    st.stop()

station_list = stations[
    ["station_uid", "station_name"]
].drop_duplicates("station_uid", keep="last")
station_list["station_uid"] = station_list["station_uid"].astype(str)
names = dict(zip(station_list["station_uid"], station_list["station_name"]))
selector_a, selector_b = st.columns(2)
history_uid = selector_a.selectbox(
    "Trạm xem lịch sử",
    station_list["station_uid"].tolist(),
    format_func=lambda uid: f"{names[uid]} ({uid})",
)
days = selector_b.selectbox(
    "Khoảng lịch sử trạm",
    [1, 3, 7, 30],
    index=2,
    format_func=lambda value: f"{value} ngày",
)
history = build_station_history(stations, history_uid, days=days, now=now)
if history.empty:
    st.info(f"Trạm {names[history_uid]} chưa có AQI hợp lệ trong khoảng chọn.")
else:
    latest_time = history["time"].max()
    latest_age = (now - latest_time).total_seconds() / 3600
    hist_a, hist_b = st.columns(2)
    hist_a.metric("Số giờ quan trắc", len(history))
    hist_b.metric("Số đo gần nhất", latest_time.strftime("%d/%m %H:%M"))
    if latest_age > 3:
        st.warning(
            f"Số đo gần nhất đã cũ {latest_age:.1f} giờ; không xem lịch sử "
            "này là tình trạng hiện tại."
        )
    st.line_chart(
        history.set_index("time")[["aqi_tram"]].rename(
            columns={"aqi_tram": "AQI trạm"}
        ),
        x_label="Giờ đo",
        y_label="AQI",
        alt=f"Diễn biến AQI tại {names[history_uid]}",
    )
    pollutants = [
        column for column in ("pm25_aqi", "pm10_aqi")
        if column in history and history[column].notna().any()
    ]
    if pollutants:
        st.line_chart(
            history.set_index("time")[pollutants].rename(columns={
                "pm25_aqi": "PM2.5 AQI",
                "pm10_aqi": "PM10 AQI",
            }),
            x_label="Giờ đo",
            y_label="AQI thành phần",
            alt=f"Chỉ số AQI thành phần tại {names[history_uid]}",
        )

compare_uids = st.multiselect(
    "Chọn trạm cần so sánh",
    options=station_list["station_uid"].tolist(),
    default=station_list["station_uid"].tolist()[:3],
    format_func=lambda uid: f"{names[uid]} ({uid})",
)
if len(compare_uids) < 2:
    st.info("Chọn ít nhất 2 trạm để so sánh.")
else:
    comparison = build_station_comparison_history(
        stations, compare_uids, days=days, now=now
    )
    if comparison.empty:
        st.info("Các trạm đã chọn chưa có AQI hợp lệ trong khoảng này.")
    else:
        overlap = int(comparison.notna().all(axis=1).sum())
        st.metric("Giờ có dữ liệu đồng thời giữa các trạm", overlap)
        st.line_chart(
            comparison,
            x_label="Giờ đo",
            y_label="AQI WAQI",
            alt="So sánh diễn biến AQI giữa các trạm được chọn",
        )
        st.dataframe(
            comparison.count().rename("Số giờ AQI hợp lệ"),
            width="stretch",
            alt="Số giờ dữ liệu hợp lệ theo từng trạm",
        )
        if overlap < 24:
            st.caption(
                f"Mới có {overlap} giờ đồng thời; chỉ xem xu hướng, chưa đủ "
                "cơ sở so sánh thống kê."
            )

st.subheader("Tổng hợp AQI theo trạm")
tracked = raw_feed[
    ["station_uid", "station_name"]
].drop_duplicates("station_uid", keep="last")
daily = build_daily_station_summary(
    stations, days=days, now=now, tracked_stations=tracked
)
if daily.empty:
    st.info("Chưa có giờ hoàn tất nào trong khoảng chọn.")
else:
    label_map = dict(zip(
        daily["station_uid"].astype(str),
        daily["station_name"],
    ))
    selected_daily_uids = st.multiselect(
        "Trạm trong bảng tổng hợp",
        options=sorted(label_map),
        default=sorted(label_map)[:3],
        format_func=lambda uid: f"{label_map[uid]} ({uid})",
        key="station_daily_summary_selection",
    )
    visible = daily.loc[
        daily["station_uid"].astype(str).isin(selected_daily_uids)
    ].copy()
    if not visible.empty:
        visible["Mã trạm"] = visible["station_uid"].astype(str)
        visible["Trạm"] = visible["station_name"]
        chart = visible.pivot(
            index="station_name",
            columns="date",
            values="aqi_mean",
        )
        st.line_chart(
            chart.T,
            x_label="Ngày",
            y_label="AQI trung bình WAQI",
            alt="AQI trung bình mỗi ngày theo trạm",
        )
        st.dataframe(
            visible.rename(columns={
                "date": "Ngày",
                "aqi_mean": "AQI trung bình",
                "aqi_max": "AQI cao nhất",
                "hours_available": "Giờ có AQI",
                "expected_hours": "Giờ kỳ vọng",
                "hours_missing": "Giờ thiếu",
                "coverage_pct": "Độ phủ (%)",
                "hours_above_100": "Giờ AQI > 100",
                "hours_above_150": "Giờ AQI > 150",
                "hours_above_200": "Giờ AQI > 200",
            })[
                [
                    "Mã trạm", "Trạm", "Ngày", "AQI trung bình",
                    "AQI cao nhất", "Giờ có AQI", "Giờ kỳ vọng", "Giờ thiếu",
                    "Độ phủ (%)", "Giờ AQI > 100", "Giờ AQI > 150",
                    "Giờ AQI > 200",
                ]
            ],
            width="stretch",
            hide_index=True,
            alt="Tổng hợp AQI và độ phủ theo trạm và ngày",
        )
