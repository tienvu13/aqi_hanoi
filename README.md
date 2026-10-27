# Phân tích chất lượng không khí Hà Nội

Dự án thu thập, làm sạch, phân tích và dự báo chất lượng không khí tại Hà Nội. Dữ liệu đến từ CAMS/Open-Meteo, WAQI và một số nguồn quan trắc bổ sung. Kết quả được lưu thành tệp CSV, hiển thị bằng Streamlit và có thể nạp vào PostgreSQL.

## Cấu trúc dự án

```text
aqi_hanoi/
├── aqi_hanoi/
│   ├── collectors/  Thu thập dữ liệu từ các nguồn
│   ├── core/        Cấu hình, HTTP clients và tiện ích dùng chung
│   ├── pipeline/    ETL, kiểm tra chất lượng và lịch chạy
│   ├── analysis/    Phân tích, độ phủ trạm và mô hình
│   ├── alerts/      Tạo cảnh báo và thông báo
│   ├── database/    Nạp kho PostgreSQL và truy vấn EDA
│   └── dashboard/  Hàm dùng chung cho dashboard
├── app_pages/       Các trang Streamlit
├── clean/           Dữ liệu đã chuẩn hóa
├── figures/         Biểu đồ, báo cáo và trạng thái pipeline
├── notebooks/       Notebook khám phá dữ liệu
├── raw/             Dữ liệu thu thập và log
├── sql/             Schema cơ sở dữ liệu
├── tests/           Kiểm thử offline
├── dashboard.py     Entry point Streamlit
└── README.md
```

Chạy các module bằng `python -m aqi_hanoi.<nhóm>.<tên_module>`. Các thư mục dữ liệu `raw/`, `clean/` và `figures/` nằm ở gốc dự án.

## 1. Yêu cầu

- Python 3.11 trở lên
- Internet khi thu thập dữ liệu
- Token WAQI để lấy dữ liệu trạm
- API key OpenAQ nếu muốn dùng nguồn OpenAQ
- PostgreSQL và `DATABASE_URL` chỉ cần khi chạy module trong `aqi_hanoi.database`

## 2. Cài đặt trên Windows

Mở PowerShell tại thư mục dự án:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

### Cấu hình khóa API

Đặt token cho phiên PowerShell hiện tại:

```powershell
$env:WAQI_TOKEN = "token-WAQI-cua-ban"
$env:OPENAQ_API_KEY = "api-key-OpenAQ-cua-ban"
```

- `WAQI_TOKEN`: cần cho việc thu thập dữ liệu WAQI.
- `OPENAQ_API_KEY`: tùy chọn, chỉ cần khi sử dụng OpenAQ.

Bạn cũng có thể lưu token WAQI mới trong tệp `waqi_token.txt` ở thư mục gốc dự án. Tệp này đã nằm trong `.gitignore`. Không ghi token trực tiếp vào mã Python và không đưa token hoặc mật khẩu lên kho mã nguồn. Nếu thông tin bí mật đã bị chia sẻ, hãy thu hồi hoặc đổi khóa.

### Cấu hình PostgreSQL (tùy chọn)

Chỉ cần cấu hình nếu chạy các module trong `aqi_hanoi.database`. Thay thông tin
bên dưới bằng thông tin kết nối PostgreSQL của bạn:

```powershell
$env:DATABASE_URL = "postgresql+psycopg2://USER:PASSWORD@HOST:5432/DATABASE"
```

## 3. Chạy pipeline

Sau khi kích hoạt môi trường ảo và cấu hình token WAQI, chạy toàn bộ pipeline:

```powershell
.\run_pipeline.ps1
```

Script sẽ dừng khi một bước gặp lỗi. Nếu cần chạy riêng từng bước, thực hiện theo thứ tự:

