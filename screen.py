import datetime, glob, os, re, subprocess, sys, time
import numpy as np
import pandas as pd

SELL_TH = 8000     # 売り残の枯渇ライン（億円）
FETCH_MIN = 1.0    # 株価を取得する最小の信用残合計（億円）
LIQ_MIN = 10.0     # 判定の対象にする信用残合計（億円）。これ未満は「対象外（信用残小）」
CHUNK = 150
ENTRY_LAG = 2      # 検証の買値: 申込日から数えてこの営業日後の「始値」（公表が申込日の翌営業日以降のため保守的に2）
HORIZONS = (1, 5, 10, 20)   # 買値からの営業日数（買値の日を1日目とした終値で測る）
BENCH = "1306.T"   # 比較用のTOPIX連動ETF

def num(s):
    s = s.strip()
    if s in ("*", "-", ""): return None
    neg = "▲" in s
    s = s.replace("▲", "").replace(",", "").replace("%", "").strip()
    try: v = float(s)
    except ValueError: return None
    return -v if neg else v

def parse_pdf(path):
    text = subprocess.run(["pdftotext", "-layout", path, "-"], capture_output=True).stdout.decode("utf-8", "replace")
    R = {}
    for line in text.split("\n"):
        for key, tag in (("株数 Shs.", "sh"), ("金額 Val.", "val")):
            if key not in line: continue
            head, tail = line.split(key, 1)
            f = [x for x in re.split(r"\s{2,}", tail.strip()) if x]
            m = re.search(r"(?<![0-9A-Z])(\d{4}[0-9A-Z])(?![0-9A-Z])", head)
            if not m or len(f) < 14: continue
            parts = [x for x in re.split(r"\s{2,}", head.strip()) if x]
            r = R.setdefault(m.group(1), {})
            if tag == "sh":
                r["name"] = parts[1].replace(" 普通株式", "") if len(parts) > 1 else ""
                mk = next((re.match(r"(プライム|スタンダード|グロース)", p) for p in parts if re.match(r"(プライム|スタンダード|グロース)", p)), None)
                r["mk"] = mk.group(1) if mk else ""
            r[tag] = [num(x) for x in f[:14]]
    rows = []
    for c, r in R.items():
        if not r.get("mk") or "sh" not in r or "val" not in r: continue
        s, v = r["sh"], r["val"]
        sell, buy = s[0] or 0, s[3] or 0
        rows.append(dict(code=c[:4] if c[4] == "0" else c, name=r["name"], market=r["mk"],
                         ratio=(buy / sell if sell > 0 else 99.0),
                         buy_oku=(v[3] or 0) / 1e8, sell_oku=(v[0] or 0) / 1e8))
    return pd.DataFrame(rows)

def tech(close):
    close = close.dropna()
    if len(close) < 210: return None
    ma = {n: close.rolling(n).mean().iloc[-1] for n in (25, 50, 75, 200)}
    win = close.iloc[-250:]
    months = (close.index[-1] - win.idxmax()).days / 30.4
    return dict(price=close.iloc[-1], ma25=ma[25], ma50=ma[50], ma75=ma[75], ma200=ma[200], months_from_high=months)

def score(r):
    ratio = r["ratio"]
    a = 2 if ratio <= 1 else 1 if ratio <= 3 else 0 if ratio <= 5 else -1 if ratio <= 10 else -2
    h = 1 if r["months_from_high"] >= 6 else (-1 if ratio > 3 else 0)
    p, m25, m50, m75, m200 = r["price"], r["ma25"], r["ma50"], r["ma75"], r["ma200"]
    if m25 > m50 > m75 > m200: c = 2
    elif m50 > m200 and p > m200: c = 1
    elif m25 < m50 < m75: c = -2
    elif p < m200: c = -1
    else: c = 0
    return a + h + c

def position(m):
    return "高値圏" if m < 1 else ("高値から6か月以上" if m >= 6 else "高値から6か月未満")

