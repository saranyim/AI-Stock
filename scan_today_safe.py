import sys
import os
import time
import json
import re
import pandas as pd
import yfinance as yf
import requests

# บังคับให้ Output Stream รองรับภาษาไทยบน Windows Task Scheduler
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

DELAY_BETWEEN_REQUESTS = 0.3

# --- 1. Load Configurations ---
def load_config(config_path="config.json") -> dict:
    if not os.path.exists(config_path):
        print(f"⚠️ Warning: '{config_path}' not found. Using fallback local configuration.")
        return {
            "ai": {
                "base_url": "http://localhost:8000/v1",
                "api_key": "",
                "model": "qwen35-think"
            }
        }
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)

CONFIG = load_config()
AI_CONFIG = CONFIG.get("ai", {})

# --- 2. Load S&P 500 Tickers ---
print("[INFO] กำลังโหลดรายชื่อหุ้น S&P 500 จากไฟล์ Local...")
try:
    df_sp500 = pd.read_csv("sp500_companies.csv")
    col_name = "Symbol" if "Symbol" in df_sp500.columns else "Ticker"
    sp500_tickers = (
        df_sp500[col_name].astype(str).str.replace(".", "-", regex=False).tolist()
    )
    print(f"[SUCCESS] โหลดรายชื่อสำเร็จทั้งหมด {len(sp500_tickers)} หุ้นจากไฟล์ sp500_companies.csv")
except FileNotFoundError:
    print("[WARNING] ไม่พบไฟล์ 'sp500_companies.csv' ในเครื่อง ใช้รายชื่อสำรอง...")
    sp500_tickers = [
        "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "COST", "AMD", "PYPL", "QCOM"
    ]
except Exception as e:
    print(f"[ERROR] เกิดข้อผิดพลาดในการอ่านไฟล์: {e}")
    sys.exit(1)

print("\n[SAFE MODE] เริ่มสแกนประจำวัน (เปิดระบบ Delay ชะลอเวลาการดึงข้อมูล)...")

# --- 3. Market Filter (QQQ > EMA50) ---
try:
    qqq = yf.download("QQQ", period="1y", interval="1d", auto_adjust=True, progress=False)
    if isinstance(qqq.columns, pd.MultiIndex):
        qqq.columns = qqq.columns.get_level_values(0)
    qqq["EMA50"] = qqq["Close"].ewm(span=50, adjust=False).mean()

    qqq_close = float(qqq["Close"].iloc[-1])
    qqq_ema50 = float(qqq["EMA50"].iloc[-1])
    market_ok = qqq_close > qqq_ema50
except Exception as e:
    print(f"[ERROR] ไม่สามารถดึงข้อมูล QQQ ได้: {e}")
    market_ok = False
    qqq_close, qqq_ema50 = 0.0, 0.0

if not market_ok:
    print(f"\n[MARKET WARNING] QQQ (${qqq_close:.2f}) อยู่ต่ำกว่า EMA50 (${qqq_ema50:.2f})[cite: 3]")
    print("[INFO] สภาพตลาดภาพรวมเป็นขาลง/พักฐาน เพิ่มความระมัดระวังในการเข้าซื้อ...\n[cite: 3]")
else:
    print(f"\n[MARKET FILTER PASSED] QQQ (${qqq_close:.2f}) ยืนเหนือ EMA50 (${qqq_ema50:.2f})\n[cite: 4]")

# --- 4. Technical Indicator Scanner (BUY & SELL Signals) ---
print("กำลังสแกนหาหุ้นทั้งฝั่ง BUY (A+ Setup) และฝั่ง SELL (Bearish Breakdown)...")
buy_matched_stocks = []
sell_matched_stocks = []

