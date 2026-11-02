import os
import json
import re
import time
from datetime import datetime
import requests
import yfinance as yf
import pandas as pd

# --- 1. Load Configurations ---
def load_config(config_path="config.json") -> dict:
    if not os.path.exists(config_path):
        return {"ai": {"base_url": "http://localhost:8000/v1", "api_key": "", "model": "qwen35-think"}}
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)

CONFIG = load_config()
AI_CONFIG = CONFIG.get("ai", {})

def load_and_enrich_my_stocks(json_path="my_stocks.json") -> list:
    """อ่าน my_stocks.json และคำนวณค่า SL/TP ตามกฎ Swing Trade อัตโนมัติ"""
    if not os.path.exists(json_path):
        print(f"⚠️ ไม่พบไฟล์ {json_path} กรุณาสร้างไฟล์ข้อมูลหุ้นก่อนครับ")
        return []
    
    with open(json_path, "r", encoding="utf-8") as f:
        raw_stocks = json.load(f)
        
    enriched_stocks = []
    for stock in raw_stocks:
        buy_p = float(stock["buy_price"])
        
        # คำนวณอัตโนมัติหากไม่มีการระบุค่ามาใน JSON
        initial_sl = stock.get("initial_sl", round(buy_p * 0.98, 2))       # Stop Loss -2%
        tp1 = stock.get("tp1", round(buy_p * 1.05, 2))                    # Take Profit 1 +5%
        tp2_lock_sl = stock.get("tp2_lock_sl", round(buy_p * 1.03, 2))    # Lock Profit SL +3%
        tp1_hit = stock.get("tp1_hit", False)                             # สถานะขายไม้แรก
        
        enriched_stocks.append({
            "symbol": stock["symbol"],
            "buy_price": buy_p,
            "buy_date": stock.get("buy_date", ""),
            "shares": stock.get("shares", 0),
            "initial_sl": initial_sl,
            "tp1": tp1,
            "tp2_lock_sl": tp2_lock_sl,
            "tp1_hit": tp1_hit
        })
        
    return enriched_stocks

# --- 2. Technical & Exit Strategy Checker ---
def analyze_position_technical(stock_info: dict) -> dict:
    symbol = stock_info["symbol"]
    buy_price = stock_info["buy_price"]
    initial_sl = stock_info["initial_sl"]
    tp1 = stock_info["tp1"]
    tp2_lock_sl = stock_info["tp2_lock_sl"]
    tp1_hit = stock_info.get("tp1_hit", False)

    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period="3mo")
        if df.empty or len(df) < 50:
            return {"symbol": symbol, "status": "Error: Insufficient Data"}

        df['EMA20'] = df['Close'].ewm(span=20, adjust=False).mean()
        df['EMA50'] = df['Close'].ewm(span=50, adjust=False).mean()

        current_price = df['Close'].iloc[-1]
        ema20 = df['EMA20'].iloc[-1]
        ema50 = df['EMA50'].iloc[-1]
        prev_close = df['Close'].iloc[-2]

        pnl_pct = ((current_price - buy_price) / buy_price) * 100

        # --- Rule Engine การตัดสินใจขาย ---
        action = "HOLD"
        action_color = "⚪"
        reason = "ราคาเคลื่อนไหวในกรอบปกติ ถือครองตามแผน"

        # 1. เช็ค Stop Loss แรกเริ่ม (-2%)
        if not tp1_hit and current_price <= initial_sl:
            action = "CUT LOSS (Sell All)"
            action_color = "🔴"
            reason = f"ราคาปิด (${current_price:.2f}) หลุด Stop Loss แรกเริ่ม (-2%) ที่ ${initial_sl:.2f}"

        # 2. เช็คการถึงเป้า TP1 (+5%)
        elif not tp1_hit and current_price >= tp1:
            action = "TAKE PROFIT 1 (Sell 50%)"
            action_color = "🟢"
            reason = f"ราคาถึงเป้า TP1 (+5%) ที่ ${tp1:.2f} แนะนำขายล็อกกำไร 50% และตั้ง SL ล็อกกำไรไม้สองที่ ${tp2_lock_sl:.2f}"

        # 3. กรณี TP1 ผ่านไปแล้ว เช็ค Trailing Stop ล็อกกำไร (+3% หรือ Trailing ตาม EMA20)
        elif tp1_hit:
            current_sl = max(tp2_lock_sl, ema20)  # ใช้ค่าที่สูงกว่าระหว่าง +3% Lock หรือ EMA20
            if current_price <= current_sl:
                action = "TAKE PROFIT 2 (Sell Remaining 50%)"
                action_color = "🔵"
                reason = f"ราคาปิด (${current_price:.2f}) หลุด Trailing Stop (${current_sl:.2f}) ขายส่วนที่เหลือ"
            else:
                action = "LET PROFIT RUN"
                action_color = "🟢"
                reason = f"ไม้แรกขายแล้ว ไม้ที่สองถือตาม Trailing Stop EMA20 (${ema20:.2f})"

        # 4. เช็คสัญญาณเตือนหลุด EMA20 (กรณีราคาตกแรงก่อนชน SL)
        elif current_price < ema20 and current_price < prev_close:
            action_color = "🟡"
            reason += f" | ⚠️ เตือน: ราคาหลุด EMA20 (${ema20:.2f}) โมเมนตัมเริ่มอ่อนตัว"

        return {
            "symbol": symbol,
            "buy_price": buy_price,
            "current_price": round(current_price, 2),
            "pnl_pct": round(pnl_pct, 2),
            "ema20": round(ema20, 2),
            "ema50": round(ema50, 2),
            "initial_sl": initial_sl,
            "tp1": tp1,
            "action": f"{action_color} {action}",
            "tech_reason": reason,
            "tp1_hit": tp1_hit
        }
    except Exception as e:
        return {"symbol": symbol, "status": f"Error: {e}"}

