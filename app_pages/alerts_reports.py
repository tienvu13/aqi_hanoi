import json

import pandas as pd
import streamlit as st

from aqi_hanoi.dashboard.dashboard_common import PROJECT_DIR, current_hanoi_time, read_csv
from aqi_hanoi.dashboard.dashboard_export import build_dashboard_export
from aqi_hanoi.analysis.station_coverage import count_overlap_hours


now = current_hanoi_time()
source_status_path = (
    PROJECT_DIR / "figures" / "source_collection_status.json"
)
st.subheader("Trạng thái thu thập các nguồn bổ sung")
if source_status_path.is_file():
    try:
        source_status = json.loads(
            source_status_path.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as error:
        st.error(f"Không đọc được trạng thái nguồn bổ sung: {error}")
        st.stop()
    source_rows = source_status.get("sources", [])
    if not isinstance(source_rows, list):
        st.error("Tệp trạng thái nguồn bổ sung không có danh sách hợp lệ.")
        st.stop()
    if source_rows:
        source_frame = pd.DataFrame(source_rows)
        st.dataframe(
            source_frame.rename(columns={
                "source": "Nguồn",
                "status": "Trạng thái",
                "started_at": "Bắt đầu",
                "finished_at": "Kết thúc",
                "exit_code": "Mã thoát",
                "detail": "Chi tiết",
            }),
            width="stretch",
            hide_index=True,
            alt="Kết quả thu thập theo nguồn dữ liệu bổ sung",
        )
        failed_sources = source_frame.loc[
            source_frame["status"].eq("FAILED")
        ]
        if not failed_sources.empty:
            st.warning(
                f"{len(failed_sources)} nguồn bổ sung lỗi; xem "
                "`raw/source_collection.log` để biết chi tiết."
            )
    else:
        st.info("Chưa có lượt chạy collector nguồn bổ sung.")
else:
    st.info(
        "Chưa có trạng thái thu thập. Chạy "
        "`python collect_optional_sources.py` hoặc pipeline đầy đủ."
    )

st.subheader("Cảnh báo cục bộ")
alerts_path = PROJECT_DIR / "figures" / "local_alerts.csv"
if alerts_path.is_file():
    alerts = read_csv(
        "figures/local_alerts.csv",
        ("horizon_hours", "status", "source_time", "detail"),
    )
    if alerts.empty:
        st.info("Tệp cảnh báo chưa có bản ghi.")
    elif alerts["status"].eq("DATA_STALE").any():
        st.warning(
            "Không phát hành dự báo vì dữ liệu đầu vào quá cũ. Cập nhật dữ liệu "
            "rồi chạy `python generate_alerts.py`."
        )
    elif alerts["status"].eq("ALERT").any():
        st.error("Mô hình cảnh báo có nguy cơ AQI vượt 150.")
    else:
        st.success("Không có cảnh báo vượt ngưỡng trong dự báo gần nhất.")
    st.dataframe(
        alerts,
        width="stretch",
        hide_index=True,
        alt="Cảnh báo AQI cục bộ theo chân trời dự báo",
    )
else:
    st.info(
        "Chưa có báo cáo cảnh báo. Chạy `python generate_alerts.py`; dữ liệu "
        "cũ sẽ được ghi rõ là DATA_STALE."
    )

st.subheader("Chất lượng dữ liệu")
quality_path = PROJECT_DIR / "figures" / "data_quality_report.csv"
model_path = PROJECT_DIR / "clean" / "fact_model_hourly.csv"
station_path = PROJECT_DIR / "clean" / "station_feed_hourly.csv"
if quality_path.is_file():
    quality = read_csv(
        "figures/data_quality_report.csv",
        ("source", "entity", "latest_data", "age_hours", "status", "detail"),
    )
    problems = quality.loc[quality["status"].isin(["WARN", "ERROR"])]
    if model_path.is_file() and station_path.is_file():
        model = read_csv("clean/fact_model_hourly.csv", ("time",))
        station = read_csv(
            "clean/station_feed_hourly.csv",
            ("time", "station_uid", "aqi_tram"),
        )
        overlaps = count_overlap_hours(model["time"], station["time"])
        overlap_a, overlap_b = st.columns(2)
        overlap_a.metric("Số giờ WAQI–CAMS trùng", f"{overlaps} / 24 tối thiểu")
        if overlaps < 24:
            overlap_b.warning(
                "Chưa đủ giờ trùng để diễn giải đánh giá CAMS–WAQI."
            )
        else:
            overlap_b.success(
                "Đạt tối thiểu 24 giờ; nên thu thập thêm để đánh giá vững hơn."
            )
    if problems.empty:
        st.success("Báo cáo gần nhất không có dòng WARN/ERROR.")
    else:
        station_rows = quality.loc[
            quality["entity"].astype(str).str.startswith("station:")
        ]
        fresh_count = station_rows.loc[
            station_rows["status"] == "OK", "entity"
        ].nunique()
        st.warning(
            f"Báo cáo có {len(problems)} dòng WARN/ERROR; "
            f"{fresh_count}/{station_rows['entity'].nunique()} trạm đạt freshness."
        )
        st.dataframe(
            problems,
            width="stretch",
            hide_index=True,
            alt="Các cảnh báo và lỗi trong báo cáo chất lượng dữ liệu",
        )
    with st.expander("Xem toàn bộ báo cáo chất lượng"):
        st.dataframe(
            quality,
            width="stretch",
            hide_index=True,
            alt="Báo cáo chất lượng dữ liệu đầy đủ",
        )
else:
    st.info("Chưa có báo cáo. Chạy `python data_quality.py` để tạo.")

st.subheader("Tải báo cáo")
st.caption(
    "Tệp ZIP gồm tóm tắt, độ phủ trạm, chất lượng dữ liệu, lịch sử dự báo và "
    "dữ liệu trong 30 ngày gần nhất; không bao gồm feed WAQI thô."
)
try:
    export_data = build_dashboard_export(PROJECT_DIR, now=now)
except (
    OSError,
    pd.errors.ParserError,
    UnicodeError,
    ValueError,
    json.JSONDecodeError,
) as error:
    st.error(f"Không thể tạo bộ báo cáo tải xuống: {error}")
else:
    st.download_button(
        "Tải bộ báo cáo CSV (ZIP)",
        data=export_data,
        file_name=f"bao-cao-aqi-ha-noi-{now:%Y%m%d-%H%M}.zip",
        mime="application/zip",
    )
