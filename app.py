import sys
import os
import time
import json
import re
import warnings
import pandas as pd
import yfinance as yf
import requests
import streamlit as st
import plotly.graph_objects as go
from datetime import datetime, timedelta
from collections import Counter

# 1. ซ่อนคำเตือน Deprecation Warning ของ Pandas
warnings.filterwarnings("ignore")

# ตั้งค่า Cache สำหรับ yfinance เพื่อป้องกัน DB Lock บน macOS/Linux
try:
    yf.set_tz_cache_location("/tmp/yf_cache")
except Exception:
    pass

st.set_page_config(
    page_title="Swing Trade AI Cockpit",
    page_icon="📈",
    layout="wide"
)

# --- CONFIG & STORAGE FILE PATHS ---
CONFIG_PATH = "config.json"
SP500_PATH = "sp500_companies.csv"
MY_STOCKS_PATH = "my_stocks.json"
TRADE_HISTORY_PATH = "trade_history.json"

DELAY_BETWEEN_REQUESTS = 0.3

# --- 1. HELPER FUNCTIONS FOR FILE I/O ---
def load_config() -> dict:
    if not os.path.exists(CONFIG_PATH):
        return {
            "ai": {
                "base_url": "http://localhost:11434/v1",
                "api_key": "ollama",
                "model": "qwen2.5:7b"
            }
        }
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def load_sp500_list() -> list:
    if os.path.exists(SP500_PATH):
        try:
            df = pd.read_csv(SP500_PATH)
            col = "Symbol" if "Symbol" in df.columns else "Ticker"
            return df[col].astype(str).str.replace(".", "-", regex=False).tolist()
        except Exception:
            pass
    return ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "COST", "AMD", "PYPL", "QCOM", "NXPI", "BAX", "HCA"]

