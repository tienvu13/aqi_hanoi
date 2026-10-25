import pandas as pd
import streamlit as st

from aqi_hanoi.dashboard.dashboard_common import (
    PROJECT_DIR,
    load_model,
    read_csv,
    read_optional_csv,
)
from aqi_hanoi.analysis.station_coverage import build_hourly_comparison
from aqi_hanoi.analysis.source_comparison import (
    build_model_pm25_comparison,
    summarize_pm25_model_agreement,
)


model = load_model()
st.subheader("Đánh giá mô hình trên tập test")
forecast_results = read_csv(
    "figures/forecast_results.csv",
    ("horizon", "split", "model", "mae", "rmse"),
)
alert_results = read_csv(
    "figures/alert_results.csv",
    ("horizon", "split", "model", "f1", "probability_threshold"),
)
forecast_test = forecast_results.loc[forecast_results["split"] == "test"]
alert_test = alert_results.loc[alert_results["split"] == "test"]
tab_forecast, tab_classification = st.tabs(
    ["Dự báo AQI", "Phân loại nguy cơ"]
)
with tab_forecast:
    st.dataframe(
        forecast_test,
        width="stretch",
        hide_index=True,
        alt="Đánh giá dự báo AQI trên tập test",
    )
with tab_classification:
    st.dataframe(
        alert_test,
        width="stretch",
        hide_index=True,
        alt="Đánh giá phân loại nguy cơ trên tập test",
    )
st.caption(
    "Kết quả offline, không phải dự báo mới. Chạy `python forecast.py` hoặc "
    "`python alert_classify.py` để cập nhật."
)

st.subheader("CAMS và quan trắc WAQI theo giờ trùng")
station_path = PROJECT_DIR / "clean" / "station_feed_hourly.csv"
if not station_path.is_file():
    st.info("Chưa có feed WAQI để so sánh với CAMS.")
else:
    stations = read_csv(
        "clean/station_feed_hourly.csv",
        ("time", "station_uid", "aqi_tram"),
    )
    comparison = build_hourly_comparison(
        model.reset_index()[["time", "aqi"]],
        stations,
    )
    if comparison.empty:
        st.info("Chưa có giờ nào có dữ liệu đồng thời từ CAMS và WAQI.")
    else:
        overlap = len(comparison)
        if overlap < 24:
            st.warning(
                f"Mới có {overlap}/24 giờ trùng. Chỉ xem biểu đồ tham khảo; "
                "chưa hiển thị tương quan hoặc MAE."
            )
        else:
            correlation = comparison["aqi_cams"].corr(
                comparison["aqi_waqi_mean"]
            )
            mae = (
                comparison["aqi_cams"] - comparison["aqi_waqi_mean"]
            ).abs().mean()
            metric_a, metric_b, metric_c = st.columns(3)
            metric_a.metric("Giờ trùng", overlap)
            metric_b.metric(
                "Tương quan",
                f"{correlation:.3f}" if pd.notna(correlation) else "—",
            )
            metric_c.metric("MAE", f"{mae:.1f}")
        chart = comparison.set_index("time")[
            ["aqi_cams", "aqi_waqi_mean"]
        ].rename(columns={
            "aqi_cams": "CAMS (mô hình)",
            "aqi_waqi_mean": "WAQI (trung bình trạm)",
        })
        st.line_chart(
            chart,
            x_label="Giờ đo",
            y_label="AQI",
            alt="AQI CAMS và WAQI tại các giờ trùng nhau",
        )
        st.dataframe(
            comparison.rename(columns={
                "time": "Giờ",
                "aqi_cams": "AQI CAMS",
                "aqi_waqi_mean": "AQI WAQI trung bình",
                "station_count": "Số trạm đóng góp",
            }),
            width="stretch",
            hide_index=True,
            alt="Dữ liệu CAMS và WAQI theo giờ trùng",
        )
        st.caption(
            "WAQI là trung bình các trạm có số đo trong mỗi giờ; số trạm đóng "
            "góp có thể thay đổi. CAMS cùng họ nguồn với nhãn huấn luyện."
        )

st.subheader("Đối chiếu PM2.5 giữa CAMS và các nguồn quan trắc")
openaq_history = read_optional_csv(
    "raw/openaq_measurements.csv",
    (
        "location_id", "location_name", "parameter", "value", "unit",
        "observed_at",
    ),
    command="python collect_openaq.py",
)
measurement_frames = []
if openaq_history is not None:
    openaq_history = openaq_history.copy()
    openaq_history["source"] = "OpenAQ"
    measurement_frames.append(openaq_history)

