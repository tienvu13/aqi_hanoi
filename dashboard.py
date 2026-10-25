import streamlit as st


st.set_page_config(
    page_title="AQI Hà Nội",
    page_icon=":material/air:",
    layout="wide",
)

st.title("Chất lượng không khí Hà Nội")
st.caption(
    "Dashboard cục bộ · phân biệt dữ liệu CAMS, quan trắc WAQI và đánh giá "
    "mô hình."
)

page = st.navigation(
    [
        st.Page(
            "app_pages/overview.py",
            title="Tổng quan",
            icon=":material/dashboard:",
            default=True,
        ),
        st.Page(
            "app_pages/stations.py",
            title="Trạm WAQI",
            icon=":material/location_on:",
        ),
        st.Page(
            "app_pages/analysis.py",
            title="Phân tích",
            icon=":material/analytics:",
        ),
        st.Page(
            "app_pages/alerts_reports.py",
            title="Cảnh báo & báo cáo",
            icon=":material/assessment:",
        ),
    ],
    position="top",
)
page.run()
