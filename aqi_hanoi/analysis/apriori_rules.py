import numpy as np, pandas as pd
from mlxtend.frequent_patterns import apriori, association_rules

df = pd.read_csv("clean/fact_model_hourly.csv", parse_dates=["time"])
df = df.dropna(subset=["aqi", "wind_speed", "humidity", "temp", "pressure", "precipitation"])

# ---- 1. Rời rạc hoá thành các "item" ----
cats = pd.DataFrame({
    "gio":   pd.cut(df.wind_speed, [-1, 5, 10, 1000],
                    labels=["gió yếu (<5)", "gió vừa (5-10)", "gió mạnh (>=10)"]),
    "am":    pd.cut(df.humidity, [-1, 60, 80, 101],
                    labels=["ẩm thấp (<60)", "ẩm vừa (60-80)", "ẩm cao (>80)"]),
    "mua":   np.where(df.precipitation > 0.1, "có mưa", "không mưa"),
    "nhiet": pd.qcut(df.temp, 3, labels=["nhiệt thấp", "nhiệt vừa", "nhiệt cao"]),
    "ap":    pd.qcut(df.pressure, 3, labels=["áp suất thấp", "áp suất vừa", "áp suất cao"]),
    "khung": pd.cut(df.hour, [-1, 5, 10, 16, 23],
                    labels=["đêm (0-5h)", "sáng (6-10h)", "trưa-chiều (11-16h)", "tối (17-23h)"]),
    "mevua": df.season.values,
    "tuan":  np.where(df.dow >= 5, "cuối tuần", "ngày thường"),
    "aqi":   pd.cut(df.aqi, [-1, 100, 150, 1000],
                    labels=["AQI tốt-TB", "AQI kém", "AQI xấu+"]),
})
onehot = pd.get_dummies(cats, prefix="", prefix_sep="").astype(bool)
print("Số giao dịch:", len(onehot), "| số item:", onehot.shape[1])
print("\nTỷ lệ nền của mức AQI:\n", onehot[["AQI tốt-TB", "AQI kém", "AQI xấu+"]].mean().round(3))

# ---- 2. Apriori + luật ----
fi = apriori(onehot, min_support=0.03, use_colnames=True, max_len=4)
try:
    rules = association_rules(fi, num_itemsets=len(onehot), metric="lift", min_threshold=1.0)
except TypeError:                                   # phiên bản mlxtend cũ
    rules = association_rules(fi, metric="lift", min_threshold=1.0)

is_aqi = lambda s: any(i.startswith("AQI") for i in s)
rules = rules[rules.consequents.apply(lambda s: len(s) == 1 and is_aqi(s))
              & ~rules.antecedents.apply(is_aqi)].copy()
rules["antecedents"] = rules.antecedents.apply(lambda s: " + ".join(sorted(s)))
rules["consequents"] = rules.consequents.apply(lambda s: next(iter(s)))
cols = ["antecedents", "consequents", "support", "confidence", "lift"]
rules = rules[cols].round(3)

# ---- 3. In top luật cho từng mức AQI ----
for target in ["AQI xấu+", "AQI tốt-TB"]:
    r = rules[(rules.consequents == target) & (rules.confidence >= 0.5)]
    print(f"\n===== TOP 10 LUẬT DẪN TỚI: {target} (sắp theo lift) =====")
    print(r.sort_values("lift", ascending=False).head(10).to_string(index=False))

rules.sort_values("lift", ascending=False).to_csv("figures/association_rules.csv", index=False)
print("\nĐã lưu figures/association_rules.csv")