for relative_path, source_name, command in [
    (
        "raw/hanoi_portal_pm25.csv",
        "Hanoi monitoring portal",
        "python collect_hanoi_portal.py",
    ),
    (
        "raw/sensorcommunity_pm25.csv",
        "Sensor.Community",
        "python collect_sensorcommunity.py",
    ),
]:
    observations = read_optional_csv(
        relative_path,
        (
            "location_id", "location_name", "parameter", "value", "unit",
            "observed_at",
        ),
        command=command,
    )
    if observations is not None:
        observations = observations.copy()
        observations["source"] = source_name
        measurement_frames.append(observations)

if not measurement_frames:
    st.info("Chưa có dữ liệu quan trắc độc lập để so sánh với CAMS.")
else:
    source_measurements = pd.concat(measurement_frames, ignore_index=True)
    pm_comparison = build_model_pm25_comparison(
        model.reset_index()[["time", "pm25"]],
        source_measurements,
    )
    if pm_comparison.empty:
        st.info("Chưa có giờ trùng PM2.5 cùng đơn vị µg/m³ giữa các nguồn.")
    else:
        agreement = summarize_pm25_model_agreement(pm_comparison)
        low_overlap = agreement.loc[agreement["overlap_hours"] < 24]
        if not low_overlap.empty:
            st.warning(
                "Một số trạm có dưới 24 giờ trùng; các chỉ số hiện tại chỉ "
                "mang tính tham khảo, chưa đủ đánh giá độ tin cậy."
            )
        names = dict(zip(
            zip(agreement["source"], agreement["location_id"]),
            agreement["location_name"],
        ))
        comparison_options = list(zip(
            agreement["source"],
            agreement["location_id"],
        ))
        selected_location = st.selectbox(
            "Chọn trạm và nguồn để đối chiếu PM2.5",
            comparison_options,
            format_func=lambda selection: (
                f"{names[selection]} · {selection[0]} ({selection[1]})"
            ),
        )
        selected_comparison = pm_comparison.loc[
            pm_comparison["source"].eq(selected_location[0])
            & pm_comparison["location_id"].eq(selected_location[1])
        ]
        selected_summary = agreement.loc[
            agreement["source"].eq(selected_location[0])
            & agreement["location_id"].eq(selected_location[1])
        ].iloc[0]
        metrics = st.columns(4)
        metrics[0].metric("Giờ trùng", int(selected_summary["overlap_hours"]))
        metrics[1].metric(
            "MAE (µg/m³)", f"{selected_summary['mae']:.1f}"
        )
        metrics[2].metric(
            "RMSE (µg/m³)", f"{selected_summary['rmse']:.1f}"
        )
        correlation = selected_summary["correlation"]
        metrics[3].metric(
            "Tương quan",
            f"{correlation:.3f}" if pd.notna(correlation) else "—",
        )
        chart = selected_comparison.set_index("time")[
            ["cams_pm25", "openaq_pm25"]
        ].rename(columns={
            "cams_pm25": "CAMS (mô hình)",
            "openaq_pm25": "OpenAQ (quan trắc)",
        })
        st.line_chart(
            chart,
            x_label="Giờ đo",
            y_label="PM2.5 (µg/m³)",
            alt="Đối chiếu PM2.5 CAMS và OpenAQ theo giờ",
        )
        st.dataframe(
            agreement.rename(columns={
                "source": "Nguồn",
                "location_id": "Mã trạm",
                "location_name": "Trạm",
                "overlap_hours": "Giờ trùng",
                "mean_bias": "Sai lệch trung bình CAMS - OpenAQ (µg/m³)",
                "mae": "MAE (µg/m³)",
                "rmse": "RMSE (µg/m³)",
                "correlation": "Tương quan",
            }),
            width="stretch",
            hide_index=True,
            alt="Thống kê mức độ đồng thuận PM2.5 theo trạm OpenAQ",
        )
        st.caption(
            "Đây là mức độ đồng thuận với mô hình CAMS, không xác định nguồn "
            "nào đúng tuyệt đối. Chỉ so sánh PM2.5 cùng đơn vị và cùng giờ. "
            "Các nguồn có thể dùng chung thiết bị/nhà cung cấp; không mặc định "
            "là đối chiếu độc lập. WAQI cung cấp AQI thành phần, không phải "
            "nồng độ PM2.5 để ghép trực tiếp."
        )