def verdict(r, th):
    if r["buy_oku"] + r["sell_oku"] < LIQ_MIN: return "対象外（信用残小）"
    if r["score"] >= th:
        return {"高値圏": "買い候補（勢い型）", "高値から6か月以上": "買い候補（需給改善型）"}.get(r["position"], "買い候補（中間）")
    if r["score"] <= -3: return "回避・戻り売り"
    return "様子見"

def build(df, tdf, th):
    res = df.merge(tdf, on="code")
    res["score"] = res.apply(score, axis=1)
    res["position"] = res.months_from_high.apply(position)
    res["verdict"] = res.apply(lambda r: verdict(r, th), axis=1)
    res["total_oku"] = res.buy_oku + res.sell_oku
    return res.sort_values(["score", "total_oku"], ascending=[False, False])

COLS = ["code", "name", "market", "score", "verdict", "position", "ratio", "buy_oku", "sell_oku", "total_oku",
        "price", "ma25", "ma50", "ma75", "ma200", "months_from_high"]
JP = ["コード", "名称", "市場", "スコア", "判定", "位置づけ", "信用倍率", "買い残(億円)", "売り残(億円)", "信用残合計(億円)",
      "株価", "25日線", "50日線", "75日線", "200日線", "高値からの月数"]

def norm(df):
    df = df.copy()
    idx = pd.to_datetime(df.index)
    if getattr(idx, "tz", None) is not None: idx = idx.tz_convert(None)
    df.index = idx.normalize()
    return df

def fetch(codes):
    import yfinance as yf
    out = {}
    for i in range(0, len(codes), CHUNK):
        chunk = codes[i:i + CHUNK]
        tk = [c + ".T" for c in chunk]
        px = None
        for _ in range(3):
            try:
                px = yf.download(tk, period="2y", auto_adjust=True, group_by="ticker", progress=False, threads=True)
                break
            except Exception as e:
                print("retry", e, flush=True); time.sleep(10)
        if px is None: continue
        for c, t in zip(chunk, tk):
            try: d = px[t][["Open", "Close"]].dropna()
            except Exception: continue
            if len(d): out[c] = norm(d)
        time.sleep(3)
    return out

def fetch_bench():
    import yfinance as yf
    b = yf.download(BENCH, period="2y", auto_adjust=True, progress=False)
    if isinstance(b.columns, pd.MultiIndex): b.columns = b.columns.get_level_values(0)
    return norm(b[["Open", "Close"]].dropna())

def forward(d, date, bench):
    """申込日 date のシグナルに対し、ENTRY_LAG営業日後の始値で買った場合の、その後の騰落率(%)と、TOPIX ETFに対する超過(%)"""
    pos = d.index.searchsorted(date, side="right") - 1
    e = pos + ENTRY_LAG
    if pos < 0 or e >= len(d): return {}
    entry = d["Open"].iloc[e]
    res = {"買値日": d.index[e].date().isoformat(), "買値": entry}
    b0 = bench["Open"].get(d.index[e]) if bench is not None else None
    for h in HORIZONS:
        k = e + h - 1
        if k >= len(d): continue
        r = d["Close"].iloc[k] / entry - 1
        res[f"{h}日後%"] = r * 100
        b1 = bench["Close"].get(d.index[k]) if bench is not None else None
        if b0 is not None and b1 is not None and pd.notna(b0) and pd.notna(b1):
            res[f"{h}日後_超過%"] = (r - (b1 / b0 - 1)) * 100
    return res

def agg(g):
    out = {"件数": len(g)}
    for h in HORIZONS:
        r, x = g[f"{h}日後%"] if f"{h}日後%" in g else None, g[f"{h}日後_超過%"] if f"{h}日後_超過%" in g else None
        n = int(r.notna().sum()) if r is not None else 0
        out[f"{h}日後_件数"] = n
        out[f"{h}日後_平均%"] = round(r.mean(), 2) if n else None
        out[f"{h}日後_勝率%"] = round((r > 0).sum() / n * 100, 1) if n else None
        out[f"{h}日後_超過平均%"] = round(x.mean(), 2) if x is not None and x.notna().sum() else None
    return out

