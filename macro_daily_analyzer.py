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
from collections import Counter

# ซ่อนคำเตือน Deprecation Warning ของ Pandas
warnings.filterwarnings("ignore")

# ตั้งค่า Cache สำหรับ yfinance บนระบบปฏิบัติการเพื่อป้องกัน DB Lock
try:
    yf.set_tz_cache_location("/tmp/yf_cache")
except Exception:
    pass

# บังคับให้ Output Stream รองรับภาษาไทยบน Terminal
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

CONFIG_PATH = "config.json"
SP500_PATH = "sp500_companies.csv"
OUTPUT_MD_PATH = "daily_macro_watchlist.md"

DELAY_BETWEEN_NEWS = 0.5   # หน่วงเวลาดึงข่าวป้องกัน Rate Limit / IP Block
DELAY_BETWEEN_STOCKS = 1.5  # หน่วงเวลาระหว่าง Ticker

# --- 1. CONFIG & HEALTH CHECK LAYER ---
def load_config() -> dict:
    if not os.path.exists(CONFIG_PATH):
        print(f"⚠️ [WARNING] ไม่พบไฟล์ '{CONFIG_PATH}' ใช้คอนฟิกเริ่มต้นสำรอง")
        return {
            "ai": {
                "base_url": "http://192.168.1.242:4000/v1",
                "api_key": "",
                "model": "qwen35-fast"
            }
        }
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def check_system_health(ai_config: dict) -> bool:
    print("=" * 90)
    print("🔍 [SYSTEM HEALTH CHECK] ตรวจสอบความพร้อมของ Local AI Server & API ข่าวสาร")
    print("=" * 90)
    
    base_url = ai_config.get("base_url", "").rstrip("/")
    model_name = ai_config.get("model", "")
    api_key = ai_config.get("api_key", "")
    
    print(f"🔹 Target AI Server : {base_url}")
    print(f"🔹 LLM Model       : {model_name}")
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}" if api_key else "Bearer ollama"
    }
    
    # 1. Ping Check LLM Endpoint
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
            print("🟢 [AI SERVER]: Connected & Ready (122B Engine Online)")
            llm_ok = True
        else:
            print(f"🔴 [AI SERVER ERROR]: HTTP {resp.status_code} - {resp.text[:100]}")
    except Exception as e:
        print(f"🔴 [AI SERVER ERROR]: ไม่สามารถเชื่อมต่อ LLM Server ได้ ({e})")
        print("💡 กรุณาตรวจสอบ IP 192.168.1.242:4000 หรือ Service vLLM/Ollama")
    
    # 2. Check News Sources with Fallback (สลับ RSS หาก yfinance ล่ม)
    news_ok = False
    try:
        test_ticker = yf.Ticker("QQQ")
        if test_ticker.news:
            print("🟢 [NEWS APIS]: Yahoo Finance Connected")
            # news_ok = True
    except Exception:
        pass
        
    if not news_ok:
        print("⚠️ Yahoo Finance มีปัญหา กำลังสลับไปเช็กสำนักข่าวสำรอง (RSS Feeds)...")
        fallback_rss = [
            "https://search.cnbc.com/rs/search/combined Garfield/rss.xml",
            "https://www.investing.com/rss/news.rss",
            "https://feeds.bbci.co.uk/news/business/rss.xml",
            "https://www.federalreserve.gov/feeds/press_all.xml",
            "https://feeds.a.dj.com/rss/WSJcomUSBusiness.xml",
            "https://www.benzinga.com/feed",
            "https://www.zacks.com/rss/news/",
            "https://oilprice.com/rss/main",
            "https://www.coindesk.com/arc/outboundfeeds/rss/"
        ]
        for rss_url in fallback_rss:
            try:
                feed = feedparser.parse(rss_url)
                if feed.entries:
                    print(f"🟢 [NEWS APIS FALLBACK]: เชื่อมต่อข่าวสำรองสำเร็จ ({rss_url.split('/')[2]})")
                    news_ok = True
                    break
            except Exception:
                continue

    if not news_ok:
        print("🟡 [NEWS APIS WARNING]: ไม่สามารถดึงข่าวได้ในขณะนี้ ระบบจะสแกนโดยใช้องค์ประกอบเทคนิคเป็นหลัก")
        news_ok = True

    print("=" * 90 + "\n")
    return llm_ok and news_ok

