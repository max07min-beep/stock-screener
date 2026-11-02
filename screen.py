import glob, re, subprocess, sys, time
import pandas as pd

SELL_TH = 8000      # 売り残の枯渇ライン（億円）
MIN_MARGIN = 1.0    # 買残+売残がこの億円未満の銘柄は除外
CHUNK = 150

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
                         buy=buy, sell=sell, ratio=(buy / sell if sell > 0 else 99.0),
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

def main():
    pdfs = sorted(glob.glob("data/*.pdf"))
    if not pdfs: sys.exit("data/ にPDFがありません")
    pdf = pdfs[-1]
    df = parse_pdf(pdf)
    sell_total, buy_total = df.sell_oku.sum(), df.buy_oku.sum()
    dry = sell_total < SELL_TH
    th = 3 + (1 if dry else 0)
    df = df[(df.buy_oku + df.sell_oku) >= MIN_MARGIN].reset_index(drop=True)
    print(pdf, "銘柄数", len(df), flush=True)

    import yfinance as yf
    out = []
    codes = df.code.tolist()
    for i in range(0, len(codes), CHUNK):
        chunk = codes[i:i + CHUNK]
        tk = [c + ".T" for c in chunk]
        for attempt in range(3):
            try:
                px = yf.download(tk, period="15mo", auto_adjust=True, group_by="ticker", progress=False, threads=True)
                break
            except Exception as e:
                print("retry", e, flush=True); time.sleep(10); px = None
        if px is None: continue
        for c, t in zip(chunk, tk):
            try: s = px[t]["Close"]
            except Exception: continue
            tc = tech(s)
            if tc: out.append(dict(code=c, **tc))
        time.sleep(3)
    t = pd.DataFrame(out)
    res = df.merge(t, on="code")
    res["score"] = res.apply(score, axis=1)
    res["verdict"] = res.score.apply(lambda x: "買い候補" if x >= th else "回避・戻り売り" if x <= -3 else "様子見")
    res = res.sort_values("score", ascending=False)
    cols = ["code", "name", "market", "score", "verdict", "ratio", "buy_oku", "sell_oku", "price", "ma25", "ma50", "ma75", "ma200", "months_from_high"]
    res[cols].round(2).to_csv("result.csv", index=False, encoding="utf-8-sig")
    with open("summary.txt", "w", encoding="utf-8") as f:
        f.write(f"元データ: {pdf}\n普通株 信用売り残 {sell_total:.0f}億円 / 買い残 {buy_total:.0f}億円 / 倍率 {buy_total/sell_total:.2f}\n")
        f.write(f"売り残{SELL_TH}億円未満(燃料枯渇): {dry}  買い候補の閾値: {th}点以上\n株価を取得できた銘柄: {len(res)} / {len(df)}\n")

if __name__ == "__main__":
    main()
