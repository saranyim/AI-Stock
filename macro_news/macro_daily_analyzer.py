import sys
import os
import time
import json
import re
import warnings
import pandas as pd
import yfinance as yf
import requests
import feedparser
from datetime import datetime
from collections import Counter, defaultdict

# ซ่อนคำเตือน Deprecation Warning ของ Pandas
warnings.filterwarnings("ignore")

# ตั้งค่า Cache สำหรับ yfinance เพื่อป้องกัน DB Lock บน OS
try:
    yf.set_tz_cache_location("/tmp/yf_cache")
except Exception:
    pass

# บังคับให้ Output Stream รองรับภาษาไทยบน Terminal / Command Prompt
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

CONFIG_PATH = "configmac.json"
SP500_CSV_PATH = "sp500_companies.csv"
CACHE_NEWS_PATH = "temp_fetched_news.json"
OUTPUT_MD_PATH = "daily_macro_watchlist.md"
OUTPUT_JSON_PATH = "sp500_sector_groups.json"

DELAY_BETWEEN_NEWS = 0.5   # Anti-flood delay ดึงข่าว
DELAY_BETWEEN_STOCKS = 0.2  # หน่วงเวลาระหว่าง Ticker

# --- STEP 1: CONFIG & SYSTEM HEALTH DIAGNOSTIC ---
def load_config() -> dict:
    if not os.path.exists(CONFIG_PATH):
        print(f"⚠️ [WARNING] ไม่พบไฟล์ '{CONFIG_PATH}' ใช้คอนฟิกเริ่มต้นสำรอง")
        return {
            "ai": {
                "base_url": "http://localhost:11434/v1",
                "api_key": "ollama",
                "model": "qwen2.5:14b"
            }
        }
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def check_system_health(ai_config: dict) -> bool:
    print("=" * 95)
    print("📌 [STEP 1/4] SYSTEM DIAGNOSTIC & HEALTH CHECK")
    print("=" * 95)
    
    base_url = ai_config.get("base_url", "").rstrip("/")
    model_name = ai_config.get("model", "")
    api_key = ai_config.get("api_key", "")
    
    print(f"🔹 Target AI Server : {base_url}")
    print(f"🔹 LLM Model       : {model_name}")
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}" if api_key else "Bearer ollama"
    }
    
    llm_ok = False
    try:
        resp = requests.post(
            f"{base_url}/chat/completions",
            headers=headers,
            json={
                "model": model_name,
                "messages": [{"role": "user", "content": "Ping test. Respond with OK."}],
                "max_tokens": 10
            },
            timeout=15
        )
        if resp.status_code == 200:
            print("🟢 [AI SERVER]: Connected & Ready (Local AI Engine Online)")
            llm_ok = True
        else:
            print(f"🔴 [AI SERVER ERROR]: HTTP {resp.status_code} - {resp.text[:100]}")
    except Exception as e:
        print(f"🔴 [AI SERVER ERROR]: ไม่สามารถเชื่อมต่อ LLM Server ได้ ({e})")
    
    news_ok = False
    try:
        test_ticker = yf.Ticker("QQQ")
        if test_ticker.news:
            print("🟢 [NEWS APIS]: Yahoo Finance Connected")
            news_ok = True
    except Exception:
        pass
        
    if not news_ok:
        print("⚠️ yfinance news มีปัญหา สลับไปเช็ก RSS Feed สำรอง...")
        try:
            feed = feedparser.parse("https://feeds.finance.yahoo.com/rss/2.0/headline?s=NVDA&region=US&lang=en-US")
            if feed.entries:
                print("🟢 [NEWS APIS FALLBACK]: เชื่อมต่อ RSS Feeds สำเร็จ")
                news_ok = True
        except Exception:
            pass

    print("=" * 95 + "\n")
    return llm_ok and news_ok