def load_my_stocks() -> list:
    if not os.path.exists(MY_STOCKS_PATH):
        return []
    with open(MY_STOCKS_PATH, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except Exception:
            return []

def save_my_stocks(stocks_list: list):
    with open(MY_STOCKS_PATH, "w", encoding="utf-8") as f:
        json.dump(stocks_list, f, indent=2, ensure_ascii=False)

def log_trade_history(trade_item: dict, action_type: str):
    history = []
    if os.path.exists(TRADE_HISTORY_PATH):
        with open(TRADE_HISTORY_PATH, "r", encoding="utf-8") as f:
            try:
                history = json.load(f)
            except Exception:
                history = []
    
    trade_record = {
        **trade_item,
        "closed_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "closed_reason": action_type
    }
    history.append(trade_record)
    with open(TRADE_HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)

# --- 2. ENHANCED NEWS FETCHER (พร้อมระบบนับจำนวนข่าวแยกตามแหล่ง) ---
ALLOWED_PUBLISHERS = [
    "Yahoo Finance", "Reuters", "CNBC", "Bloomberg", 
    "MarketWatch", "Seeking Alpha", "Investing.com"
]

@st.cache_data(ttl=900)
def fetch_ticker_news_with_sources(symbol: str, limit: int = 8) -> dict:
    """
    ดึงข่าวสารเฉพาะ Ticker พร้อมนับจำนวนบทความแยกตามแหล่งข่าวต่างประเทศ
    """
    try:
        ticker = yf.Ticker(symbol)
        yf_news = ticker.news
        if not yf_news:
            return {"articles": [], "source_counts": {}, "total_count": 0}

        cleaned_news = []
        source_counter = Counter()

        for item in yf_news[:limit]:
            title = item.get("title") or item.get("headline") or ""
            summary = (item.get("summary") or item.get("text") or "")[:500]
            publisher = item.get("provider", {}).get("displayName") or item.get("publisher") or "Yahoo Finance"
            url = item.get("link") or item.get("url") or f"https://finance.yahoo.com/quote/{symbol}"

            # นับจำนวนข่าวแยกตามแหล่งข่าว (Source)
            source_counter[publisher] += 1

            cleaned_news.append({
                "title": title,
                "summary": summary,
                "publisher": publisher,
                "url": url
            })

        return {
            "articles": cleaned_news,
            "source_counts": dict(source_counter),
            "total_count": len(cleaned_news)
        }
    except Exception as e:
        return {"articles": [], "source_counts": {}, "total_count": 0, "error": str(e)}

# --- 3. LOCAL AI RISK ANALYZER ---
def analyze_news_risk_with_ai(symbol: str, news_data: dict, ai_config: dict) -> dict:
    news_list = news_data.get("articles", [])
    if not news_list:
        return {
            "risk_status": "PASS",
            "reasoning": "ไม่พบข่าวสารใหม่ระยะสั้น (ใช้องค์ประกอบทางเทคนิคเป็นหลัก)",
            "has_immediate_red_flags": False
        }

    news_text = "\n".join([f"- [{n['publisher']}] {n['title']}: {n['summary']}" for n in news_list])
    
    prompt = f"""
คุณคือระบบวิเคราะห์ความเสี่ยงจากข่าวสาร (News Risk Filter) สำหรับ Swing Trade (ถือครอง 3-10 วัน)

วิเคราะห์ข่าวของหุ้น: **{symbol}** ต่อไปนี้:
{news_text}

โจทย์การประเมิน (เน้นระยะสั้น 1-7 วันเท่านั้น):
1. มี Red Flags ลบแรงทันทีหรือไม่? (เช่น งบแย่กว่าคาดมาก, โดน Downgrade, โดนฟ้องร้อง, ผู้บริหารลาออก)
2. มี Catalyst บวกระยะสั้นหรือไม่? (เช่น งบดีกว่าคาดมาก, ปรับเพิ่มเป้ารายได้, โดน Upgrade)

ตอบกลับเป็น JSON เท่านั้น:
{{
    "risk_status": "PASS", 
    "news_sentiment": "POSITIVE",
    "reasoning": "เหตุผลสั้นๆ 1-2 ประโยคภาษาไทย",
    "has_immediate_red_flags": false
}}

*คำอธิบาย risk_status: "PASS" (ไม่มีข่าวลบ), "WARNING" (ข่าวผสมผสาน), "REJECT" (มีข่าวลบแรง ห้ามซื้อ)*
"""
    try:
        response = requests.post(
            f"{ai_config.get('base_url')}/chat/completions",
            headers={
                "Authorization": f"Bearer {ai_config.get('api_key', '')}",
                "Content-Type": "application/json"
            },
            json={
                "model": ai_config.get("model", "qwen2.5:7b"),
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
                "max_tokens": 500
            },
            timeout=120
        )
        if response.status_code == 200:
            raw_content = response.json()["choices"][0]["message"]["content"]
            raw_content = re.sub(r'<think>.*?</think>', '', raw_content, flags=re.DOTALL).strip()
            json_match = re.search(r'\{.*\}', raw_content, re.DOTALL)
            if json_match:
                return json.loads(json_match.group())
    except Exception:
        pass

    return {
        "risk_status": "WARNING",
        "reasoning": "AI Scan Timeout / Parsing Error",
        "has_immediate_red_flags": False
    }

# --- 4. STREAMLIT UI LAYOUT ---
cfg = load_config()
ai_cfg = cfg.get("ai", {})

st.title("📈 Swing Trade AI Cockpit & Portfolio Tracker")
st.caption("ระบบวิเคราะห์หุ้นสเปกเกรด A+ แบบ 2 ทาง (Technical + News Risk Filter)")

# Market Context Check (QQQ > EMA50)
try:
    qqq = yf.download("QQQ", period="1y", interval="1d", auto_adjust=True, progress=False)
    if isinstance(qqq.columns, pd.MultiIndex): qqq.columns = qqq.columns.get_level_values(0)
    qqq["EMA50"] = qqq["Close"].ewm(span=50, adjust=False).mean()
    qqq_close = float(qqq["Close"].iloc[-1])
    qqq_ema50 = float(qqq["EMA50"].iloc[-1])
    market_ok = qqq_close > qqq_ema50
except Exception:
    market_ok = False
    qqq_close, qqq_ema50 = 0.0, 0.0

if market_ok:
    st.success(f"🟢 **MARKET SAFE:** QQQ (${qqq_close:.2f}) > EMA50 (${qqq_ema50:.2f}) | ตลาดเป็นใจให้ Swing Trade ขาขึ้น")
else:
    st.warning(f"🔴 **MARKET WARNING:** QQQ (${qqq_close:.2f}) < EMA50 (${qqq_ema50:.2f}) | ตลาดพักฐาน ควรลด Position Size ลง 50%")

tab1, tab2 = st.tabs(["🔍 Market Scanner", "🛡️ Position Tracker"])

# -----------------------------------------------------------------------------
# TAB 1: MARKET SCANNER (Text Output + Source Counting)
# -----------------------------------------------------------------------------
with tab1:
    st.subheader("🔍 สแกนคัดกรองหุ้นสเปกเกรด A+ (S&P 500 ทั้งหมด 500 ตัว)")
    st.write("ระบบจะทำการสแกนหุ้นทั้งหมดใน S&P 500 พร้อมระบบ Anti-Flood หน่วงเวลา 0.3s")
    
    run_scan_btn = st.button("🚀 เริ่มรันระบบสแกนประจำวัน (500 หุ้น)", type="primary")

    if run_scan_btn:
        sp_tickers = load_sp500_list()
        st.info(f"กำลังสแกนสัญญาณเทคนิคหุ้น S&P 500 จำนวน {len(sp_tickers)} ตัว...")
        
        progress_bar = st.progress(0)
        matched_stocks = []

        for idx, ticker in enumerate(sp_tickers):
            progress_bar.progress((idx + 1) / len(sp_tickers))
            try:
                df = yf.download(ticker, period="1y", interval="1d", auto_adjust=True, progress=False)
                time.sleep(DELAY_BETWEEN_REQUESTS)
                
                if df.empty or len(df) < 200: continue
                if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)

                df["EMA20"] = df["Close"].ewm(span=20, adjust=False).mean()
                df["EMA50"] = df["Close"].ewm(span=50, adjust=False).mean()
                df["EMA200"] = df["Close"].ewm(span=200, adjust=False).mean()
                df["Vol_SMA20"] = df["Volume"].rolling(20).mean()

                close = float(df["Close"].iloc[-1])
                open_p = float(df["Open"].iloc[-1])
                low = float(df["Low"].iloc[-1])
                vol = float(df["Volume"].iloc[-1])
                vol_sma = float(df["Vol_SMA20"].iloc[-1])
                ema20 = float(df["EMA20"].iloc[-1])
                ema50 = float(df["EMA50"].iloc[-1])
                ema200 = float(df["EMA200"].iloc[-1])
                rvol = round(vol / vol_sma, 2) if vol_sma > 0 else 1.0

                # A+ Setup Filters
                if close > (ema200 * 1.03) and ema20 > ema50 and (low <= (ema20 * 1.008) or low <= (ema50 * 1.008)) and (close > open_p * 1.008) and (close >= ema20) and vol >= (vol_sma * 1.20):
                    matched_stocks.append({
                        "symbol": ticker, "close": round(close, 2), "rvol": rvol,
                        "ema20": round(ema20, 2), "ema50": round(ema50, 2),
                        "initial_sl": round(close * 0.98, 2), "tp1": round(close * 1.05, 2)
                    })
            except Exception:
                continue

        st.success(f"สแกนครบ 500 ตัวเรียบร้อย! พบหุ้นเข้าเกณฑ์ A+ ทั้งหมด {len(matched_stocks)} ตัว")

        if matched_stocks:
            st.write("---")
            st.subheader("🤖 ผลการวิเคราะห์ข่าวสารและความเสี่ยงโดย AI")
            
            scanned_results = []
            for item in matched_stocks:
                sym = item["symbol"]
                news_data = fetch_ticker_news_with_sources(sym, limit=6)
                ai_res = analyze_news_risk_with_ai(sym, news_data, ai_cfg)
                
                status = ai_res.get("risk_status", "PASS")
                action = "🟢 STRONG BUY" if status == "PASS" and item["rvol"] >= 1.5 else ("🟡 BUY" if status == "PASS" else "🔴 REJECT")
                
                item_data = {
                    **item, 
                    **ai_res, 
                    "news_data": news_data, 
                    "action": action
                }
                scanned_results.append(item_data)

            st.session_state["scan_results"] = scanned_results

    # แสดงผลสแกนแบบ Text Output & Detailed Cards
    if "scan_results" in st.session_state:
        results = st.session_state["scan_results"]
        my_stocks = load_my_stocks()
        my_symbols = [s["symbol"].upper() for s in my_stocks]

        for res in results:
            sym = res["symbol"]
            with st.expander(f"📌 **{sym}** | Price: **${res['close']}** | RVOL: **{res['rvol']}x** | Action: **{res['action']}**", expanded=True):
                col_info, col_news = st.columns([1, 1.2])

                with col_info:
                    st.write(f"- **ราคาปิด:** ${res['close']}")
                    st.write(f"- **RVOL (โวลุ่มหนุน):** {res['rvol']}x")
                    st.write(f"- **Stop Loss (-2%):** ${res['initial_sl']}")
                    st.write(f"- **Take Profit 1 (+5%):** ${res['tp1']}")
                    st.write(f"- **AI Risk Status:** `{res.get('risk_status')}`")
                    st.write(f"- **AI Reasoning:** {res.get('reasoning')}")

                    # ปุ่มกด BUY เพื่อบันทึกลง my_stocks.json
                    if sym in my_symbols:
                        st.success("✅ มีหุ้นตัวนี้ในพอร์ตแล้ว")
                    else:
                        if st.button(f"📥 BUY & Add to Portfolio ({sym})", key=f"buy_{sym}"):
                            new_entry = {
                                "symbol": sym,
                                "buy_price": res['close'],
                                "buy_date": datetime.now().strftime("%Y-%m-%d"),
                                "initial_sl": res['initial_sl'],
                                "tp1": res['tp1'],
                                "tp2_lock_sl": round(res['close'] * 1.03, 2),
                                "tp1_hit": False
                            }
                            my_stocks.append(new_entry)
                            save_my_stocks(my_stocks)
                            st.success(f"บันทึก {sym} เข้าพอร์ตเรียบร้อยแล้ว!")
                            st.rerun()

                with col_news:
                    st.markdown("##### 📰 บทความข่าวสารที่โหลดได้แยกตามสำนักข่าว")
                    news_data = res.get("news_data", {})
                    source_counts = news_data.get("source_counts", {})
                    total_news = news_data.get("total_count", 0)

                    st.write(f"**จำนวนข่าวที่โหลดได้ทั้งหมด:** {total_news} บทความ")
                    
                    # แสดงจำนวนข่าวแยกตามแต่ละแหล่งข่าว
                    if source_counts:
                        counts_str = " | ".join([f"**{src}:** {cnt} ข่าว" for src, cnt in source_counts.items()])
                        st.markdown(f"📊 **สถิติรายสำนักข่าว:** {counts_str}")
                    else:
                        st.write("ไม่พบข่าวสารจากสำนักข่าวหลัก")

                    st.write("**ลิงก์หัวข้อข่าวล่าสุด:**")
                    for n in news_data.get("articles", [])[:4]:
                        st.markdown(f"- [{n['publisher']}] [{n['title']}]({n['url']})")

