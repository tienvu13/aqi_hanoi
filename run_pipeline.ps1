$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$steps = @(
    "aqi_hanoi.collectors.collect_aq_model",
    "aqi_hanoi.collectors.collect_weather",
    "aqi_hanoi.collectors.collect_waqi",
    "aqi_hanoi.collectors.collect_waqi_feed",
    "aqi_hanoi.pipeline.etl",
    "aqi_hanoi.pipeline.etl_feed",
    "aqi_hanoi.collectors.collect_optional_sources",
    "aqi_hanoi.pipeline.data_quality",
    "aqi_hanoi.alerts.generate_alerts",
    "aqi_hanoi.pipeline.forecast_monitoring",
    "aqi_hanoi.analysis.compare_stations"
)

foreach ($step in $steps) {
    Write-Host "`n===== $step ====="
    & python -m $step
    if ($LASTEXITCODE -ne 0) {
        throw "Pipeline thất bại ở $step với mã lỗi $LASTEXITCODE."
    }
}