```powershell
python -m aqi_hanoi.collectors.collect_aq_model
python -m aqi_hanoi.collectors.collect_weather
python -m aqi_hanoi.collectors.collect_waqi
python -m aqi_hanoi.collectors.collect_waqi_feed
python -m aqi_hanoi.pipeline.etl
python -m aqi_hanoi.pipeline.etl_feed
python -m aqi_hanoi.collectors.collect_optional_sources
python -m aqi_hanoi.pipeline.data_quality
python -m aqi_hanoi.alerts.generate_alerts
python -m aqi_hanoi.analysis.compare_stations
```

### Pipeline thực hiện những gì?

1. Thu thập dữ liệu mô hình chất lượng không khí CAMS và dữ liệu thời tiết Open-Meteo.
2. Thu thập dữ liệu AQI từ WAQI và xử lý dữ liệu trạm theo giờ.
3. Thử cập nhật các nguồn bổ sung.
4. Làm sạch, chuẩn hóa dữ liệu và kiểm tra chất lượng.
5. Tạo cảnh báo cục bộ và so sánh dữ liệu CAMS–WAQI.

Các collector Open-Meteo cập nhật dữ liệu gần đây và giữ lại lịch sử đã có. Các yêu cầu HTTP có cơ chế thử lại với lỗi mạng, timeout và một số mã lỗi tạm thời.

## 4. Nguồn dữ liệu

### CAMS và Open-Meteo

- CAMS cung cấp dữ liệu chất lượng không khí từ mô hình, không phải phép đo trực tiếp tại trạm.
- Open-Meteo cung cấp dữ liệu thời tiết mô hình lưới tại tọa độ trung tâm, không phải số đo tại từng trạm.
- Dữ liệu được lưu và xử lý theo giờ.

### WAQI

- Cần token WAQI hợp lệ.
- Collector tìm trạm trong khung tọa độ xấp xỉ Hà Nội: `20.55–21.65°N`, `105.28–106.05°E`.
- Chỉ giữ các trạm có tên nhận diện Hà Nội/Hanoi, tọa độ phù hợp, AQI hợp lệ và số đo không quá 3 giờ.
- Mục tiêu là tối đa 10 trạm; số trạm thực tế phụ thuộc dữ liệu công khai của WAQI.
- Trang Trạm WAQI hiển thị các trạm có AQI hợp lệ trong lượt thu thập gần nhất. Dữ liệu cũ vẫn được giữ làm lịch sử.
- Khung tọa độ là vùng xấp xỉ, không phải ranh giới hành chính chính xác.

Nên lên lịch thu thập WAQI mỗi giờ để tích lũy dữ liệu theo thời gian.

### OpenAQ (tùy chọn)

Chạy:

```powershell
python -m aqi_hanoi.collectors.collect_openaq
```

Collector tìm tối đa 10 địa điểm trong vùng tọa độ Hà Nội có phép đo PM2.5/PM10 trong 24 giờ gần nhất. Kết quả được lưu tại:

- `raw/openaq_measurements.csv`
- `clean/openaq_latest.csv`

OpenAQ lưu nồng độ PM riêng, không tự quy đổi hoặc gộp với AQI của WAQI. Các giá trị PM2.5 chỉ được đối chiếu khi đơn vị là `µg/m³`.

### Các nguồn bổ sung không cần API key

```powershell
python -m aqi_hanoi.collectors.collect_hanoi_portal
python -m aqi_hanoi.collectors.collect_sensorcommunity
python -m aqi_hanoi.collectors.collect_noaa_weather
```

- Cổng quan trắc Hà Nội lưu dữ liệu tại `raw/hanoi_portal_pm25.csv`.
- Sensor.Community lưu dữ liệu tại `raw/sensorcommunity_pm25.csv`.
- NOAA Aviation Weather API lấy METAR sân bay Nội Bài (ICAO `VVNB`), lưu tại `raw/noaa_vvnb_metar.csv`.

