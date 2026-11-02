import os
import json
import re
import time
from datetime import datetime
import requests
import yfinance as yf
import pandas as pd
import numpy as np

# --- 1. Load Configuration ---
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

# --- 2. Load S&P 500 Tickers from CSV ---
def load_sp500_tickers(csv_path="sp500_companies.csv") -> list:
    """อ่านรายชื่อ Symbol จาก sp500_companies.csv และแปลง format สำหรับ yfinance"""
    if not os.path.exists(csv_path):
        print(f"⚠️ Warning: '{csv_path}' not found. Using default Watchlist.")
        return ["BAX", "HCA", "IQV", "NXPI", "PYPL", "QCOM", "CNC", "TEL", "CDNS", "ITW", "STLD", "HLT"]
    
    try:
        df = pd.read_csv(csv_path)
        if 'Symbol' in df.columns:
            # แปลงจุด (.) ใน Ticker เป็นขีด (-) เช่น BRK.B -> BRK-B เพื่อให้ yfinance อ่านได้ถูกต้อง
            tickers = df['Symbol'].dropna().str.replace('.', '-', regex=False).tolist()
            return tickers
        else:
            print("⚠️ 'Symbol' column not found in CSV.")
            return []
    except Exception as e:
        print(f"⚠️ Error reading CSV: {e}")
        return []

# --- 3. News Analyzer Class (Targeted & Defensive) ---
class FinanceNewsAnalyzer:
    def __init__(self, ai_config: dict):
        self.ai_config = ai_config
        self.news_data = {}         # {symbol: [news_items]}
        self.analysis_results = {}  # {symbol: analysis_dict}
        
    def fetch_news_for_ticker(self, symbol: str, limit: int = 5) -> list:
        """ดึงข่าวเฉพาะเจาะจงราย Ticker ผ่าน yfinance"""
        try:
            ticker = yf.Ticker(symbol)
            yf_news = ticker.news
            if not yf_news:
                return []
            
            cleaned_news = []
            for item in yf_news[:limit]:
                title = item.get("title") or item.get("headline") or ""
                summary = item.get("summary") or item.get("text") or ""
                provider = item.get("provider", {}).get("displayName") or item.get("publisher") or "Yahoo Finance"
                pub_time = item.get("providerPublishTime") or item.get("pubDate") or ""
                
                cleaned_news.append({
                    "title": title,
                    "summary": summary[:600],
                    "publisher": provider,
                    "published_at": str(pub_time)
                })
            return cleaned_news
        except Exception as e:
            print(f"  ⚠️ Error fetching news for {symbol}: {e}")
            return []

    def fetch_all_targeted_news(self, symbols: list):
        print(f"\n📰 Fetching targeted news for {len(symbols)} tickers...")
        for sym in symbols:
            news = self.fetch_news_for_ticker(sym)
            self.news_data[sym] = news
            print(f"  - {sym}: found {len(news)} articles")
            time.sleep(0.3)

    def _strip_think_tags(self, text: str) -> str:
        """กำจัดแท็ก <think>...</think> จาก Reasoning Models (เช่น Qwen 3.5)"""
        if not text:
            return ""
        text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
        return text.strip()

    def extract_content_from_response(self, result: dict) -> str:
        choices = result.get("choices", [])
        if not choices:
            return ""
        message = choices[0].get("message", {})
        content = message.get("content") or message.get("reasoning_content") or ""
        return self._strip_think_tags(content)

    def analyze_ticker_with_ai(self, symbol: str, news_list: list) -> dict:
        """วิเคราะห์ข่าวสารโดยมุ่งเน้น Short-term Risk Filter (1-7 วัน) สำหรับ Swing Trade"""
        if not news_list:
            return {
                "symbol": symbol,
                "risk_status": "PASS",
                "news_sentiment": "NEUTRAL",
                "impact_scope": "NONE",
                "reasoning": "ไม่พบข่าวสารล่าสุด (No news found) - ให้ใช้องค์ประกอบเทคนิค (EMA/RVOL) เป็นหลัก",
                "has_immediate_red_flags": False
            }

        news_text_block = "\n".join([
            f"- [{n['publisher']}] {n['title']}: {n['summary']}"
            for n in news_list
        ])

        prompt = f"""
คุณคือระบบกรองความเสี่ยงจากข่าวสาร (News Risk Filter) สำหรับนักเทรดสไตล์ Swing Trade (ถือครอง 3-10 วัน)

จงวิเคราะห์ข่าวของหุ้น: **{symbol}** ต่อไปนี้:
{news_text_block}

**โจทย์การประเมิน (เน้นระยะสั้น 1-7 วันเท่านั้น):**
1. มี Red Flags ลบแรงทันทีหรือไม่? (เช่น งบการเงินแย่กว่าคาดมาก, โดน Downgrade, โดนฟ้องร้อง, ผู้บริหารลาออก)
2. มี Catalyst บวกระยะสั้นหรือไม่? (เช่น งบดีกว่าคาดมาก, ปรับเพิ่มเป้ารายได้, โดน Upgrade)
3. หากเป็นข่าวระยะยาว (เช่น แผน 3-5 ปี) ให้ระบุ impact_scope เป็น "LONG_TERM" และไม่ต้องนำมากดดันคะแนนระยะสั้น

ตอบกลับในรูปแบบ JSON เท่านั้น ตามโครงสร้างนี้:
{{
    "symbol": "{symbol}",
    "risk_status": "PASS", 
    "news_sentiment": "POSITIVE", 
    "impact_scope": "SHORT_TERM",
    "reasoning": "อธิบายเหตุผลสั้นๆ 1-2 ประโยค",
    "has_immediate_red_flags": false
}}

*คำอธิบายค่า:*
- risk_status: "PASS" (ไม่มีข่าวลบ), "WARNING" (ข่าวปนกัน/ต้องระวัง), "REJECT" (มีข่าวลบแรง ห้ามเข้าซื้อ)
- news_sentiment: "POSITIVE", "NEUTRAL", "NEGATIVE"
- impact_scope: "SHORT_TERM", "LONG_TERM", "NONE"
- has_immediate_red_flags: true (ถ้ามีข่าวลบวิกฤต), false (ถ้าไม่มี)
"""

        # System Retry Loop 2 ครั้งกรณี Timeout หรือ JSON Parse ผิดพลาด
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
                        "max_tokens": 800
                    },
                    timeout=180
                )
                
                if response.status_code == 200:
                    raw_content = self.extract_content_from_response(response.json())
                    json_match = re.search(r'\{.*\}', raw_content, re.DOTALL)
                    if json_match:
                        analysis = json.loads(json_match.group())
                        analysis["symbol"] = symbol
                        return analysis
            except Exception as e:
                print(f"  ⚠️ Attempt {attempt+1} failed for {symbol}: {e}")
                time.sleep(2)

        # Fallback กรณี AI ล้มเหลวทั้งหมด
        return {
            "symbol": symbol,
            "risk_status": "WARNING",
            "news_sentiment": "NEUTRAL",
            "impact_scope": "NONE",
            "reasoning": "AI Analysis Failed / Timeout",
            "has_immediate_red_flags": False
        }

    def analyze_all_tickers(self):
        print(f"\n🤖 Analyzing news with Local AI ({self.ai_config.get('model')})...")
        for symbol, news_list in self.news_data.items():
            print(f"  - Analyzing {symbol}...")
            analysis = self.analyze_ticker_with_ai(symbol, news_list)
            self.analysis_results[symbol] = analysis
        return self.analysis_results


