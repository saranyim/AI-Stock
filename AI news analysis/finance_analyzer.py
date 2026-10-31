import requests
import feedparser
import json
from datetime import datetime
import re
import time
import yfinance as yf
import pandas as pd
import numpy as np
from collections import defaultdict

# --- Configuration ---
AI_CONFIG = {
    "base_url": "http://192.168.1.242:4000/v1",
    "api_key": "sk-wd0WciDnVko1ZhyOP__zaw",
    "model": "qwen35-fast"

}
FREE_RSS_FEEDS = {
    "bbc_business": "https://feeds.bbci.co.uk/news/business/rss.xml",
    "bloomberg_markets": "https://feeds.bloomberg.com/markets/news.rss",
    "seeking_alpha": "https://seekingalpha.com/feed",
    "investing_com": "https://www.investing.com/rss/news.rss",
}

# --- Class FinanceNewsAnalyzer ---
class FinanceNewsAnalyzer:
    def __init__(self):
        self.news_data = []
        self.analysis_results = []
        
    def fetch_news_from_rss(self, feed_name: str, url: str, limit: int = 10) -> list:
        """ดึงขอมูลข่าวจาก RSS"""
        try:
            headers = {"User-Agent": "Mozilla/5.0"}
            response = requests.get(url, headers=headers, timeout=15)
            response.raise_for_status()
            feed = feedparser.parse(response.content)
            articles = []
            
            for entry in feed.entries[:limit]:
                title = entry.get("title") or "No Title"
                summary = entry.get("summary") or entry.get("description") or ""
                articles.append({
                    "title": title,
                    "content": summary[:1000],
                    "source": feed.feed.get("title", feed_name),
                    "published_at": entry.get("published") or datetime.now().isoformat(),
                    "url": entry.get("link") or ""
                })
            return articles
        except Exception as e:
            print(f"  ✗ Error {feed_name}: {e}")
            return []
    
    def fetch_all_news(self, total_limit: int = 50):
        """ดึงขาวรวมก่น"""
        print(f"\n📰 Fetching {total_limit} news articles...")
        articles_collected = 0
        for feed_name, url in FREE_RSS_FEEDS.items():
            if articles_collected >= total_limit:
                break
            remaining = total_limit - articles_collected
            news = self.fetch_news_from_rss(feed_name, url, limit=remaining)
            self.news_data.extend(news)
            articles_collected += len(news)
            time.sleep(2)
        print(f"✅ Total: {len(self.news_data)} articles")
        return self.news_data
    
    def extract_content_from_response(self, result: dict) -> str:
        """ดึง content (รองรับ Reasoning Model)"""
        choices = result.get("choices", [])
        if not choices:
            return None
        message = choices[0].get("message", {})
        content = message.get("content")
        if content:
            return content.strip()
        reasoning = message.get("reasoning_content")
        if reasoning:
            return reasoning.strip()
        provider = message.get("provider_specific_fields", {})
        reasoning2 = provider.get("reasoning")
        if reasoning2:
            return reasoning2.strip()
        return None
    
    def analyze_with_ai(self, news_item: dict) -> dict:
        """วเคราะหข่าวดวย AI"""
        title = news_item.get("title") or "No Title"
        content = news_item.get("content") or ""
        source = news_item.get("source") or "Unknown"
        
        prompt = f"""
คณเปนผชวยเชี่ยวชาญดานการวเคราะหตลาดการเงิน จงวเคราะหขาวตอไปนี้:

**หัวขอขาว**: {title}
**เนือหา**: {content[:500]}
**แหลงขาว**: {source}

จงวเคราะหและตอบในรปูแบบ JSON เทานั้น:
{{
    "stock_symbols": ["SYMBOL1"],
    "sentiment_score": 0.8,
    "sentiment_label": "positive",
    "price_impact": "high",
    "confidence": 0.85,
    "reasoning": "เหตผผลสั้นๆ 2-3 ประโยค",
    "key_factors": ["ปจจัย 1"],
    "recommendation": "buy"
}}
ตอบ JSON เทานั้น
"""

        try:
            response = requests.post(
                f"{AI_CONFIG['base_url']}/chat/completions",
                headers={"Authorization": f"Bearer {AI_CONFIG['api_key']}", "Content-Type": "application/json"},
                json={"model": AI_CONFIG["model"], "messages": [{"role": "user", "content": prompt}], "max_tokens": 1000},
                timeout=300
            )
            
            if response.status_code != 200:
                return self._default_analysis(news_item)
            
            result = response.json()
            ai_content = self.extract_content_from_response(result)
            
            if not ai_content:
                return self._default_analysis(news_item)
            
            json_match = re.search(r'\{.*\}', ai_content, re.DOTALL)
            if json_match:
                try:
                    analysis = json.loads(json_match.group())
                    analysis["original_news"] = news_item
                    return analysis
                except:
                    pass
            return self._default_analysis(news_item)
        except Exception as e:
            return self._default_analysis(news_item)
    
    def _default_analysis(self, news_item: dict) -> dict:
        return {
            "stock_symbols": [], "sentiment_score": 0, "sentiment_label": "neutral",
            "price_impact": "low", "confidence": 0, "reasoning": "Analysis failed",
            "key_factors": [], "recommendation": "hold", "original_news": news_item
        }
    
    def analyze_all_news(self):
        print(f"\n🤖 Analyzing {len(self.news_data)} news...")
        for i, news in enumerate(self.news_data):
            print(f"[{i+1}/{len(self.news_data)}] {news.get('title', '')[:50]}...")
            analysis = self.analyze_with_ai(news)
            self.analysis_results.append(analysis)
            time.sleep(2)
        return self.analysis_results
    
    def get_aggregated_stocks(self, min_score: float = 0.3) -> dict:
        """รวมผลวเคราะหตามหุ้น"""
        stock_analysis = {}
        for result in self.analysis_results:
            for symbol in result.get("stock_symbols", []):
                if symbol not in stock_analysis:
                    stock_analysis[symbol] = {
                        "symbol": symbol, "total_score": 0, "count": 0,
                        "sentiments": [], "news_count": 0, "recommendations": []
                    }
                score = result.get("sentiment_score", 0)
                if score > 0:
                    stock_analysis[symbol]["total_score"] += score
                    stock_analysis[symbol]["count"] += 1
                    stock_analysis[symbol]["sentiments"].append(score)
                    stock_analysis[symbol]["news_count"] += 1
                    stock_analysis[symbol]["recommendations"].append(result.get("recommendation", "hold"))
        
        for symbol in stock_analysis:
            data = stock_analysis[symbol]
            if data["count"] > 0:
                data["avg_score"] = data["total_score"] / data["count"]
                data["max_score"] = max(data["sentiments"])
                # หา recommendation ส่วนใหญ
                rec_counts = defaultdict(int)
                for rec in data["recommendations"]:
                    rec_counts[rec] += 1
                data["final_recommendation"] = max(rec_counts, key=rec_counts.get)
            else:
                data["avg_score"] = 0
                data["final_recommendation"] = "hold"
        
        return {k: v for k, v in stock_analysis.items() if v["avg_score"] >= min_score}

