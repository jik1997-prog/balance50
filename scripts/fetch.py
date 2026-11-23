"""BALANCE FIFTY 데이터 수집: tickers.json 의 종목을 받아 data/stocks.json 생성.
시세·재무: yfinance(비공식, 무료) / 한국 공시·재무: DART OpenAPI(무료 키)"""
import json, os, sys, time, math, io, zipfile, datetime
import xml.etree.ElementTree as ET
import requests
import yfinance as yf

KEY = os.environ.get("DART_API_KEY", "").strip()
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "data", "stocks.json")
FX = 1370.0
EVENT_WORDS = ["공급계약", "단일판매", "임상", "합병", "인수", "유상증자", "자기주식", "기술이전", "특허", "실적"]

def num(x, nd=2):
    try:
        x = float(x)
        return None if math.isnan(x) or math.isinf(x) else round(x, nd)
    except Exception:
        return None

def hist_stats(t):
    h = t.history(period="1y", auto_adjust=True)["Close"].dropna()
    if len(h) < 30:
        return None
    px, prev = float(h.iloc[-1]), float(h.iloc[-2])
    base = float(h.iloc[-126]) if len(h) > 126 else float(h.iloc[0])
    vol = float(h.pct_change().dropna().std()) * math.sqrt(252) * 100
    return dict(px=px, c=(px / prev - 1) * 100, m6=(px / base - 1) * 100, vol=vol, h=[float(x) for x in h.iloc[-250:]])

def cap_text(cap, cur):
    if not cap:
        return "-", 0
    if cur == "KRW":
        return f"약 {cap / 1e12:,.1f}조원", cap / 1e12
    return f"약 {cap / 1e12:,.2f}조달러", cap / 1e12 * FX

def one_stock(it):
    t = yf.Ticker(it["y"])
    st = hist_stats(t)
    if not st:
        return None
    try:
        info = t.info or {}
    except Exception:
        info = {}
    cap, capkrw = cap_text(info.get("marketCap"), info.get("currency"))
    per = num(info.get("trailingPE"))
    if per is None and (num(info.get("trailingEps")) or 0) < 0:
        per = -1
    roe = num((info.get("returnOnEquity") or 0) * 100) if info.get("returnOnEquity") is not None else None
    dy = info.get("trailingAnnualDividendYield")
    g = info.get("revenueGrowth")
    px = st["px"] if it["m"] == "US" else round(st["px"])
    return {"s": it["s"], "n": it["n"], "m": it["m"], "sec": it["sec"], "px": px,
            "c": num(st["c"]), "cap": cap, "L": 1 if capkrw >= 30 else 0, "per": per,
            "pbr": num(info.get("priceToBook")), "roe": roe, "debt": num(info.get("debtToEquity")),
            "div": num((dy or 0) * 100), "g": num(g * 100) if g is not None else None,
            "vol": num(st["vol"], 1), "m6": num(st["m6"], 1), "ev": 0,
            "ov": (info.get("longBusinessSummary") or "")[:300],
            "h": [round(x) if it["m"] == "KR" else round(x, 2) for x in st["h"]]}

# ---------- DART ----------
def dart_corp_map():
    r = requests.get("https://opendart.fss.or.kr/api/corpCode.xml", params={"crtfc_key": KEY}, timeout=60)
    z = zipfile.ZipFile(io.BytesIO(r.content))
    root = ET.fromstring(z.read(z.namelist()[0]))
    m = {}
    for e in root.iter("list"):
        sc = (e.findtext("stock_code") or "").strip()
        if sc:
            m[sc] = e.findtext("corp_code")
    return m

def dart_filings(corp):
    today = datetime.date.today()
    p = {"crtfc_key": KEY, "corp_code": corp, "bgn_de": (today - datetime.timedelta(days=90)).strftime("%Y%m%d"),
         "end_de": today.strftime("%Y%m%d"), "page_count": 10}
    d = requests.get("https://opendart.fss.or.kr/api/list.json", params=p, timeout=30).json()
    rows = d.get("list", []) if d.get("status") == "000" else []
    recent = (today - datetime.timedelta(days=30)).strftime("%Y%m%d")
    titles = [f"{x['rcept_dt'][:4]}-{x['rcept_dt'][4:6]}-{x['rcept_dt'][6:]} {x['report_nm'].strip()}" for x in rows[:3]]
    ev = any(w in x["report_nm"] for x in rows if x["rcept_dt"] >= recent for w in EVENT_WORDS)
    return titles, ev

def dart_fin(corp):
    y = datetime.date.today().year
    for yr in (y - 1, y - 2):
        p = {"crtfc_key": KEY, "corp_code": corp, "bsns_year": str(yr), "reprt_code": "11011"}
        d = requests.get("https://opendart.fss.or.kr/api/fnlttSinglAcnt.json", params=p, timeout=30).json()
        if d.get("status") != "000":
            continue
        rows = [x for x in d["list"] if x.get("fs_div") == "CFS"] or d["list"]
        def pick(*names):
            for x in rows:
                if x["account_nm"] in names:
                    try:
                        return float(x["thstrm_amount"].replace(",", ""))
                    except Exception:
                        return None
        debt, eq, ni = pick("부채총계"), pick("자본총계"), pick("당기순이익", "당기순이익(손실)", "연결당기순이익")
        if eq and debt is not None and ni is not None:
            return {"debt": num(debt / eq * 100, 1), "roe": num(ni / eq * 100, 1)}
    return {}

def indices():
    spec = [("KOSPI", "^KS11"), ("KOSDAQ", "^KQ11"), ("NASDAQ", "^IXIC"), ("S&P 500", "^GSPC"), ("DOW", "^DJI"),
            ("NIKKEI", "^N225"), ("USD/KRW", "KRW=X"), ("WTI", "CL=F"), ("US10Y", "^TNX")]
    out = []
    for n, y in spec:
        try:
            h = yf.Ticker(y).history(period="5d")["Close"].dropna()
            v, p = float(h.iloc[-1]), float(h.iloc[-2])
            out.append([n, f"{v:,.2f}" + ("%" if n == "US10Y" else ""), round((v / p - 1) * 100, 2)])
        except Exception as e:
            print("index fail", n, e)
    return out

def main():
    items = json.load(open(os.path.join(HERE, "tickers.json"), encoding="utf-8"))
    cmap = {}
    if KEY:
        try:
            cmap = dart_corp_map()
            print("DART corp codes:", len(cmap))
        except Exception as e:
            print("DART corp map fail:", e)
    else:
        print("DART_API_KEY 없음 - 한국 공시/재무 보강을 건너뜁니다")
    stocks = []
    for it in items:
        try:
            s = one_stock(it)
            if not s:
                print("no price:", it["s"]); continue
            corp = cmap.get(it["s"]) if it["m"] == "KR" else None
            if corp:
                try:
                    titles, ev = dart_filings(corp)
                    if titles:
                        s["fil"] = "최근 공시: " + " / ".join(titles)
                    s["ev"] = 1 if ev else 0
                    fin = dart_fin(corp)
                    for k, v in fin.items():
                        if v is not None:
                            s[k] = v
                except Exception as e:
                    print("DART fail", it["s"], e)
            stocks.append(s)
            print("ok", it["s"], it["n"])
        except Exception as e:
            print("fail", it["s"], e)
        time.sleep(1)
    if not stocks:
        print("수집된 종목이 없어 기존 data/stocks.json을 유지합니다")
        sys.exit(1)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump({"updated": datetime.datetime.now(datetime.timezone.utc).isoformat(), "indices": indices(), "stocks": stocks},
              open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
    print("saved", len(stocks))

main()