# -----------------------------------------------------------------------------
# TAB 2: POSITION TRACKER
# -----------------------------------------------------------------------------
with tab2:
    st.subheader("🛡️ Portfolio Position Tracker & Exit Strategy")
    
    my_stocks = load_my_stocks()
    if not my_stocks:
        st.info("ยังไม่มีรายการหุ้นในพอร์ต (สามารถกดเพิ่มได้จากหน้า Market Scanner)")
    else:
        st.write(f"จำนวนหุ้นที่ถือครองอยู่: **{len(my_stocks)}** ตัว")
        
        portfolio_results = []
        for stock in my_stocks:
            sym = str(stock["symbol"]).upper()
            buy_p = float(stock["buy_price"])
            
            sl_p = float(stock.get("initial_sl", round(buy_p * 0.98, 2)))
            tp1_p = float(stock.get("tp1", round(buy_p * 1.05, 2)))
            tp2_lock_p = float(stock.get("tp2_lock_sl", round(buy_p * 1.03, 2)))
            tp1_hit = stock.get("tp1_hit", False)

            try:
                ticker = yf.Ticker(sym)
                df = ticker.history(period="3mo")
                if df.empty or len(df) < 20: continue

                if isinstance(df.columns, pd.MultiIndex): 
                    df.columns = df.columns.get_level_values(0)
                
                curr_p = float(df['Close'].iloc[-1])
                df['EMA20'] = df['Close'].ewm(span=20, adjust=False).mean()
                df['EMA50'] = df['Close'].ewm(span=50, adjust=False).mean()
                df['Vol_SMA20'] = df['Volume'].rolling(20).mean()
                
                ema20 = float(df['EMA20'].iloc[-1])
                ema50 = float(df['EMA50'].iloc[-1])
                avg_vol = float(df['Vol_SMA20'].iloc[-1])
                rvol = round(float(df['Volume'].iloc[-1]) / avg_vol, 2) if avg_vol > 0 else 1.0
                pnl = round(((curr_p - buy_p) / buy_p) * 100, 2)
                
                # Rule Engine Exit Strategy
                if not tp1_hit and curr_p <= sl_p:
                    action = "🔴 CUT LOSS (Sell All)"
                elif not tp1_hit and curr_p >= tp1_p:
                    action = "🟢 TAKE PROFIT 1 (Sell 50%)"
                elif tp1_hit and curr_p <= max(tp2_lock_p, ema20):
                    action = "🔵 TAKE PROFIT 2 (Sell 50%)"
                else:
                    action = "⚪ HOLD"
                
                portfolio_results.append({
                    **stock, "symbol": sym, "current_price": round(curr_p, 2),
                    "initial_sl": sl_p, "tp1": tp1_p,
                    "pnl_pct": pnl, "rvol": rvol, "ema20": round(ema20, 2),
                    "action": action
                })
            except Exception as e:
                st.error(f"Error fetching data for {sym}: {e}")

        # Render Portfolio Cards
        for p in portfolio_results:
            with st.container():
                st.markdown(f"### {p['symbol']} | P&L: **{p['pnl_pct']:+.2f}%** | Action: **{p['action']}**")
                col_p1, col_p2, col_p3 = st.columns([1, 1, 1])
                
                with col_p1:
                    st.write(f"- **Entry Price:** ${p['buy_price']}")
                    st.write(f"- **Current Price:** ${p['current_price']}")
                    st.write(f"- **Entry Date:** {p.get('buy_date', 'N/A')}")
                with col_p2:
                    st.write(f"- **Initial SL (-2%):** ${p['initial_sl']}")
                    st.write(f"- **Target TP1 (+5%):** ${p['tp1']}")
                    st.write(f"- **RVOL:** {p['rvol']}x | **EMA20:** ${p['ema20']}")
                with col_p3:
                    if st.button(f"❌ SELL & Close Position ({p['symbol']})", key=f"sell_{p['symbol']}"):
                        log_trade_history(p, action_type=p['action'])
                        updated_my_stocks = [s for s in my_stocks if str(s['symbol']).upper() != p['symbol']]
                        save_my_stocks(updated_my_stocks)
                        st.success(f"ปิดออเดอร์และลบ {p['symbol']} ออกจากพอร์ตแล้ว!")
                        st.rerun()
                st.write("---")