# --- STEP 2: MACRO REGIME & MULTI-SOURCE NEWS FETCHING ---
def fetch_verified_macro_news() -> dict:
    print("📌 [STEP 2/4] FETCHING MULTI-SOURCE MACRO NEWS & CACHING...")
    
    # เช็ก Local Cache ชั่วคราวก่อน (ป้องกันยิงซ้ำระหว่างเทสต์)
    if os.path.exists(CACHE_NEWS_PATH):
        try:
            with open(CACHE_NEWS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                cached_time = datetime.fromisoformat(data.get("timestamp", "2000-01-01"))
                if datetime.now() - cached_time < pd.Timedelta(hours=2):
                    print(f"📦 [CACHE HIT] โหลดข่าวเก่าจาก '{CACHE_NEWS_PATH}' (ไม่ต้องยิง Request ใหม่)")
                    return data
        except Exception:
            pass

    verified_sources = {
        "Yahoo Finance": "https://feeds.finance.yahoo.com/rss/2.0/headline?s=NVDA&region=US&lang=en-US",
        "Fed Policy": "https://www.federalreserve.gov/feeds/press_all.xml",
        "Seeking Alpha": "https://seekingalpha.com/feed.xml",
        "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "OilPrice": "https://oilprice.com/rss/main",
        "Zacks": "https://www.zacks.com/rss/news/"
    }

    collected = []
    source_stats = {}

    for name, rss_url in verified_sources.items():
        try:
            time.sleep(0.3)
            feed = feedparser.parse(rss_url, request_headers={"User-Agent": "Mozilla/5.0"})
            entries = feed.entries
            count = len(entries)
            source_stats[name] = count
            print(f" ⏳ Fetching {name:<18}: 🟢 {count} articles")
            for entry in entries[:8]:
                collected.append({
                    "source": name,
                    "title": entry.get("title", "No Title"),
                    "link": entry.get("link", ""),
                    "summary": entry.get("summary", "")[:300]
                })
        except Exception as e:
            source_stats[name] = 0
            print(f" ⏳ Fetching {name:<18}: 🔴 Failed ({e})")

    payload = {
        "timestamp": datetime.now().isoformat(),
        "total_articles": len(collected),
        "source_stats": source_stats,
        "articles": collected
    }

    with open(CACHE_NEWS_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    return payload

def analyze_macro_regime_with_ai(news_payload: dict, ai_config: dict) -> dict:
    # เช็กดัชนี QQQ
    qqq_close, qqq_ema50, market_bullish = 0.0, 0.0, False
    try:
        qqq = yf.download("QQQ", period="1y", interval="1d", auto_adjust=True, progress=False)
        if isinstance(qqq.columns, pd.MultiIndex): qqq.columns = qqq.columns.get_level_values(0)
        qqq["EMA50"] = qqq["Close"].ewm(span=50, adjust=False).mean()
        qqq_close = float(qqq["Close"].iloc[-1])
        qqq_ema50 = float(qqq["EMA50"].iloc[-1])
        market_bullish = qqq_close > qqq_ema50
    except Exception:
        pass

    articles = news_payload.get("articles", [])
    news_text = "\n".join([f"- [{a['source']}] {a['title']}: {a['summary']}" for a in articles[:25]])

    prompt = f"""
คุณคือหัวหน้านักวิเคราะห์กลยุทธ์มหภาคและ Sector Rotation สำหรับตลาดหุ้นสหรัฐฯ

ข้อมูลดัชนี QQQ ราคาปิด: ${qqq_close:.2f} (EMA50: ${qqq_ema50:.2f}) -> Status: {"BULLISH" if market_bullish else "RETRACEMENT"}
ข่าวสารมหภาคจาก 6 สำนักข่าวหลัก:
{news_text}

โจทย์การวิเคราะห์:
1. ภาพรวม Macro Sentiment เป็นอย่างไร? (BULLISH / BEARISH / CAUTION)
2. สแกนหา Sector Rotation: เงินทุนกำลังหมุนเข้ากลุ่มใด และหมุนออกจากกลุ่มใด?
3. สรุปภาวะตลาด 2-3 ประโยคภาษาไทย

ตอบเป็น JSON เท่านั้น:
{{
    "market_sentiment": "CAUTION",
    "macro_summary": "สรุปสภาวะตลาดสั้นๆ",
    "leading_sectors": ["ชื่อ Sector 1", "ชื่อ Sector 2"],
    "lagging_sectors": ["ชื่อ Sector ที่เงินไหลออก"],
    "allow_trading": true
}}
"""
    try:
        resp = requests.post(
            f"{ai_config.get('base_url')}/chat/completions",
            headers={"Authorization": f"Bearer {ai_config.get('api_key', 'ollama')}", "Content-Type": "application/json"},
            json={
                "model": ai_config.get("model", "qwen2.5:14b"),
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
                "max_tokens": 500
            },
            timeout=90
        )
        if resp.status_code == 200:
            raw = resp.json()["choices"][0]["message"]["content"]
            raw = re.sub(r'<think>.*?</think>', '', raw, flags=re.DOTALL).strip()
            match = re.search(r'\{.*\}', raw, re.DOTALL)
            if match:
                res = json.loads(match.group())
                res["qqq_close"] = qqq_close
                res["qqq_ema50"] = qqq_ema50
                return res
    except Exception as e:
        print(f"⚠️ AI Macro Analysis Exception: {e}")

    return {
        "market_sentiment": "CAUTION" if market_bullish else "BEARISH",
        "macro_summary": "ไม่สามารถดึงข้อมูล AI ได้ ใช้องค์ประกอบทางเทคนิคเป็นหลัก",
        "leading_sectors": ["Information Technology", "Financials"],
        "lagging_sectors": ["Consumer Discretionary"],
        "allow_trading": market_bullish,
        "qqq_close": qqq_close,
        "qqq_ema50": qqq_ema50
    }

# --- STEP 3: S&P 500 SECTOR MONEY FLOW & MARTIN LUK LEADER SELECTION ---
def group_sp500_sectors() -> dict:
    if not os.path.exists(SP500_CSV_PATH):
        print(f"🔴 [ERROR] ไม่พบไฟล์ '{SP500_CSV_PATH}'")
        sys.exit(1)
        
    df = pd.read_csv(SP500_CSV_PATH)
    symbol_col = "Symbol" if "Symbol" in df.columns else "Ticker"
    sector_col = "GICS Sector" if "GICS Sector" in df.columns else "Sector"
    sub_col = "GICS Sub-Industry" if "GICS Sub-Industry" in df.columns else "Sub-Industry"

    df[symbol_col] = df[symbol_col].astype(str).str.replace(".", "-", regex=False)
    
    sector_groups = defaultdict(lambda: defaultdict(list))
    for _, row in df.iterrows():
        sec = row.get(sector_col, "Unassigned")
        sub_sec = row.get(sub_col, "General")
        sector_groups[sec][sub_sec].append(row[symbol_col])
        
    return sector_groups

def analyze_sector_money_flow(sector_groups: dict) -> list:
    print("\n" + "=" * 95)
    print("📌 [STEP 3/4] ANALYZING SECTOR MONEY FLOW & MARTIN LUK LEADERS...")
    print("=" * 95)

    # ดึง Benchmark SPY เพื่อคำนวณ Relative Strength (RS)
    spy_pct = 0.0
    try:
        spy_df = yf.download("SPY", period="5d", interval="1d", auto_adjust=True, progress=False)
        if not spy_df.empty and len(spy_df) >= 2:
            if isinstance(spy_df.columns, pd.MultiIndex): spy_df.columns = spy_df.columns.get_level_values(0)
            spy_c = float(spy_df["Close"].iloc[-1])
            spy_p = float(spy_df["Close"].iloc[-2])
            spy_pct = ((spy_c - spy_p) / spy_p) * 100
    except Exception:
        pass

    results = []

    for sec_name, sub_dict in sector_groups.items():
        all_symbols = []
        for stocks in sub_dict.values():
            all_symbols.extend(stocks)

        print(f"⏳ Processing Sector: {sec_name:<25} ({len(all_symbols)} tickers)...", end="", flush=True)

        try:
            data = yf.download(all_symbols, period="1mo", interval="1d", auto_adjust=True, progress=False)
            time.sleep(0.2)
        except Exception:
            print(" 🔴 Error")
            continue

        if data.empty:
            print(" 🟡 No Data")
            continue

        stock_metrics = []
        total_dollar_vol = 0.0
        adv_count, dec_count = 0, 0

        for sym in all_symbols:
            try:
                if len(all_symbols) == 1:
                    df_s = data.copy()
                else:
                    df_s = pd.DataFrame({
                        "Close": data["Close"][sym],
                        "High": data["High"][sym],
                        "Volume": data["Volume"][sym]
                    }).dropna()

                if len(df_s) < 10: continue

                c_curr = float(df_s["Close"].iloc[-1])
                c_prev = float(df_s["Close"].iloc[-2])
                pct_chg = ((c_curr - c_prev) / c_prev) * 100

                vol_curr = float(df_s["Volume"].iloc[-1])
                vol_sma20 = float(df_s["Volume"].rolling(20).mean().iloc[-1])
                rvol = round(vol_curr / vol_sma20, 2) if vol_sma20 > 0 else 1.0

                dollar_vol = c_curr * vol_curr
                total_dollar_vol += dollar_vol

                swing_h = float(df_s["High"].iloc[:-1].max())

                if pct_chg > 0: adv_count += 1
                else: dec_count += 1

                stock_metrics.append({
                    "symbol": sym,
                    "close": c_curr,
                    "pct_change": pct_chg,
                    "dollar_volume": dollar_vol,
                    "rvol": rvol,
                    "swing_high": swing_h,
                    "near_breakout": c_curr >= (swing_h * 0.98)
                })
            except Exception:
                continue

        if not stock_metrics:
            print(" 🟡 Skipped")
            continue

        df_sec = pd.DataFrame(stock_metrics)
        min_p = float(df_sec["close"].min())
        max_p = float(df_sec["close"].max())
        avg_p = float(df_sec["close"].mean())
        avg_pct = float(df_sec["pct_change"].mean())
        
        rs_vs_spy = round(avg_pct - spy_pct, 2)
        breadth_ratio = (adv_count / len(stock_metrics)) * 100

        # Money Flow Decision
        if avg_pct > 0.5 and breadth_ratio >= 60 and rs_vs_spy > 0:
            flow_status = "🟢 MONEY IN (Strong Inflow)"
        elif avg_pct > 0 and breadth_ratio >= 50:
            flow_status = "🟡 MILD INFLOW (Accumulating)"
        elif avg_pct < -0.5 and breadth_ratio < 40:
            flow_status = "🔴 MONEY OUT (Outflow/Distribution)"
        else:
            flow_status = "⚪ NEUTRAL (Consolidating)"

        # Martin Luk Sector Leader Selection (Dollar Vol >= $10M + Near Breakout + High RVOL)
        leaders_df = df_sec[df_sec["dollar_volume"] >= 10000000].sort_values(
            by=["near_breakout", "rvol", "pct_change"], ascending=[False, False, False]
        )

        star_leader = None
        if not leaders_df.empty:
            top = leaders_df.iloc[0]
            star_leader = {
                "symbol": top["symbol"],
                "close": round(top["close"], 2),
                "pct_change": round(top["pct_change"], 2),
                "rvol": top["rvol"],
                "dollar_vol_m": round(top["dollar_volume"] / 1e6, 2),
                "is_breakout": top["near_breakout"]
            }

        results.append({
            "sector": sec_name,
            "stock_count": len(stock_metrics),
            "min_price": round(min_p, 2),
            "max_price": round(max_p, 2),
            "avg_price": round(avg_p, 2),
            "avg_pct_change": round(avg_pct, 2),
            "total_dollar_vol_m": round(total_dollar_vol / 1e6, 2),
            "relative_strength": rs_vs_spy,
            "breadth_pct": round(breadth_ratio, 1),
            "money_flow": flow_status,
            "star_leader": star_leader
        })
        print(" 🟢 Done")

    results.sort(key=lambda x: (x["relative_strength"], x["avg_pct_change"]), reverse=True)
    return results

# --- STEP 4: GENERATE MARKDOWN REPORT (.md) ---
def export_unified_markdown_report(news_payload: dict, ai_macro: dict, sector_results: list):
    print("\n" + "=" * 95)
    print("📌 [STEP 4/4] EXPORTING UNIFIED MARKDOWN REPORT (daily_macro_watchlist.md)...")
    print("=" * 95)

    timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    stats = news_payload.get("source_stats", {})
    stats_str = " | ".join([f"**{src}:** {cnt} ข่าว" for src, cnt in stats.items()])

    md_content = f"""# 📊 Daily Macro & Sector Money Flow Analysis Report
**Generated Date:** `{timestamp_str}` (เวลาไทย)  
**News Articles Analyzed:** {news_payload.get('total_articles')} items  
**Source Breakdown:** {stats_str}

---

## 🟢 1. Market Sentiment & Macro Assessment
- **Overall Market Mode:** `{ai_macro.get('market_sentiment', 'CAUTION')}`
- **QQQ Index Close:** `${ai_macro.get('qqq_close', 0):.2f}` (เส้น EMA50: `${ai_macro.get('qqq_ema50', 0):.2f}`)
- **Trade Execution Order:** `{"ALLOW_BUY" if ai_macro.get("allow_trading") else "NO_TRADE_EXECUTION"}`
- **AI Summary:** 
> {ai_macro.get('macro_summary', 'N/A')}

---

## 🔄 2. Sector Rotation (Money Flow Tracking)
- **🟢 Leading Sectors (เงินหมุนเข้า):** {", ".join(ai_macro.get('leading_sectors', []))}
- **🔴 Lagging Sectors (เงินหมุนออก):** {", ".join(ai_macro.get('lagging_sectors', []))}

---

## 🧭 3. ตารางวิเคราะห์ภาพรวมการลงทุน 11 กลุ่มอุตสาหกรรม (S&P 500 Sector Overview)

| กลุ่มอุตสาหกรรม (Sector) | จำนวนหุ้น | Min Price | Max Price | Avg Price | % Change เฉลี่ย | Dollar Volume รวม | RS vs SPY | ทิศทางเงินไหล (Money Flow) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
"""
    for s in sector_results:
        md_content += f"| **{s['sector']}** | {s['stock_count']} | ${s['min_price']} | ${s['max_price']} | ${s['avg_price']} | {s['avg_pct_change']:+.2f}% | ${s['total_dollar_vol_m']:,.1f}M | {s['relative_strength']:+.2f} | {s['money_flow']} |\n"

    md_content += """\n---

## 🌟 4. รายชื่อดาวเด่นประจำกลุ่ม (Martin Luk Sector Leaders)

คัดเลือกเฉพาะหุ้นที่มี **สภาพคล่องสูง (Dollar Vol $\\ge \\$10M/วัน)**, **RVOL เข้าหนาแน่น**, และ **ทรงกราฟจ่อ Breakout/แข็งแกร่งกว่าตลาด**

"""
    for s in sector_results:
        leader = s["star_leader"]
        md_content += f"### 📌 {s['sector']} (ทิศทางเงิน: {s['money_flow']})\n"
        if leader:
            breakout_tag = "🚀 [NEAR BREAKOUT / SWING HIGH]" if leader["is_breakout"] else "📈 [MOMENTUM LEADER]"
            md_content += f"- **ดาวเด่นประจำกลุ่ม:** `{leader['symbol']}` {breakout_tag}\n"
            md_content += f"  - **ราคาล่าสุด:** `${leader['close']}` ({leader['pct_change']:+.2f}%)\n"
            md_content += f"  - **Relative Volume (RVOL):** `{leader['rvol']}x`\n"
            md_content += f"  - **Dollar Volume (สภาพคล่อง):** `${leader['dollar_vol_m']} Million/day`\n"
            md_content += f"  - **Intraday Action Plan:** หาก Pre-market เปิด Gap กระโดดขึ้น ให้เฝ้ารอ Breakout TF 1-5 นาที\n"
        else:
            md_content += f"- *ไม่พบหุ้นเข้าเกณฑ์ดาวเด่นที่มีสภาพคล่องเพียงพอในกลุ่มนี้*\n"
        md_content += "\n"

    md_content += f"""---
## 📰 5. Raw News Headlines Collected
"""
    for a in news_payload.get("articles", [])[:15]:
        md_content += f"- [{a['source']}] [{a['title']}]({a['link']})\n"

    with open(OUTPUT_MD_PATH, "w", encoding="utf-8") as f:
        f.write(md_content)
        
    print(f"💾 บันทึกรายงานสรุปทั้งหมดลงไฟล์: '{OUTPUT_MD_PATH}' เรียบร้อยแล้ว!")

# --- MAIN EXECUTION FLOW ---
def main():
    config = load_config()
    ai_config = config.get("ai", {})
    
    # STEP 1: System Diagnostic
    if not check_system_health(ai_config):
        print("🛑 [SYSTEM HALTED]: ตรวจพบความล้มเหลวของระบบ ยุติการทำงาน")
        sys.exit(1)

    # STEP 2: Fetch Macro News & Analyze Market Regime
    news_payload = fetch_verified_macro_news()
    ai_macro = analyze_macro_regime_with_ai(news_payload, ai_config)

    # STEP 3: Group S&P 500 & Calculate Sector Money Flow
    sector_groups = group_sp500_sectors()
    sector_results = analyze_sector_money_flow(sector_groups)

    # STEP 4: Export to Unified Markdown Report
    export_unified_markdown_report(news_payload, ai_macro, sector_results)

    print("\n" + "=" * 95)
    print("🎉 [COMPLETE] สคริปต์ฉบับรวมสมบูรณ์ (Steps 1-4) ทำงานเสร็จสิ้น!")
    print(f"📌 ผลลัพธ์ถูกบันทึกลง '{OUTPUT_MD_PATH}' พร้อมสำหรับ Script ช่วงค่ำแล้วครับ")
    print("=" * 95)

if __name__ == "__main__":
    main()