Các nguồn bổ sung được lưu riêng. Lỗi một nguồn không ngăn các nguồn tiếp theo được thử cập nhật. OpenAQ sẽ được bỏ qua nếu chưa có `OPENAQ_API_KEY`.

Lưu ý:

- Cổng quan trắc Hà Nội dùng endpoint công khai của website; cần tự kiểm tra điều khoản sử dụng trước khi chia sẻ dữ liệu.
- Sensor.Community là mạng cảm biến cộng đồng; độ phủ và hiệu chuẩn có thể khác nhau.
- METAR Nội Bài chỉ đại diện cho trạm sân bay, không đại diện toàn Hà Nội. Gió dùng đơn vị knot, nhiệt độ °C và áp suất hPa.
- Tham khảo [tài liệu NOAA Aviation Weather API](https://aviationweather.gov/data/api/) và [danh mục trạm NOAA](https://www.ncei.noaa.gov/pub/data/noaa/isd-history.csv).

## 5. Thu thập tự động mỗi giờ trên Windows

Chạy lệnh sau một lần để tạo Scheduled Task:

```powershell
.\install_waqi_schedule.ps1
```

Tác vụ bắt đầu ở đầu giờ kế tiếp và ghi log vào `raw/log.txt`. Tác vụ theo giờ cập nhật dữ liệu, chạy ETL, kiểm tra chất lượng, tạo cảnh báo và ghi lịch sử dự báo.

Để xóa lịch chạy tự động:

```powershell
Unregister-ScheduledTask -TaskName "AQI Hanoi WAQI Collection" -Confirm:$false
```

`run_waqi.bat` chạy pipeline theo giờ bằng môi trường `.venv` của dự án.

### Theo dõi trạng thái và log

- `figures/pipeline_status.json`: trạng thái `RUNNING/SUCCEEDED/FAILED`, thời gian chạy và bước lỗi.
- `figures/source_collection_status.json`: trạng thái các nguồn bổ sung.
- `raw/source_collection.log`: log cập nhật các nguồn bổ sung.
- `raw/log.txt`: log pipeline.
- `figures/forecast_history.csv`: lịch sử dự báo.
- `figures/data_quality_report.csv`: báo cáo chất lượng dữ liệu.

Dashboard cảnh báo khi không có lần chạy thành công trong hơn 2 giờ. Hãy xem các dòng `WARN` trong báo cáo chất lượng trước khi diễn giải kết quả.

## 6. Phân tích và dự báo

### Đánh giá CAMS–WAQI

Chạy:

```powershell
python -m aqi_hanoi.analysis.compare_stations
```

Cần tối thiểu 24 giờ dữ liệu trùng nhau giữa CAMS và WAQI. Nếu chưa đủ, script sẽ thông báo và bỏ qua đánh giá thống kê. Kiểm tra mốc thời gian mới nhất trong các tệp `raw/` và `clean/`.

### Đánh giá mô hình và cảnh báo

```powershell
python -m aqi_hanoi.analysis.forecast
python -m aqi_hanoi.analysis.alert_classify
python -m aqi_hanoi.alerts.generate_alerts
```

- `aqi_hanoi.analysis.forecast` và `aqi_hanoi.analysis.alert_classify` đánh giá mô hình offline.
- Việc chọn mô hình/ngưỡng dùng validation năm 2025; tập test bắt đầu từ năm 2026.
- Các kết quả được lưu trong `figures/forecast_results.csv`, `figures/forecast_predictions.csv`, `figures/alert_results.csv` và `figures/alert_thresholds.csv`.
- `aqi_hanoi.alerts.generate_alerts` dự báo nguy cơ AQI vượt 150 trong 6 hoặc 24 giờ, ghi kết quả vào `figures/local_alerts.csv`.
- Nếu dữ liệu đặc trưng mới nhất cũ hơn 6 giờ, script chỉ ghi `DATA_STALE`, không phát hành xác suất/cảnh báo.

Trên Windows, chạy `python -m aqi_hanoi.alerts.notify_alerts` để hiển thị thông báo toast cục bộ khi có `ALERT` và dữ liệu còn mới. Cần đăng nhập Windows và bật thông báo. Cùng một nhóm thời gian dự báo chỉ được thông báo tối đa một lần trong 6 giờ. Đây là cảnh báo cục bộ, không phải dịch vụ cảnh báo chính thức theo thời gian thực.

Các module phân tích khác gồm `aqi_hanoi.analysis.anomaly_detect`, `aqi_hanoi.analysis.cluster`, `aqi_hanoi.analysis.apriori_rules` và `aqi_hanoi.analysis.explain_model`. Chúng sử dụng dữ liệu trong `clean/fact_model_hourly.csv` và lưu kết quả trong `clean/` hoặc `figures/`. Kết quả bất thường, phân cụm và luật kết hợp mang tính mô tả, không chứng minh nguyên nhân.

## 7. Chạy dashboard

```powershell
streamlit run dashboard.py
```

Dashboard có 4 trang:

- **Tổng quan:** trạng thái pipeline, AQI CAMS mới nhất, xu hướng và độ phủ
  dữ liệu; biểu đồ PM2.5 CAMS trung bình theo từng giờ trong ngày, so sánh 4
  mùa (Xuân 3–5, Hạ 6–8, Thu 9–11, Đông 12–2) và PM2.5 trung bình theo
  khoảng tốc độ gió.
- **Trạm WAQI:** tình trạng dữ liệu trạm, bản đồ, lịch sử AQI/PM và phép đo OpenAQ nếu có.
- **Phân tích:** đánh giá offline, so sánh CAMS với WAQI và các nguồn PM2.5 bổ sung.
- **Cảnh báo & báo cáo:** cảnh báo cục bộ, báo cáo chất lượng và tải ZIP báo cáo.

Dashboard thể hiện số đo mới/cũ/thiếu, độ phủ dữ liệu, giờ trùng giữa các nguồn và khoảng thời gian bị thiếu. Có thể chọn nhiều trạm để xem lịch sử; giờ không có dữ liệu sẽ để trống, không nội suy giữa các điểm.

ZIP báo cáo bao gồm dữ liệu tổng hợp CAMS/WAQI, độ phủ trạm, chất lượng dữ liệu, lịch sử dự báo và chuỗi giờ trong 30 ngày gần nhất; không chứa dữ liệu feed thô.

Lưu ý khi đọc kết quả:

- Không xem AQI mô hình là phép đo tức thời tại trạm.
- Không trộn AQI WAQI với nồng độ PM2.5.
- Cần tối thiểu 24 giờ dữ liệu trùng để cân nhắc các chỉ số so sánh; kết quả đồng thuận không chứng minh nguồn nào đúng tuyệt đối.
- Dự báo đối chiếu với CAMS không phải xác minh độc lập vì CAMS cùng họ nguồn với nhãn dùng huấn luyện.
- Hướng dẫn hoạt động ngoài trời theo AQI chỉ mang tính thông tin chung, không thay thế cảnh báo chính thức hoặc tư vấn y tế.

## 8. PostgreSQL

Đặt biến `DATABASE_URL`, sau đó chạy:

```powershell
python -m aqi_hanoi.database.load_dw
```

`aqi_hanoi.database.load_dw` tạo bảng nếu chưa có và nạp/cập nhật dữ liệu theo khóa tự nhiên, giúp có thể chạy lại mà không xóa bảng hoặc tạo bản ghi trùng.

Để đọc dữ liệu từ PostgreSQL và tạo biểu đồ trong `figures/`, chạy:

```powershell
python -m aqi_hanoi.database.eda
```

## 9. Kiểm thử offline

Các kiểm thử không gọi API bên ngoài hoặc cơ sở dữ liệu:

```powershell
python -m unittest discover -s tests -v
```