for idx, ticker in enumerate(sp500_tickers, 1):
    if idx % 50 == 0:
        print(f"   ...สแกนผ่านไปแล้ว {idx}/{len(sp500_tickers)} หุ้น")

    try:
        df = yf.download(
            ticker, period="1y", interval="1d", auto_adjust=True, progress=False
        )
        time.sleep(DELAY_BETWEEN_REQUESTS)

        if df.empty or len(df) < 200:
            continue
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        # คำนวณ Indicators
        df["EMA20"] = df["Close"].ewm(span=20, adjust=False).mean()
        df["EMA50"] = df["Close"].ewm(span=50, adjust=False).mean()
        df["EMA200"] = df["Close"].ewm(span=200, adjust=False).mean()
        df["Vol_SMA20"] = df["Volume"].rolling(20).mean()

        close = float(df["Close"].iloc[-1])
        open_p = float(df["Open"].iloc[-1])
        high = float(df["High"].iloc[-1])
        low = float(df["Low"].iloc[-1])
        vol = float(df["Volume"].iloc[-1])
        vol_sma = float(df["Vol_SMA20"].iloc[-1])

        ema20 = float(df["EMA20"].iloc[-1])
        ema50 = float(df["EMA50"].iloc[-1])
        ema200 = float(df["EMA200"].iloc[-1])
        rvol = round(vol / vol_sma, 2) if vol_sma > 0 else 1.0

        stock_info = {
            "symbol": ticker,
            "close": round(close, 2),
            "open": round(open_p, 2),
            "high": round(high, 2),
            "low": round(low, 2),
            "ema20": round(ema20, 2),
            "ema50": round(ema50, 2),
            "ema200": round(ema200, 2),
            "rvol": rvol,
        }

        # 🟢 เงื่อนไข BUY A+ Setup
        buy_cond1 = close > (ema200 * 1.03)
        buy_cond2 = ema20 > ema50
        buy_cond3 = (
            (low <= (ema20 * 1.008) or low <= (ema50 * 1.008))
            and (close > open_p * 1.008)
            and (close >= ema20)
        )
        buy_cond4 = vol >= (vol_sma * 1.20)

        if buy_cond1 and buy_cond2 and buy_cond3 and buy_cond4:
            stock_info.update({
                "tp1": round(close * 1.05, 2),
                "sl_initial": round(close * 0.98, 2),
                "sl_step2": round(close * 1.03, 2),
                "tech_type": "BUY"
            })
            buy_matched_stocks.append(stock_info)

        # 🔴 เงื่อนไข SELL / Breakdown Setup (ราคาหลุด EMA20 และ EMA50 หรือ หลุด EMA200 พร้อมโวลุ่ม)
        sell_cond1 = (close < ema20 and close < ema50) or (close < ema200)
        sell_cond2 = ema20 < ema50
        sell_cond3 = close < open_p  # แท่งแดง
        sell_cond4 = vol >= (vol_sma * 1.15)  # แรงขายหนาแน่น

        if sell_cond1 and sell_cond2 and sell_cond3 and sell_cond4:
            stock_info.update({"tech_type": "SELL"})
            sell_matched_stocks.append(stock_info)

    except Exception:
        continue

print(f"\n[SCAN COMPLETE]")
print(f"  - พบหุ้นเข้าสูตรซื้อ (BUY Candidate): {len(buy_matched_stocks)} ตัว")
print(f"  - พบหุ้นเข้าสูตรขาย (SELL Warning): {len(sell_matched_stocks)} ตัว")


