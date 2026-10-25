CREATE TABLE IF NOT EXISTS dim_time (
    time_key      INT PRIMARY KEY,          -- dạng YYYYMMDDHH
    full_datetime TIMESTAMP NOT NULL,
    date          DATE NOT NULL,
    year          SMALLINT, month SMALLINT, day SMALLINT, hour SMALLINT,
    dow           SMALLINT,                 -- 0 = Thứ Hai
    dow_name      VARCHAR(10),
    season        VARCHAR(10),
    is_weekend    BOOLEAN,
    is_rush_hour  BOOLEAN                   -- 7-9h và 17-19h
);

CREATE TABLE IF NOT EXISTS dim_station (
    station_key  SERIAL PRIMARY KEY,
    station_uid  VARCHAR(30) UNIQUE NOT NULL,
    station_name VARCHAR(200),
    area         VARCHAR(100),              -- khu vực
    lat          DOUBLE PRECISION,
    lon          DOUBLE PRECISION,
    is_model     BOOLEAN DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS dim_source (
    source_key  SERIAL PRIMARY KEY,
    source_name VARCHAR(50) UNIQUE NOT NULL,
    source_type VARCHAR(30)                 -- 'Đo trực tiếp' / 'Mô hình'
);

CREATE TABLE IF NOT EXISTS dim_aqi_level (
    aqi_level_key SMALLINT PRIMARY KEY,
    level_name    VARCHAR(20) UNIQUE NOT NULL,
    aqi_min       INT, aqi_max INT,
    color_hex     VARCHAR(7)
);

CREATE TABLE IF NOT EXISTS fact_air_quality (
    time_key      INT NOT NULL REFERENCES dim_time,
    station_key   INT NOT NULL REFERENCES dim_station,
    source_key    INT NOT NULL REFERENCES dim_source,
    aqi_level_key SMALLINT REFERENCES dim_aqi_level,
    aqi           REAL,
    pm25          REAL,
    pm10          REAL,
    temp          REAL,
    humidity      REAL,
    precipitation REAL,
    wind_speed    REAL,
    wind_dir      REAL,
    pressure      REAL,
    PRIMARY KEY (time_key, station_key, source_key)
);

CREATE INDEX IF NOT EXISTS idx_fact_station ON fact_air_quality(station_key);
CREATE INDEX IF NOT EXISTS idx_fact_level ON fact_air_quality(aqi_level_key);

INSERT INTO dim_source (source_name, source_type) VALUES
  ('WAQI', 'Đo trực tiếp'),
  ('Open-Meteo CAMS', 'Mô hình')
ON CONFLICT (source_name) DO NOTHING;

INSERT INTO dim_aqi_level VALUES
  (1,'Tốt',0,50,'#00E400'), (2,'Trung bình',51,100,'#FFFF00'),
  (3,'Kém',101,150,'#FF7E00'), (4,'Xấu',151,200,'#FF0000'),
  (5,'Rất xấu',201,300,'#8F3F97'), (6,'Nguy hại',301,500,'#7E0023')
ON CONFLICT (aqi_level_key) DO NOTHING;