# --- 3. AI Emergency News Risk Checker ---
def fetch_ticker_news(symbol: str) -> list:
    try:
        ticker = yf.Ticker(symbol)
        news = ticker.news or []
        cleaned = []
        for n in news[:3]:
            cleaned.append({
                "title": n.get("title") or n.get("headline") or "",
                "summary": (n.get("summary") or n.get("text") or "")[:400]
            })
        return cleaned
    except:
        return []

def check_emergency_news_with_ai(symbol: str, news_list: list) -> dict:
    if not news_list:
        return {"has_emergency_red_flag": False, "news_reason": "ไม่มีข่าวใหม่"}

    news_text = "\n".join([f"- {n['title']}: {n['summary']}" for n in news_list])
    prompt = f"""
คุณคือระบบเฝ้าระวังความเสี่ยงวิกฤต (Emergency Exit Filter) สำหรับหุ้นที่ถือครองอยู่: **{symbol}**

ข่าวล่าสุด:
{news_text}

ประเมินว่ามี "ข่าวร้ายวิกฤตทันที" ที่ต้อง **ขายทิ้งทันทีโดยไม่รอ Technical SL** หรือไม่? 
(เช่น โดนฟ้องร้องคดีอาญา, งบออกแย่กว่าคาดมหาศาล, โดน Downgrade แบบ panic, ผู้บริหารฉ้อโกง)

ตอบกลับเป็น JSON เท่านั้น:
{{
    "has_emergency_red_flag": false,
    "news_reason": "อธิบายสั้นๆ 1 ประโยค"
}}
"""
    try:
        text_resp = requests.post(
            f"{AI_CONFIG.get('base_url')}/chat/completions",
            headers={"Authorization": f"Bearer {AI_CONFIG.get('api_key', '')}", "Content-Type": "application/json"},
            json={"model": AI_CONFIG.get("model", "qwen35-think"), "messages": [{"role": "user", "content": prompt}], "temperature": 0.1},
            timeout=120
        ).json()["choices"][0]["message"]["content"]
        
        # Clean <think> tags
        text_resp = re.sub(r'<think>.*?</think>', '', text_resp, flags=re.DOTALL).strip()
        match = re.search(r'\{.*\}', text_resp, re.DOTALL)
        if match:
            return json.loads(match.group())
    except Exception:
        pass
    return {"has_emergency_red_flag": False, "news_reason": "AI Scan Failed"}

# --- 4. Main Workflow ---
def main():
    print("=" * 95)
    print("🛡️ PORTFOLIO POSITION TRACKER & EXIT STRATEGY EVALUATOR")
    print("=" * 95)

    my_stocks = load_and_enrich_my_stocks("my_stocks.json")
    if not my_stocks:
        return

    print(f"📦 Tracking {len(my_stocks)} positions in portfolio...\n")
    
    tracking_results = []

    for stock in my_stocks:
        sym = stock["symbol"]
        print(f"🔎 Checking {sym} (Entry: ${stock['buy_price']})...")
        
        # 1. เช็ค Technical & Price Levels
        tech_eval = analyze_position_technical(stock)
        
        # 2. เช็ค Emergency News ด้วย Local AI
        news_list = fetch_ticker_news(sym)
        news_eval = check_emergency_news_with_ai(sym, news_list)

        # 3. รวมการตัดสินใจ (Consolidation)
        final_action = tech_eval.get("action", "HOLD")
        reason = tech_eval.get("tech_reason", "")

        if news_eval.get("has_emergency_red_flag", False):
            final_action = "🚨 EMERGENCY EXIT (Sell All)"
            reason = f"ข่าวร้ายวิกฤต: {news_eval.get('news_reason')}"

        tracking_results.append({
            "symbol": sym,
            "buy_price": stock['buy_price'],
            "current_price": tech_eval.get('current_price', 0),
            "sl_price": tech_eval.get('initial_sl', 0),
            "tp1_price": tech_eval.get('tp1', 0),
            "pnl": f"{tech_eval.get('pnl_pct', 0):+.2f}%",
            "action": final_action,
            "reason": reason
        })
        time.sleep(0.5)

    # แสดงผลตารางติดตามพอร์ต
    print("\n" + "=" * 95)
    print(f"{'Symbol':<7} | {'Entry':<8} | {'Current':<8} | {'SL (-2%)':<8} | {'TP1 (+5%)':<9} | {'P&L (%)':<8} | {'Action':<28}")
    print("-" * 95)
    
    for r in tracking_results:
        print(f"{r['symbol']:<7} | ${r['buy_price']:<7} | ${r['current_price']:<7} | ${r['sl_price']:<7} | ${r['tp1_price']:<8} | {r['pnl']:<8} | {r['action']}")
        print(f"  └─ Reason: {r['reason']}\n")
        
    print("=" * 95)
    print("✅ Tracking completed. Execute sell orders accordingly if any actions triggered.")

if __name__ == "__main__":
    main()