st.subheader("Theo dõi dự báo đã phát")
history_path = PROJECT_DIR / "figures" / "forecast_history.csv"
if not history_path.is_file():
    st.info(
        "Chưa có lịch sử. Tác vụ theo giờ sẽ tạo "
        "`figures/forecast_history.csv`."
    )
else:
    history = read_csv(
        "figures/forecast_history.csv",
        (
            "generated_at", "target_time", "horizon_hours", "status",
            "probability_aqi_above_150", "model_source_aqi",
            "verification_status", "model_prediction_correct",
            "station_mean_aqi", "station_count",
        ),
    )
    verified = history.loc[
        history["verification_status"] == "VERIFIED_MODEL"
    ].copy()
    if verified.empty:
        st.info("Chưa có dự báo đến mốc xác minh.")
    else:
        verified["model_prediction_correct"] = pd.to_numeric(
            verified["model_prediction_correct"], errors="coerce"
        )
        verified["station_prediction_correct"] = pd.to_numeric(
            verified["station_prediction_correct"], errors="coerce"
        )
        station_verified = verified.dropna(
            subset=["station_mean_aqi", "station_count"]
        )
        metrics = st.columns(4)
        metrics[0].metric("Đã đối chiếu CAMS", len(verified))
        accuracy = verified["model_prediction_correct"].mean()
        metrics[1].metric(
            "Đúng lớp theo CAMS",
            f"{accuracy:.1%}" if pd.notna(accuracy) else "—",
        )
        metrics[2].metric(
            "Mốc có quan trắc WAQI",
            f"{len(station_verified)} / {len(verified)}",
        )
        station_accuracy = station_verified[
            "station_prediction_correct"
        ].mean()
        metrics[3].metric(
            "Đúng lớp theo WAQI",
            f"{station_accuracy:.1%}" if pd.notna(station_accuracy) else "—",
        )
        st.caption(
            "CAMS/Open-Meteo không phải xác minh độc lập; WAQI chỉ tính tại "
            "mốc có số đo trùng giờ và cỡ mẫu nhỏ có thể làm tỷ lệ biến động."
        )
    history["generated_at"] = pd.to_datetime(
        history["generated_at"], errors="coerce"
    )
    display = history.sort_values(
        "generated_at", ascending=False
    ).head(100).rename(columns={
        "generated_at": "Lúc phát",
        "source_time": "Giờ đầu vào",
        "target_time": "Giờ đích",
        "horizon_hours": "Chân trời (giờ)",
        "status": "Dự báo",
        "probability_aqi_above_150": "Xác suất AQI > 150",
        "model_source_aqi": "AQI CAMS tại giờ đích",
        "model_prediction_correct": "Đúng theo CAMS",
        "station_mean_aqi": "AQI WAQI TB tại giờ đích",
        "station_count": "Số trạm",
        "verification_status": "Xác minh",
    })
    columns = [
        "Lúc phát", "Giờ đích", "Chân trời (giờ)", "Dự báo",
        "Xác suất AQI > 150", "AQI CAMS tại giờ đích",
        "AQI WAQI TB tại giờ đích", "Số trạm", "Xác minh",
    ]
    st.dataframe(
        display[columns],
        width="stretch",
        hide_index=True,
        alt="Lịch sử phát và xác minh các dự báo AQI",
    )

st.subheader("Phân tích chuyên sâu")
analysis_kind = st.selectbox(
    "Chọn nội dung phân tích",
    [
        "Ngày bất thường",
        "Phân cụm ngày",
        "Luật kết hợp",
        "Giải thích mô hình",
    ],
    key="advanced_analysis_kind",
)