# --- Class EMA Trend Checker ---
class EMATrendChecker:
    def __init__(self):
        self.trend_results = {}
    
    def check_trend(self, symbol: str, period: str = "3mo") -> dict:
        """ตรวจสอบเทรนด์ดวย EMA20 และ EMA50"""
        try:
            print(f"  🔍 Fetching data for {symbol}...")
            ticker = yf.Ticker(symbol)
            df = ticker.history(period=period)
            
            if df.empty:
                return {"symbol": symbol, "status": "No Data", "trend": "N/A"}
            
            # คำนวน EMA
            df['EMA20'] = df['Close'].ewm(span=20, adjust=False).mean()
            df['EMA50'] = df['Close'].ewm(span=50, adjust=False).mean()
            
            current_price = df['Close'].iloc[-1]
            ema20 = df['EMA20'].iloc[-1]
            ema50 = df['EMA50'].iloc[-1]
            
            # ตรวจสอบเทรนด์
            trend = "Neutral"
            signal = "Hold"
            
            if ema20 > ema50 and current_price > ema20:
                trend = "Bullish (Up)"
                signal = "Buy"
            elif ema20 < ema50 and current_price < ema20:
                trend = "Bearish (Down)"
                signal = "Sell"
            else:
                trend = "Consolidating"
                signal = "Hold"
            
            return {
                "symbol": symbol,
                "current_price": round(current_price, 2),
                "ema20": round(ema20, 2),
                "ema50": round(ema50, 2),
                "trend": trend,
                "ema_signal": signal,
                "price_vs_ema20": "Above" if current_price > ema20 else "Below",
                "ema20_vs_ema50": "Above" if ema20 > ema50 else "Below"
            }
            
        except Exception as e:
            return {"symbol": symbol, "status": "Error", "error": str(e), "trend": "N/A"}
    
    def check_all_stocks(self, stocks: list) -> dict:
        """ตรวจสอบทกุหุ้น"""
        print("\n📈 Checking EMA Trends...")
        for stock in stocks:
            self.trend_results[stock] = self.check_trend(stock)
        return self.trend_results