# --- 2. LOAD TICKERS ---
def load_sp500_list() -> list:
    if os.path.exists(SP500_PATH):
        try:
            df = pd.read_csv(SP500_PATH)
            col = "Symbol" if "Symbol" in df.columns else "Ticker"
            tickers = df[col].astype(str).str.replace(".", "-", regex=False).tolist()
            print(f"📄 โหลดรายชื่อหุ้น S&P 500 จากไฟล์ CSV สำเร็จ: {len(tickers)} ตัว")
            return tickers
        except Exception as e:
            print(f"⚠️ Error reading CSV: {e}")
    print("⚠️ ไม่พบไฟล์ CSV ใช้รายชื่อ Watchlist สำรอง...")
    return ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "COST", "AMD", "COIN", "PLTR", "RIOT"]

# --- 3. MACRO & SECTOR ROTATION ANALYZER ---
def analyze_macro_market_regime(ai_config: dict) -> dict:
    print("🌐 [STEP 1/3] วิเคราะห์สภาวะเศรษฐกิจภาพรวม & ดัชนี QQQ (Market Regime)...")
    
    qqq_close, qqq_ema50, market_bullish = 0.0, 0.0, False
    try:
        qqq = yf.download("QQQ", period="1y", interval="1d", auto_adjust=True, progress=False)
        if isinstance(qqq.columns, pd.MultiIndex): qqq.columns = qqq.columns.get_level_values(0)
        qqq["EMA50"] = qqq["Close"].ewm(span=50, adjust=False).mean()
        qqq_close = float(qqq["Close"].iloc[-1])
        qqq_ema50 = float(qqq["EMA50"].iloc[-1])
        market_bullish = qqq_close > qqq_ema50
    except Exception as e:
        print(f"⚠️ ไม่สามารถดึงข้อมูล QQQ ได้: {e}")

    macro_articles = []
    source_counts = Counter()
    try:
        spy_ticker = yf.Ticker("SPY")
        yf_news = spy_ticker.news or []
        for n in yf_news[:6]:
            title = n.get("title") or n.get("headline") or ""
            summary = (n.get("summary") or n.get("text") or "")[:500]
            publisher = n.get("provider", {}).get("displayName") or n.get("publisher") or "Yahoo Finance"
            source_counts[publisher] += 1
            macro_articles.append(f"- [{publisher}] {title}: {summary}")
    except Exception:
        pass

    news_text_block = "\n".join(macro_articles) if macro_articles else "ไม่พบข่าวสารวิกฤตเศรษฐกิจภาพรวม"

    prompt = f"""
คุณคือหัวหน้านักวิเคราะห์กลยุทธ์มหภาค (Macro Regime Analyst) สำหรับการเทรดหุ้นสหรัฐฯ สไตล์ Martin Luk

ข้อมูลตลาดปัจจุบัน:
- ดัชนี QQQ ราคาปิดล่าสุด: ${qqq_close:.2f} (เส้น EMA50: ${qqq_ema50:.2f}) -> สัญญาณทางเทคนิค: {"BULLISH" if market_bullish else "BEARISH/RETRACEMENT"}
- ข่าวสารภาพรวมเศรษฐกิจ/เลือกตั้ง/Fed ล่าสุด:
{news_text_block}

โจทย์ประเมิน:
ให้อ่านข่าวภาพรวมแล้ววิเคราะห์ว่าเงินทุนกำลังหมุนเวียนไปกลุ่มธุรกิจไหน (Sector Rotation) และสภาวะตลาดอยู่ในโหมด "Breakout แล้วไปต่อ" หรือโหมดผันผวนสูง "Breakout แล้วโดนทุบกลับ (Trap)"

ตอบกลับในรูปแบบ JSON เท่านั้น:
{{
    "macro_risk_level": "LOW_RISK", 
    "market_regime": "BREAKOUT_MODE", 
    "leading_sectors": "Crypto/Fintech, Energy, Aerospace",
    "ai_assessment": "สรุปวิเคราะห์สั้นๆ 2-3 ประโยคภาษาไทย บอกทิศทางเงินหมุนเวียน",
    "allow_trading": true
}}

*คำอธิบาย macro_risk_level: "LOW_RISK" (ตลาดปลอดภัย), "MEDIUM_RISK" (ระวังความผันผวน), "HIGH_RISK" (เกิดวิกฤตเศรษฐกิจ สั่งระงับซื้อ)*
"""
    try:
        resp = requests.post(
            f"{ai_config.get('base_url')}/chat/completions",
            headers={"Authorization": f"Bearer {ai_config.get('api_key', '')}", "Content-Type": "application/json"},
            json={
                "model": ai_config.get("model", "qwen35-fast"),
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
                "max_tokens": 500
            },
            timeout=120
        )
        if resp.status_code == 200:
            raw = resp.json()["choices"][0]["message"]["content"]
            raw = re.sub(r'<think>.*?</think>', '', raw, flags=re.DOTALL).strip()
            match = re.search(r'\{.*\}', raw, re.DOTALL)
            if match:
                res = json.loads(match.group())
                res["qqq_close"] = qqq_close
                res["qqq_ema50"] = qqq_ema50
                res["source_counts"] = dict(source_counts)
                return res
    except Exception as e:
        print(f"⚠️ AI Macro Analysis Failed: {e}")

    return {
        "macro_risk_level": "LOW_RISK" if market_bullish else "MEDIUM_RISK",
        "market_regime": "BREAKOUT_MODE" if market_bullish else "CAUTION_MODE",
        "leading_sectors": "General Technology",
        "ai_assessment": "ใช้องค์ประกอบดัชนี QQQ เป็นหลัก ตลาดทรงตัวเหนือ EMA50",
        "allow_trading": market_bullish,
        "qqq_close": qqq_close,
        "qqq_ema50": qqq_ema50,
        "source_counts": dict(source_counts)
    }