# --- 4. Technical & Volume Trend Checker ---
class EMATrendChecker:
    def __init__(self):
        self.trend_results = {}

    def check_trend_and_volume(self, symbol: str, period: str = "3mo") -> dict:
        try:
            ticker = yf.Ticker(symbol)
            df = ticker.history(period=period)
            if df.empty or len(df) < 50:
                return {"symbol": symbol, "status": "No Data", "trend": "N/A", "ema_signal": "Hold"}

            # EMA Calculation
            df['EMA20'] = df['Close'].ewm(span=20, adjust=False).mean()
            df['EMA50'] = df['Close'].ewm(span=50, adjust=False).mean()
            df['Vol_SMA20'] = df['Volume'].rolling(window=20).mean()

            current_price = df['Close'].iloc[-1]
            ema20 = df['EMA20'].iloc[-1]
            ema50 = df['EMA50'].iloc[-1]
            
            # RVOL (Relative Volume) Calculation
            current_vol = df['Volume'].iloc[-1]
            avg_vol20 = df['Vol_SMA20'].iloc[-1]
            rvol = round(current_vol / avg_vol20, 2) if avg_vol20 > 0 else 1.0

            trend = "Neutral"
            signal = "Hold"
            if ema20 > ema50 and current_price > ema20:
                trend, signal = "Bullish (Up)", "Buy"
            elif ema20 < ema50 and current_price < ema20:
                trend, signal = "Bearish (Down)", "Sell"

            return {
                "symbol": symbol,
                "current_price": round(current_price, 2),
                "ema20": round(ema20, 2),
                "ema50": round(ema50, 2),
                "rvol": rvol,
                "trend": trend,
                "ema_signal": signal
            }
        except Exception as e:
            return {"symbol": symbol, "status": f"Error: {e}", "trend": "N/A", "ema_signal": "Hold"}

    def check_all_stocks(self, stocks: list) -> dict:
        print("\n📈 Checking EMA Trends & Volume (RVOL)...")
        for stock in stocks:
            self.trend_results[stock] = self.check_trend_and_volume(stock)
        return self.trend_results


