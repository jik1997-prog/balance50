import json, os, datetime
os.makedirs("data", exist_ok=True)
json.dump({"updated": datetime.datetime.utcnow().isoformat() + "Z", "stocks": []},
          open("data/stocks.json", "w"), ensure_ascii=False)
print("ok")