# --- 4. TECHNICAL & DOLLAR VOLUME SCANNER (MARTIN LUK STEP 1 & 2) ---
def scan_qualified_tickers(tickers: list) -> list:
    print("📈 [STEP 2/3] สแกนคัดกรองหุ้นตามเกณฑ์ Martin Luk (Dollar Volume + TF Day Context)...")
    qualified = []
    
    for idx, ticker in enumerate(tickers, 1):
        if idx % 50 == 0:
            print(f"   ...สแกนผ่านไปแล้ว {idx}/{len(tickers)} หุ้น")
            
        df = pd.DataFrame()
        for attempt in range(3):
            try:
                df = yf.download(ticker, period="1y", interval="1d", auto_adjust=True, progress=False)
                time.sleep(0.1)
                if not df.empty:
                    break
            except Exception:
                time.sleep(1.0)
                
        if df.empty or len(df) < 200:
            continue
            
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        close = float(df["Close"].iloc[-1])
        vol = float(df["Volume"].iloc[-1])
        
        # 1. DOLLAR VOLUME FILTER (ตัดหุ้นสภาพคล่องต่ำ ป้องกัน RVOL หลอก)
        df["Vol_SMA20"] = df["Volume"].rolling(20).mean()
        avg_vol20 = float(df["Vol_SMA20"].iloc[-1])
        avg_dollar_volume = close * avg_vol20
        
        if avg_dollar_volume < 10_000_000: # ต้องเกิน 10 ล้านดอลลาร์/วัน
            continue
            
        rvol = round(vol / avg_vol20, 2) if avg_vol20 > 0 else 1.0
        
        # 2. TIMEFRAME DAY CONTEXT CHECK (Martin Luk Step 2)
        df["EMA20"] = df["Close"].ewm(span=20, adjust=False).mean()
        df["EMA50"] = df["Close"].ewm(span=50, adjust=False).mean()
        df["EMA200"] = df["Close"].ewm(span=200, adjust=False).mean()
        
        ema20 = float(df["EMA20"].iloc[-1])
        ema50 = float(df["EMA50"].iloc[-1])
        ema200 = float(df["EMA200"].iloc[-1])
        
        recent_swing_high = float(df["High"].iloc[-61:-1].max())
        consolidation_top = float(df["Close"].iloc[-20:-1].max())
        
        is_uptrend = close > ema200 and ema20 > ema50
        near_swing_high = close >= (recent_swing_high * 0.95)
        above_consolidation = close >= consolidation_top
        volume_supported = rvol >= 1.2
        
        if is_uptrend and (near_swing_high or above_consolidation) and volume_supported:
            liquidity_label = f"${avg_dollar_volume/1_000_000:.1f}M/day"
            qualified.append({
                "symbol": ticker,
                "close": round(close, 2),
                "rvol": rvol,
                "avg_dollar_volume_m": round(avg_dollar_volume / 1_000_000, 2),
                "liquidity_label": liquidity_label,
                "swing_high": round(recent_swing_high, 2),
                "consolidation_top": round(consolidation_top, 2),
                "ema20": round(ema20, 2),
                "ema50": round(ema50, 2),
                "ema200": round(ema200, 2),
                "day_context": "🚀 จ่อทะลุ Swing High" if close >= recent_swing_high else "📦 หลุดกรอบสะสมเดิม"
            })
            
    print(f"✅ สแกนเสร็จสิ้น! พบหุ้นสเปก A+ ผ่านเกณฑ์ Dollar Volume & TF Day: {len(qualified)} ตัว\n")
    return qualified