# --- Main Function ---
def main():
    print("=" * 70)
    print("📊 Finance News Analyzer + EMA Trend Confirmation")
    print("=" * 70)
    
    # 1. ดึงขาว
    analyzer = FinanceNewsAnalyzer()
    analyzer.fetch_all_news(total_limit=50)
    
    if not analyzer.news_data:
        print("⚠️ No news. Using demo.")
        analyzer.news_data = [{"title": "Apple Earnings Beat", "content": "Apple reported great earnings.", "source": "Demo", "published_at": datetime.now().isoformat(), "url": ""}]
    
    # 2. วเคราะห
    analyzer.analyze_all_news()
    
    # 3. รวมผล
    aggregated = analyzer.get_aggregated_stocks(min_score=0.3)
    
    if not aggregated:
        print("\n⚠️ No stocks found with positive sentiment.")
        return
    
    # 4. ตรวจสอบ EMA
    ema_checker = EMATrendChecker()
    stock_symbols = list(aggregated.keys())
    ema_checker.check_all_stocks(stock_symbols)
    
    # 5. สรุปผลลพัธ (Dashboard)
    print("\n" + "=" * 70)
    print("🏆 FINAL RECOMMENDATIONS (News + EMA Confirmation)")
    print("=" * 70)
    
    final_recommendations = []
    
    for symbol, data in aggregated.items():
        trend_data = ema_checker.trend_results.get(symbol, {})
        
        news_rec = data.get("final_recommendation", "hold")
        ema_rec = trend_data.get("ema_signal", "Hold")
        
        # ตัดสินใจ
        if news_rec == "buy" and ema_rec == "Buy":
            final_action = "🟢 STRONG BUY"
        elif news_rec == "buy" and ema_rec == "Hold":
            final_action = "🟡 BUY (Wait for EMA)"
        elif news_rec == "sell" or ema_rec == "Sell":
            final_action = "🔴 SELL"
        else:
            final_action = "⚪ HOLD"
        
        final_recommendations.append({
            "symbol": symbol,
            "news_score": data["avg_score"],
            "news_rec": news_rec,
            "trend": trend_data.get("trend", "N/A"),
            "ema_signal": ema_rec,
            "price": trend_data.get("current_price", "N/A"),
            "action": final_action,
            "reason": data.get("reasoning", "")[:100]
        })
    
    # เรียงตาม Action
    action_order = {"🟢 STRONG BUY": 1, "🟡 BUY (Wait for EMA)": 2, "⚪ HOLD": 3, "🔴 SELL": 4}
    final_recommendations.sort(key=lambda x: action_order.get(x["action"], 5))
    
    # แสดงผล
    print(f"\n{'Symbol':<10} | {'Score':<6} | {'News Rec':<10} | {'Trend':<15} | {'Action':<20} | {'Price'}")
    print("-" * 70)
    
    for rec in final_recommendations:
        print(f"{rec['symbol']:<10} | {rec['news_score']:.2f}   | {rec['news_rec']:<10} | {rec['trend']:<15} | {rec['action']:<20} | ${rec['price']}")
    
    print("\n" + "=" * 70)
    print("✅ Analysis Complete!")
    print("=" * 70)
    
    # บันทก
    with open("final_recommendations.json", "w", encoding="utf-8") as f:
        json.dump(final_recommendations, f, indent=2, ensure_ascii=False)
    print("📄 Saved to final_recommendations.json")

if __name__ == "__main__":
    main()