# --- 5. Main Workflow ---
def main():
    print("=" * 80)
    print("📊 Quantitative & News Sentiment Swing Trade Evaluator")
    print("=" * 80)

    # 1. โหลดรายชื่อหุ้น S&P 500 จาก sp500_companies.csv (ถ้าไม่มีจะใช้ Watchlist ตั้งต้น)
    all_tickers = load_sp500_tickers("sp500_companies.csv")
    
    # หากต้องการรันสแกนทั้งตลาด S&P 500 สามารถใช้ all_tickers ได้เลย
    # ตัวอย่างนี้กำหนด Ticker ตัวอย่างที่ผ่าน Technical Filter เพื่อแสดงผลการทำงาน
    target_symbols = ["BAX", "HCA", "IQV", "NXPI", "PYPL", "QCOM", "CNC", "TEL", "CDNS"]
    
    print(f"🔍 Processing Watchlist ({len(target_symbols)} tickers)...")

    # 2. ตรวจสอบ EMA & RVOL ทางเทคนิคก่อน
    ema_checker = EMATrendChecker()
    tech_results = ema_checker.check_all_stocks(target_symbols)

    # กรองเฉพาะหุ้นที่มีสัญญาณ EMA เป็น Buy เพื่อส่งไปวิเคราะห์ข่าวต่อ (ช่วยประหยัดเวลา)
    qualified_symbols = [
        sym for sym, data in tech_results.items() 
        if data.get("ema_signal") == "Buy"
    ]

    if not qualified_symbols:
        print("⚠️ No stocks passed the Technical Buy Filter (Price > EMA20 > EMA50).")
        qualified_symbols = target_symbols  # Fallback to check all if none passed

    # 3. ดึงข่าวเฉพาะเจาะจงราย Ticker และวิเคราะห์ความเสี่ยงด้วย Local AI
    news_analyzer = FinanceNewsAnalyzer(ai_config=AI_CONFIG)
    news_analyzer.fetch_all_targeted_news(qualified_symbols)
    news_results = news_analyzer.analyze_all_tickers()

    # 4. สรุปและประเมินผลรวม (Consolidation)
    print("\n" + "=" * 80)
    print("🏆 FINAL SWING TRADE RECOMMENDATIONS")
    print("=" * 80)

    final_recommendations = []

    for symbol in qualified_symbols:
        tech = tech_results.get(symbol, {})
        news = news_results.get(symbol, {})

        ema_sig = tech.get("ema_signal", "Hold")
        rvol = tech.get("rvol", 0.0)
        risk_status = news.get("risk_status", "PASS")
        has_red_flags = news.get("has_immediate_red_flags", False)

        # Logic ตัดสินใจ Action
        if has_red_flags or risk_status == "REJECT":
            action = "🔴 REJECT (ข่าวลบแรง)"
        elif ema_sig == "Buy" and risk_status == "PASS" and rvol >= 1.5:
            action = "🟢 STRONG BUY"
        elif ema_sig == "Buy" and risk_status == "PASS":
            action = "🟡 BUY (โวลุ่มปานกลาง)"
        elif ema_sig == "Buy" and risk_status == "WARNING":
            action = "🟠 BUY WITH CAUTION"
        else:
            action = "⚪ HOLD / IGNORE"

        final_recommendations.append({
            "symbol": symbol,
            "price": tech.get("current_price", "N/A"),
            "trend": tech.get("trend", "N/A"),
            "rvol": f"{rvol}x",
            "risk_status": risk_status,
            "action": action,
            "reasoning": news.get("reasoning", "")[:90]
        })

    # เรียงลำดับตาม Action
    action_priority = {
        "🟢 STRONG BUY": 1,
        "🟡 BUY (โวลุ่มปานกลาง)": 2,
        "🟠 BUY WITH CAUTION": 3,
        "⚪ HOLD / IGNORE": 4,
        "🔴 REJECT (ข่าวลบแรง)": 5
    }
    final_recommendations.sort(key=lambda x: action_priority.get(x["action"], 99))

    # แสดงผลตารางสรุป
    print(f"\n{'Symbol':<8} | {'Price':<8} | {'RVOL':<7} | {'Risk':<8} | {'Action':<22} | {'Reasoning'}")
    print("-" * 95)
    for rec in final_recommendations:
        print(f"{rec['symbol']:<8} | ${rec['price']:<7} | {rec['rvol']:<7} | {rec['risk_status']:<8} | {rec['action']:<22} | {rec['reasoning']}")

    print("\n" + "=" * 80)
    print("✅ Analysis Completed Successfully!")
    print("=" * 80)

    # บันทึกเป็น JSON Output
    output_filename = "final_swing_recommendations.json"
    with open(output_filename, "w", encoding="utf-8") as f:
        json.dump(final_recommendations, f, indent=2, ensure_ascii=False)
    print(f"📄 Results exported to '{output_filename}'")

if __name__ == "__main__":
    main()