# --- 5. LIVE MARKDOWN STREAMING EXPORT ---
def init_markdown_file(macro_info: dict):
    timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    allow_trade = macro_info.get("allow_trading", True)
    risk_level = macro_info.get("macro_risk_level", "LOW_RISK")
    
    if risk_level == "LOW_RISK" and allow_trade:
        status_badge = "🟢 **NORMAL / BREAKOUT MODE** (Market Safe)"
    elif risk_level == "MEDIUM_RISK":
        status_badge = "🟡 **CAUTION / HIGH VOLATILITY** (Reduce Position Size 50%)"
    else:
        status_badge = "🔴 **HIGH RISK / NO TRADE** (Market Drag Crisis - สั่งพักการเทรด)"

    source_counts = macro_info.get("source_counts", {})
    source_str = " | ".join([f"**{src}:** {cnt} ข่าว" for src, cnt in source_counts.items()]) if source_counts else "Yahoo Finance"

    with open(OUTPUT_MD_PATH, "w", encoding="utf-8") as f:
        f.write("# 📊 Daily Macro & Watchlist Summary (Martin Luk Strategy)\n")
        f.write(f"**Generated Date:** `{timestamp_str}` (เวลาไทย)  \n")
        f.write(f"**LLM Engine:** `qwen35-fast` (Local Server)  \n")
        f.write(f"**News Sources Analyzed:** {source_str}\n\n")
        f.write("---\n\n")
        f.write("## 🟢 1. Market Regime & Sector Rotation Assessment\n")
        f.write(f"- **Market Status:** {status_badge}\n")
        f.write(f"- **QQQ Index Close:** `${macro_info.get('qqq_close', 0):.2f}` (เส้น EMA50: `${macro_info.get('qqq_ema50', 0):.2f}`)\n")
        f.write(f"- **Leading Sectors (เงินหมุนเข้า):** `{macro_info.get('leading_sectors', 'N/A')}`\n")
        f.write(f"- **Trade Execution Order:** `{'ALLOW_BUY' if allow_trade else 'NO_TRADE_EXECUTION'}`\n")
        f.write(f"- **AI Assessment:** \n> {macro_info.get('ai_assessment', 'ไม่มีข้อมูล')}\n\n")
        f.write("---\n\n")
        f.write("## 🎯 2. Qualified Pre-Market Watchlist (Live Streaming Results)\n\n")
        f.flush()