if analysis_kind == "Ngày bất thường":
    anomalies = read_optional_csv(
        "clean/daily_anomalies.csv",
        ("date", "pm25_mean", "score", "bat_thuong", "loai"),
        command="python anomaly_detect.py",
    )
    if anomalies is not None:
        anomalies["date"] = pd.to_datetime(anomalies["date"], errors="coerce")
        anomalies["pm25_mean"] = pd.to_numeric(
            anomalies["pm25_mean"], errors="coerce"
        )
        anomalies["score"] = pd.to_numeric(anomalies["score"], errors="coerce")
        flagged = anomalies.loc[
            anomalies["bat_thuong"].astype(str).str.lower().isin(["true", "1"])
        ].copy()
        metric_a, metric_b = st.columns(2)
        metric_a.metric("Số ngày trong phân tích", len(anomalies))
        metric_b.metric("Ngày bị đánh dấu bất thường", len(flagged))
        if flagged.empty:
            st.info("Không có ngày nào bị đánh dấu trong kết quả hiện tại.")
        else:
            st.scatter_chart(
                flagged,
                x="date",
                y="pm25_mean",
                color="loai",
                alt="Các ngày bất thường theo PM2.5 trung bình",
            )
            display = flagged.sort_values(
                "score", ascending=False
            ).head(20).rename(columns={
                "date": "Ngày",
                "pm25_mean": "PM2.5 trung bình",
                "pm25_max": "PM2.5 cao nhất",
                "score": "Điểm bất thường",
                "loai": "Nhóm",
                "gio_cao_bat_thuong": "Số giờ bất thường",
            })
            columns = [
                column for column in [
                    "Ngày", "PM2.5 trung bình", "PM2.5 cao nhất",
                    "Điểm bất thường", "Nhóm", "Số giờ bất thường",
                ] if column in display
            ]
            st.dataframe(
                display[columns],
                width="stretch",
                hide_index=True,
                alt="Hai mươi ngày có điểm bất thường cao nhất",
            )
        st.caption(
            "Isolation Forest đánh dấu ngày khác biệt so với tập dữ liệu; kết quả "
            "không xác định nguyên nhân ô nhiễm."
        )
        st.code("python anomaly_detect.py", language="powershell")

elif analysis_kind == "Phân cụm ngày":
    clusters = read_optional_csv(
        "clean/daily_clusters.csv",
        ("date", "pm25_mean", "pm25_max", "cum"),
        command="python cluster.py",
    )
    if clusters is not None:
        clusters["date"] = pd.to_datetime(clusters["date"], errors="coerce")
        clusters["cum"] = clusters["cum"].astype(str)
        st.scatter_chart(
            clusters,
            x="pm25_mean",
            y="pm25_max",
            color="cum",
            alt="Phân cụm ngày theo PM2.5 trung bình và cao nhất",
        )
        numeric_columns = [
            column for column in [
                "pm25_mean", "pm25_max", "temp", "humidity",
                "rain_log", "wind", "pressure",
            ] if column in clusters
        ]
        profiles = clusters.groupby("cum")[numeric_columns].mean()
        profiles.insert(0, "Số ngày", clusters.groupby("cum").size())
        profiles.index.name = "Cụm"
        st.dataframe(
            profiles.reset_index().round(2),
            width="stretch",
            hide_index=True,
            alt="Đặc điểm trung bình của từng cụm ngày",
        )
        st.caption(
            "Mã cụm chỉ là nhãn kỹ thuật, không phải mức chất lượng không khí "
            "hay thứ hạng; cụm mô tả các ngày có đặc trưng tương tự."
        )
        st.code("python cluster.py", language="powershell")

elif analysis_kind == "Luật kết hợp":
    rules = read_optional_csv(
        "figures/association_rules.csv",
        ("antecedents", "consequents", "support", "confidence", "lift"),
        command="python apriori_rules.py",
    )
    if rules is not None:
        targets = sorted(rules["consequents"].dropna().astype(str).unique())
        if not targets:
            st.info("Tệp kết quả chưa có luật kết hợp.")
        else:
            default_target = (
                "AQI xấu+" if "AQI xấu+" in targets else targets[0]
            )
            target = st.selectbox(
                "Mức AQI cần xem",
                targets,
                index=targets.index(default_target),
                key="association_target",
            )
            min_confidence = st.slider(
                "Độ tin cậy tối thiểu",
                min_value=0.0,
                max_value=1.0,
                value=0.5,
                step=0.05,
                key="association_confidence",
            )
            filtered = rules.loc[
                rules["consequents"].astype(str).eq(target)
                & pd.to_numeric(rules["confidence"], errors="coerce").ge(
                    min_confidence
                )
            ].sort_values(["lift", "confidence"], ascending=False)
            if filtered.empty:
                st.info("Không có luật nào thỏa bộ lọc hiện tại.")
            else:
                st.bar_chart(
                    filtered.head(10).set_index("antecedents")[["lift"]],
                    x_label="Điều kiện",
                    y_label="Lift",
                    alt="Top luật kết hợp theo hệ số lift",
                )
                st.dataframe(
                    filtered.head(50),
                    width="stretch",
                    hide_index=True,
                    alt="Các luật kết hợp đạt ngưỡng độ tin cậy",
                )
        st.caption(
            "Support là tỷ lệ xuất hiện, confidence là tỷ lệ kết quả trong các "
            "trường hợp thỏa điều kiện, lift so với mức nền. Luật kết hợp không "
            "chứng minh quan hệ nhân quả."
        )
        st.code("python apriori_rules.py", language="powershell")