# --- 5. Targeted News Fetcher & Local AI Risk Analyzer ---
class FinanceNewsAnalyzer:
    def __init__(self, ai_config: dict):
        self.ai_config = ai_config

    def fetch_news(self, symbol: str, limit: int = 4) -> list:
        try:
            t_obj = yf.Ticker(symbol)
            yf_news = t_obj.news
            if not yf_news:
                return []
            cleaned = []
            for item in yf_news[:limit]:
                cleaned.append({
                    "title": item.get("title") or item.get("headline") or "",
                    "summary": (item.get("summary") or item.get("text") or "")[:500],
                    "publisher": item.get("provider", {}).get("displayName") or "Yahoo"
                })
            return cleaned
        except Exception:
            return []

    def _strip_think_tags(self, text: str) -> str:
        if not text:
            return ""
        return re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()

    def analyze_risk_with_ai(self, symbol: str, news_list: list, tech_type: str) -> dict:
        if not news_list:
            return {
                "symbol": symbol,
                "risk_status": "PASS" if tech_type == "BUY" else "SELL_ALERT",
                "reasoning": "ไม่พบข่าวสารใหม่ - ใช้องค์ประกอบทางเทคนิคเป็นหลัก",
                "has_immediate_red_flags": False
            }

        news_text = "\n".join([f"- [{n['publisher']}] {n['title']}: {n['summary']}" for n in news_list])
        
        prompt = f"""
คุณคือระบบวิเคราะห์ความเสี่ยงและแนวโน้มจากข่าวสาร (News Sentiment Analyst) สำหรับ Swing Trade

วิเคราะห์ข่าวของหุ้น: **{symbol}** (สถานะทางเทคนิคเบื้องต้น: {tech_type}) ต่อไปนี้:
{news_text}

โจทย์การประเมิน (เน้นระยะสั้น 1-7 วัน):
1. มีข่าวลบรุนแรงทันทีหรือไม่? (เช่น งบแย่กว่าคาดมาก, โดน Downgrade, โดนฟ้องร้อง, ผู้บริหารลาออก)
2. มีข่าวบวกดันราคาหรือไม่? (เช่น งบดีกว่าคาดมาก, ปรับเพิ่มเป้ารายได้, โดน Upgrade)

ตอบกลับเป็น JSON เท่านั้น:
{{
    "symbol": "{symbol}",
    "risk_status": "PASS", 
    "news_sentiment": "POSITIVE",
    "reasoning": "เหตุผลสั้นๆ 1-2 ประโยค",
    "has_immediate_red_flags": false
}}

*คำอธิบาย risk_status:*
- สำหรับหุ้นฝั่ง BUY: "PASS" (ไม่มีข่าวลบ), "WARNING" (ข่าวปนกัน), "REJECT" (ข่าวลบแรง ห้ามซื้อ)
- สำหรับหุ้นฝั่ง SELL: "SELL_ALERT" (หลุดเทรนด์), "STRONG_SELL" (หลุดเทรนด์ + ข่าวลบแรงวิกฤต)
"""
        for attempt in range(2):
            try:
                response = requests.post(
                    f"{self.ai_config.get('base_url')}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.ai_config.get('api_key', '')}",
                        "Content-Type": "application/json"
                    },
                    json={
                        "model": self.ai_config.get("model", "qwen35-think"),
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.1,
                        "max_tokens": 600
                    },
                    timeout=120
                )
                if response.status_code == 200:
                    choices = response.json().get("choices", [])
                    if choices:
                        raw_content = choices[0].get("message", {}).get("content", "")
                        raw_content = self._strip_think_tags(raw_content)
                        json_match = re.search(r'\{.*\}', raw_content, re.DOTALL)
                        if json_match:
                            res = json.loads(json_match.group())
                            res["symbol"] = symbol
                            return res
            except Exception:
                time.sleep(1)

        return {
            "symbol": symbol,
            "risk_status": "WARNING" if tech_type == "BUY" else "SELL_ALERT",
            "reasoning": "AI Scan Timeout / Parsing Error",
            "has_immediate_red_flags": False
        }

ai_analyzer = FinanceNewsAnalyzer(AI_CONFIG)

# --- 6. Process BUY Candidates ---
buy_results = []
if buy_matched_stocks:
    print("\n🟢 กำลังให้ Local AI ตรวจสอบข่าวสารหุ้นฝั่ง BUY Candidates...")
    for stock in buy_matched_stocks:
        sym = stock["symbol"]
        print(f"   - ตรวจสอบ BUY: {sym}...")
        news = ai_analyzer.fetch_news(sym)
        eval_res = ai_analyzer.analyze_risk_with_ai(sym, news, tech_type="BUY")
        
        risk_status = eval_res.get("risk_status", "PASS")
        has_red_flags = eval_res.get("has_immediate_red_flags", False)

        if has_red_flags or risk_status == "REJECT":
            action = "🔴 REJECT (ข่าวลบแรง)"
        elif risk_status == "PASS" and stock["rvol"] >= 1.5:
            action = "🟢 STRONG BUY"
        elif risk_status == "PASS":
            action = "🟡 BUY (โวลุ่มปานกลาง)"
        elif risk_status == "WARNING":
            action = "🟠 BUY WITH CAUTION"
        else:
            action = "⚪ HOLD / IGNORE"

        buy_results.append({**stock, **eval_res, "action": action})

    buy_priority = {"🟢 STRONG BUY": 1, "🟡 BUY (โวลุ่มปานกลาง)": 2, "🟠 BUY WITH CAUTION": 3, "⚪ HOLD / IGNORE": 4, "🔴 REJECT (ข่าวลบแรง)": 5}
    buy_results.sort(key=lambda x: buy_priority.get(x["action"], 99))