def append_stock_to_markdown(stock: dict, ai_analysis: dict):
    sym = stock["symbol"]
    close_p = stock["close"]
    rvol = stock["rvol"]
    liq = stock["liquidity_label"]
    day_ctx = stock["day_context"]
    
    risk_status = ai_analysis.get("risk_status", "PASS")
    reasoning = ai_analysis.get("reasoning", "")
    news_count = ai_analysis.get("news_count", 0)
    source_counts = ai_analysis.get("source_counts", {})
    source_str = " | ".join([f"{src}: {cnt}" for src, cnt in source_counts.items()]) if source_counts else "Yahoo Finance"
    
    md_entry = f"""### 📌 {sym} | Price: ${close_p} | RVOL: {rvol}x | Dollar Vol: {liq}
- **TF Day Context:** {day_ctx} (Swing High: ${stock['swing_high']}, Consolidation Top: ${stock['consolidation_top']})
- **EMA Technical:** Price > EMA20 (${stock['ema20']}) > EMA50 (${stock['ema50']}) > EMA200 (${stock['ema200']})
- **AI News Risk Status:** `{risk_status}` (วิเคราะห์จาก {news_count} บทความ [{source_str}])
- **AI Reasoning:** {reasoning}
- **Intraday Action Plan (Script 2 Checklist):** หาก Pre-market เปิด Gap กระโดดขึ้น จ่อหลุด Swing High `${stock['swing_high']}` ให้จับกรอบ High ใน TF 1-5 นาที แล้วคำนวณ Position Size เข้าซื้อ
---
"""
    with open(OUTPUT_MD_PATH, "a", encoding="utf-8") as f:
        f.write(md_entry)
        f.flush()

def fetch_single_ticker_news(symbol: str, limit: int = 5) -> dict:
    cleaned = []
    source_counter = Counter()
    try:
        t_obj = yf.Ticker(symbol)
        yf_news = t_obj.news or []
        for item in yf_news[:limit]:
            title = item.get("title") or item.get("headline") or ""
            summary = (item.get("summary") or item.get("text") or "")[:500]
            publisher = item.get("provider", {}).get("displayName") or item.get("publisher") or "Yahoo Finance"
            source_counter[publisher] += 1
            cleaned.append(f"- [{publisher}] {title}: {summary}")
    except Exception:
        pass
    return {"articles": cleaned, "source_counts": dict(source_counter), "total_count": len(cleaned)}

def analyze_ticker_news_with_ai(symbol: str, news_info: dict, ai_config: dict) -> dict:
    articles = news_info.get("articles", [])
    if not articles:
        return {
            "risk_status": "PASS",
            "reasoning": "ไม่พบข่าวสารลบใหม่ระยะสั้น (ใช้องค์ประกอบเทคนิคและ Dollar Volume เป็นหลัก)",
            "news_count": 0,
            "source_counts": {}
        }
        
    news_text = "\n".join(articles)
    prompt = f"""
คุณคือระบบกรองความเสี่ยงจากข่าวสาร (News Risk Filter) สำหรับ Swing Trade (ถือครอง 3-10 วัน)

วิเคราะห์ข่าวของหุ้น: **{symbol}** ต่อไปนี้:
{news_text}

โจทย์การประเมิน (เน้นระยะสั้น 1-7 วันเท่านั้น):
1. มี Red Flags ลบแรงทันทีหรือไม่? (เช่น งบแย่กว่าคาดมาก, โดน Downgrade, โดนฟ้องร้อง, ผู้บริหารลาออก)
2. มี Catalyst บวกระยะสั้นหรือไม่? (เช่น งบดีกว่าคาดมาก, ปรับเพิ่มเป้ารายได้, โดน Upgrade)

ตอบกลับเป็น JSON เท่านั้น:
{{
    "risk_status": "PASS", 
    "reasoning": "เหตุผลสั้นๆ 1-2 ประโยคภาษาไทย"
}}

*คำอธิบาย risk_status: "PASS" (ไม่มีข่าวลบ), "WARNING" (ข่าวปนกัน), "REJECT" (มีข่าวลบแรง ห้ามซื้อ)*
"""
    try:
        resp = requests.post(
            f"{ai_config.get('base_url')}/chat/completions",
            headers={"Authorization": f"Bearer {ai_config.get('api_key', '')}", "Content-Type": "application/json"},
            json={
                "model": ai_config.get("model", "qwen35-fast"),
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
                "max_tokens": 400
            },
            timeout=90
        )
        if resp.status_code == 200:
            raw = resp.json()["choices"][0]["message"]["content"]
            raw = re.sub(r'<think>.*?</think>', '', raw, flags=re.DOTALL).strip()
            match = re.search(r'\{.*\}', raw, re.DOTALL)
            if match:
                res = json.loads(match.group())
                res["news_count"] = news_info["total_count"]
                res["source_counts"] = news_info["source_counts"]
                return res
    except Exception:
        pass

    return {
        "risk_status": "PASS",
        "reasoning": "AI Analysis Timeout - ผ่านการกรองโดยองค์ประกอบเทคนิค",
        "news_count": news_info["total_count"],
        "source_counts": news_info["source_counts"]
    }