else:
    explanation = read_optional_csv(
        "figures/explain_results.csv",
        ("perm_mae_reg24", "perm_auc_cls24", "logit_coef"),
        command="python explain_model.py",
    )
    if explanation is not None:
        feature_column = (
            "Unnamed: 0" if "Unnamed: 0" in explanation.columns else None
        )
        if feature_column is None:
            st.error("Tệp giải thích thiếu cột tên đặc trưng.")
        else:
            explanation = explanation.rename(
                columns={feature_column: "feature"}
            )
            feature_labels = {
                "aqi_lag0": "AQI hiện tại",
                "aqi_lag1": "AQI trước 1 giờ",
                "aqi_lag2": "AQI trước 2 giờ",
                "aqi_lag3": "AQI trước 3 giờ",
                "aqi_lag6": "AQI trước 6 giờ",
                "aqi_lag12": "AQI trước 12 giờ",
                "aqi_lag24": "AQI trước 24 giờ",
                "aqi_lag48": "AQI trước 48 giờ",
                "pm25_now": "PM2.5 hiện tại",
                "aqi_roll6": "AQI trung bình 6 giờ",
                "aqi_roll24": "AQI trung bình 24 giờ",
                "temp": "Nhiệt độ",
                "humidity": "Độ ẩm",
                "precipitation": "Lượng mưa",
                "wind_speed": "Tốc độ gió",
                "pressure": "Áp suất",
                "hour_sin": "Giờ sin",
                "hour_cos": "Giờ cos",
                "month": "Tháng",
                "dow": "Thứ trong tuần",
            }
            explanation["feature_label"] = explanation["feature"].map(
                feature_labels
            ).fillna(explanation["feature"])
            for column in [
                "perm_mae_reg24", "perm_auc_cls24", "logit_coef",
            ]:
                explanation[column] = pd.to_numeric(
                    explanation[column], errors="coerce"
                )
            reg_importance = explanation.nlargest(10, "perm_mae_reg24")
            cls_importance = explanation.nlargest(10, "perm_auc_cls24")
            importance_a, importance_b = st.columns(2)
            with importance_a:
                st.markdown("**Dự báo PM2.5 trước 24 giờ**")
                st.bar_chart(
                    reg_importance.set_index("feature_label")[
                        ["perm_mae_reg24"]
                    ],
                    x_label="Đặc trưng",
                    y_label="MAE tăng khi xáo trộn",
                    alt="Tầm quan trọng hoán vị cho dự báo PM2.5",
                )
            with importance_b:
                st.markdown("**Phân loại AQI xấu trước 24 giờ**")
                st.bar_chart(
                    cls_importance.set_index("feature_label")[
                        ["perm_auc_cls24"]
                    ],
                    x_label="Đặc trưng",
                    y_label="AUC giảm khi xáo trộn",
                    alt="Tầm quan trọng hoán vị cho phân loại AQI",
                )
            st.dataframe(
                explanation.rename(columns={
                    "feature_label": "Đặc trưng",
                    "perm_mae_reg24": "MAE tăng khi xáo trộn",
                    "perm_auc_cls24": "AUC giảm khi xáo trộn",
                    "logit_coef": "Hệ số logistic",
                })[
                    [
                        "Đặc trưng", "MAE tăng khi xáo trộn",
                        "AUC giảm khi xáo trộn", "Hệ số logistic",
                    ]
                ].round(4),
                width="stretch",
                hide_index=True,
                alt="Kết quả giải thích các đặc trưng mô hình",
            )
            st.caption(
                "Permutation importance được tính trên tập test; giá trị âm có "
                "thể là nhiễu. Hệ số logistic cho biết chiều liên hệ trong mô "
                "hình, không chứng minh quan hệ nhân quả."
            )
            st.code("python explain_model.py", language="powershell")