# --- 7. Process SELL Candidates ---
sell_results = []
if sell_matched_stocks:
    print("\n🔴 กำลังให้ Local AI ตรวจสอบข่าวสารหุ้นฝั่ง SELL Warnings...")
    for stock in sell_matched_stocks:
        sym = stock["symbol"]
        print(f"   - ตรวจสอบ SELL: {sym}...")
        news = ai_analyzer.fetch_news(sym)
        eval_res = ai_analyzer.analyze_risk_with_ai(sym, news, tech_type="SELL")
        
        has_red_flags = eval_res.get("has_immediate_red_flags", False)

        if has_red_flags or stock["rvol"] >= 2.0:
            action = "🚨 STRONG SELL (ขายทันที)"
        else:
            action = "🔻 SELL (หลุดเทรนด์)"

        sell_results.append({**stock, **eval_res, "action": action})

    sell_priority = {"🚨 STRONG SELL (ขายทันที)": 1, "🔻 SELL (หลุดเทรนด์)": 2}
    sell_results.sort(key=lambda x: sell_priority.get(x["action"], 99))


# --- 8. Display Results ---
print("\n" + "=" * 95)
print("🏆 DAILY MARKET SCAN RESULTS (BUY & SELL RECOMMENDATIONS)")
print("=" * 95)

if not market_ok:
    print("⚠️ [MARKET WARNING] ดัชนี QQQ ต่ำกว่า EMA50 (ตลาดพักฐาน) เพิ่มความระมัดระวังฝั่งซื้อ[cite: 3]\n")

# ตารางฝั่ง BUY
print("🟢 [BUY OPPORTUNITIES] หุ้นสัญญาณซื้อเกรด A+")
print(f"{'Symbol':<8} | {'Price':<8} | {'RVOL':<7} | {'Risk':<8} | {'Action':<22} | {'Reasoning'}")
print("-" * 95)
if buy_results:
    for res in buy_results:
        print(f"{res['symbol']:<8} | ${res['close']:<7} | {res['rvol']}x    | {res.get('risk_status','PASS'):<8} | {res['action']:<22} | {res.get('reasoning','')[:75]}")
else:
    print("   ไม่พบหุ้นเข้าสูตรซื้อ A+ ในวันนี้")

print("\n" + "-" * 95)

# ตารางฝั่ง SELL
print("🔴 [SELL / EXIT WARNINGS] หุ้นสัญญาณขาย/ความเสี่ยงสูง (สำหรับเช็กหุ้นในพอร์ต)")
print(f"{'Symbol':<8} | {'Price':<8} | {'RVOL':<7} | {'Status':<10} | {'Action':<25} | {'Reasoning'}")
print("-" * 95)
if sell_results:
    for res in sell_results[:15]:  # แสดงสูงสุด 15 ตัวแรก
        print(f"{res['symbol']:<8} | ${res['close']:<7} | {res['rvol']}x    | {res.get('risk_status','SELL'):<10} | {res['action']:<25} | {res.get('reasoning','')[:75]}")
else:
    print("   ไม่พบหุ้นที่มีสัญญาณขายแรงวิกฤตในวันนี้")

print("=" * 95)

# ส่งออกไฟล์ผลลัพธ์แยกหมวดหมู่
output_data = {
    "market_ok": market_ok,
    "buy_candidates": buy_results,
    "sell_warnings": sell_results
}
output_file = "scan_results_today.json"
with open(output_file, "w", encoding="utf-8") as f:
    json.dump(output_data, f, indent=2, ensure_ascii=False)

print(f"\n[COMPLETE] บันทึกผลการวิเคราะห์ลงไฟล์ '{output_file}' เรียบร้อยแล้ว!")