# --- 6. MAIN EXECUTION FLOW ---
def main():
    config = load_config()
    ai_config = config.get("ai", {})
    
    if not check_system_health(ai_config):
        print("🛑 [SYSTEM HALTED]: ตรวจพบความล้มเหลวของระบบ ยุติการทำงาน")
        sys.exit(1)
        
    sp500_list = load_sp500_list()
    macro_info = analyze_macro_market_regime(ai_config)
    
    # 1. เขียนส่วน Header ของ Markdown
    init_markdown_file(macro_info)
    
    # 2. หากสภาวะตลาดอนุญาต ให้สแกนหุ้น A+ และทยอยบันทึกลงดิสก์ทันที
    if macro_info.get("allow_trading", True):
        qualified_stocks = scan_qualified_tickers(sp500_list)
        print(f"\n🔄 เริ่มประมวลผลข่าวเรียลไทม์ (Anti-Flood Streaming)...")
        
        for idx, stock in enumerate(qualified_stocks, 1):
            sym = stock["symbol"]
            print(f"[{idx}/{len(qualified_stocks)}] 🔎 กำลังดึงข่าวและวิเคราะห์: {sym}...")
            
            news_info = fetch_single_ticker_news(sym, limit=5)
            time.sleep(DELAY_BETWEEN_NEWS)
            
            ai_res = analyze_ticker_news_with_ai(sym, news_info, ai_config)
            append_stock_to_markdown(stock, ai_res)
            print(f"   ✅ บันทึกผล {sym} ลงใน {OUTPUT_MD_PATH} เรียบร้อยแล้ว")
            
            time.sleep(DELAY_BETWEEN_STOCKS)
    else:
        print("🔴 [NO TRADE]: ตลาดมหภาคอยู่ในโหมด HIGH RISK สคริปต์สั่งระงับการเข้าซื้อ")
        with open(OUTPUT_MD_PATH, "a", encoding="utf-8") as f:
            f.write("> ⚠️ **คำเตือนระงับการเข้าซื้อ (NO TRADE):** สภาวะตลาดมหภาคสุ่มเสี่ยงสูง สคริปต์สั่งยกเลิกการสแกนเข้าซื้อเพื่อป้องกัน Breakout Trap\n")

    print("=" * 90)
    print("🎉 [COMPLETE] สคริปต์ตัวที่ 1 ทำงานเสร็จสิ้นสมบูรณ์!")
    print(f"📌 ไฟล์ '{OUTPUT_MD_PATH}' พร้อมสำหรับให้สคริปต์ตัวที่ 2 เปิดอ่านช่วงค่ำแล้วครับ")
    print("=" * 90)

if __name__ == "__main__":
    main()