def summarize(fw):
    rows = []
    for v, g in fw.groupby("判定"):
        rows.append({"日付": "全期間", "判定": v, **agg(g)})
    for (d, v), g in fw.groupby(["日付", "判定"]):
        rows.append({"日付": d, "判定": v, **agg(g)})
    return pd.DataFrame(rows)

def process(parsed, prices, bench):
    os.makedirs("history", exist_ok=True)
    logs, fw_rows, latest = [], [], None
    for date in sorted(parsed):
        df = parsed[date]
        sell_total, buy_total = df.sell_oku.sum(), df.buy_oku.sum()
        th = 3 + (1 if sell_total < SELL_TH else 0)
        df = df[(df.buy_oku + df.sell_oku) >= FETCH_MIN].reset_index(drop=True)
        ts = pd.Timestamp(date)
        rows = []
        for c in df.code:
            d = prices.get(c)
            if d is None: continue
            t = tech(d["Close"][d.index <= ts])
            if t: rows.append(dict(code=c, **t))
        if not rows:
            print(date, "株価を取得できず、スキップ"); continue
        res = build(df, pd.DataFrame(rows), th)
        table = res[COLS].round(2); table.columns = JP
        table.to_csv(f"history/result_{date}.csv", index=False, encoding="utf-8-sig")
        vc = res.verdict.value_counts()
        logs.append(dict(日付=date, 売り残億円=round(sell_total), 買い残億円=round(buy_total), 倍率=round(buy_total / sell_total, 2),
                         買い候補_需給改善型=int(vc.get("買い候補（需給改善型）", 0)), 買い候補_勢い型=int(vc.get("買い候補（勢い型）", 0)),
                         買い候補_中間=int(vc.get("買い候補（中間）", 0)), 回避=int(vc.get("回避・戻り売り", 0))))
        for r in res[res.verdict != "対象外（信用残小）"].itertuples():
            f = forward(prices[r.code], ts, bench)
            if f: fw_rows.append(dict(日付=date, コード=r.code, 名称=r.name, 判定=r.verdict, スコア=r.score, **f))
        latest = (date, res, table, sell_total, buy_total, th, vc, len(df))
    if latest is None: sys.exit("結果を作れませんでした")
    date, res, table, sell_total, buy_total, th, vc, nfetch = latest
    table.to_csv("result.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(logs).to_csv("history/summary_log.csv", index=False, encoding="utf-8-sig")
    with open("summary.txt", "w", encoding="utf-8") as f:
        f.write(f"最新の申込日: {date}\n普通株 信用売り残 {sell_total:.0f}億円 / 買い残 {buy_total:.0f}億円 / 倍率 {buy_total/sell_total:.2f}\n")
        f.write(f"売り残{SELL_TH}億円未満(燃料枯渇): {sell_total < SELL_TH}  買い候補の閾値: {th}点以上\n株価を取得できた銘柄: {len(res)} / {nfetch}\n")
        f.write("判定の内訳:\n" + vc.to_string() + "\n")
    if fw_rows:
        fw = pd.DataFrame(fw_rows)
        fw.round(2).to_csv("history/forward_detail.csv", index=False, encoding="utf-8-sig")
        summarize(fw).to_csv("history/forward_summary.csv", index=False, encoding="utf-8-sig")

def main():
    items = {}
    for p in sorted(glob.glob("data/*.pdf")):
        m = re.match(r"(\d{8})", os.path.basename(p))
        if m: items[m.group(1)] = p
        else: print("日付で始まらないファイル名のため無視:", p)
    if not items: sys.exit("data/ に 日付8桁で始まるPDFがありません")
    parsed = {d: parse_pdf(p) for d, p in items.items()}
    for d, df in parsed.items(): print(d, "銘柄数", len(df), flush=True)
    codes = sorted({c for df in parsed.values() for c in df[(df.buy_oku + df.sell_oku) >= FETCH_MIN].code})
    prices = fetch(codes)
    try: bench = fetch_bench()
    except Exception as e:
        print("ベンチマーク取得失敗:", e); bench = None
    process(parsed, prices, bench)

if __name__ == "__main__":
    main()
