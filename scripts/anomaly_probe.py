import time
from HarbourOS.anomalies import find_anomalies
from HarbourOS.storage import connect
import pandas as pd
pd.set_option("display.width", 250); pd.set_option("display.max_colwidth", 160)
t = time.time()
f = find_anomalies(connect())
print("seconds", round(time.time() - t, 1))
print(f["kind"].value_counts())
for kind, g in f.groupby("kind"):
    print("==", kind)
    for r in g.sort_values("score", ascending=False).head(10).itertuples():
        print(f"  {r.mmsi} {r.score} {r.started_at} | {r.reason}")
    print("  median score", g["score"].median())
