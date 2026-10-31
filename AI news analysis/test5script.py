import requests
import feedparser
import json
from datetime import datetime
import re
import time

AI_CONFIG = {
    "base_url": "http://192.168.1.242:4000/v1",
    "api_key": "sk-wd0WciDnVko1ZhyOP__zaw",
    "model": "qwen35-fast"

}

# RSS Feeds ที่ทำงานได้
FREE_RSS_FEEDS = {
    "bbc_business": "https://feeds.bbci.co.uk/news/business/rss.xml",
    "bloomberg_markets": "https://feeds.bloomberg.com/markets/news.rss",
}


class FinanceNewsAnalyzer:
    def __init__(self):
        self.news_data = []
        self.analysis_results = []
        
    def fetch_news_from_rss(self, feed_name: str, url: str, limit: int = 5) -> list:
        """ดึงขอมูลข่าวจาก RSS - จำกัดจำนวน"""
        try:
            print(f"Fetching from {feed_name}...")
            headers = {"User-Agent": "Mozilla/5.0"}
            response = requests.get(url, headers=headers, timeout=15)
            response.raise_for_status()
            
            feed = feedparser.parse(response.content)
            articles = []
            
            for entry in feed.entries[:limit]:  # จำกัดตามพารามิเตอร์
                title = entry.get("title") or "No Title"
                summary = entry.get("summary") or entry.get("description") or ""
                
                articles.append({
                    "title": title,
                    "content": summary[:1000],
                    "source": feed.feed.get("title", feed_name),
                    "published_at": entry.get("published") or datetime.now().isoformat(),
                    "url": entry.get("link") or ""
                })
            
            print(f"  ✓ Got {len(articles)} articles")
            return articles
            
        except Exception as e:
            print(f"  ✗ Error: {e}")
            return []
    
    def fetch_all_news(self, total_limit: int = 5):
        """ดึงขาวรวมกันตามจำนวนทีกำหนด"""
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
        """ดึง content จาก response (รองรับ Reasoning Model)"""
        
        choices = result.get("choices", [])
        if not choices:
            return None
        
        message = choices[0].get("message", {})
        
        # Priority 1: content (standard)
        content = message.get("content")
        if content:
            return content.strip()
        
        # Priority 2: reasoning_content (for think models)
        reasoning = message.get("reasoning_content")
        if reasoning:
            return reasoning.strip()
        
        # Priority 3: provider_specific_fields.reasoning
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

**สำคญ**: หลังกการคดิวเคราะห ใหตอบ JSON เทานั้น ไมมขอความอื่น
"""

        try:
            response = requests.post(
                f"{AI_CONFIG['base_url']}/chat/completions",
                headers={
                    "Authorization": f"Bearer {AI_CONFIG['api_key']}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": AI_CONFIG["model"],
                    "messages": [
                        {"role": "system", "content": "คณเปนผชวยเชี่ยวชาญดานการวเคราะหตลาดการเงิน ตอบ JSON เทานั้น"},
                        {"role": "user", "content": prompt}
                    ],
                    "temperature": 0.3,
                    "max_tokens": 1000,
                },
                timeout=300
            )
            
            print(f"  Status: {response.status_code}")
            
            if response.status_code != 200:
                print(f"  Error: {response.text[:200]}")
                return self._default_analysis(news_item)
            
            result = response.json()
            
            # Debug: แสดง response เต็ม
            print(f"  Response keys: {result.keys()}")
            if "choices" in result:
                msg = result["choices"][0].get("message", {})
                print(f"  Message keys: {msg.keys()}")
                print(f"  Content: {msg.get('content', 'None')[:100]}")
                print(f"  Reasoning: {msg.get('reasoning_content', 'None')[:100]}")
            
            # ดึง content
            ai_content = self.extract_content_from_response(result)
            
            if not ai_content:
                print(f"  ⚠️  AI returned None content")
                return self._default_analysis(news_item)
            
            print(f"  ✓ Content length: {len(ai_content)} chars")
            
            # Parse JSON
            json_match = re.search(r'\{.*\}', ai_content, re.DOTALL)
            if json_match:
                try:
                    analysis = json.loads(json_match.group())
                    analysis["original_news"] = news_item
                    return analysis
                except json.JSONDecodeError as e:
                    print(f"  ⚠️  JSON parse error: {e}")
                    return self._default_analysis(news_item)
            
            return self._default_analysis(news_item)
            
        except Exception as e:
            print(f"  ⚠️  Exception: {e}")
            return self._default_analysis(news_item)
    
    def _default_analysis(self, news_item: dict) -> dict:
        """Default analysis when AI fails"""
        return {
            "stock_symbols": [],
            "sentiment_score": 0,
            "sentiment_label": "neutral",
            "price_impact": "low",
            "confidence": 0,
            "reasoning": "Analysis failed",
            "key_factors": [],
            "recommendation": "hold",
            "original_news": news_item
        }
    
    def analyze_all_news(self):
        """วเคราะหขาวทงหมด"""
        print(f"\n🤖 Analyzing {len(self.news_data)} news...")
        
        for i, news in enumerate(self.news_data):
            print(f"\n[{i+1}/{len(self.news_data)}] {news.get('title', '')[:60]}...")
            analysis = self.analyze_with_ai(news)
            self.analysis_results.append(analysis)
            time.sleep(2)
        
        return self.analysis_results
    
    def get_top_stocks(self, min_score: float = 0.3, limit: int = 10) -> list:
        """หาหุ้นทมีโอกาสราคาขึ้น"""
        
        stock_analysis = {}
        
        for result in self.analysis_results:
            for symbol in result.get("stock_symbols", []):
                if symbol not in stock_analysis:
                    stock_analysis[symbol] = {
                        "symbol": symbol,
                        "total_score": 0,
                        "count": 0,
                        "sentiments": [],
                        "news_count": 0
                    }
                
                score = result.get("sentiment_score", 0)
                if score > 0:
                    stock_analysis[symbol]["total_score"] += score
                    stock_analysis[symbol]["count"] += 1
                    stock_analysis[symbol]["sentiments"].append(score)
                    stock_analysis[symbol]["news_count"] += 1
        
        for symbol in stock_analysis:
            data = stock_analysis[symbol]
            if data["count"] > 0:
                data["avg_score"] = data["total_score"] / data["count"]
                data["max_score"] = max(data["sentiments"])
            else:
                data["avg_score"] = 0
                data["max_score"] = 0
        
        top_stocks = [s for s in stock_analysis.values() if s["avg_score"] >= min_score]
        top_stocks.sort(key=lambda x: x["avg_score"], reverse=True)
        
        return top_stocks[:limit]
    
    def save_results(self, filename: str = "analysis_results.json"):
        """บันทกผล"""
        with open(filename, "w", encoding="utf-8") as f:
            json.dump({
                "timestamp": datetime.now().isoformat(),
                "total_news": len(self.news_data),
                "total_analyzed": len(self.analysis_results),
                "top_stocks": self.get_top_stocks(),
                "all_results": self.analysis_results
            }, f, indent=2, ensure_ascii=False)
        
        print(f"✅ Results saved to {filename}")


def main():
    print("=" * 60)
    print("📊 Finance News Analyzer - TEST MODE (5 articles)")
    print("=" * 60)
    
    analyzer = FinanceNewsAnalyzer()
    
    # 1. ดึงขาว (5 บทความ)
    print("\n[1/4] Fetching 5 news articles...")
    analyzer.fetch_all_news(total_limit=5)
    
    if not analyzer.news_data:
        print("⚠️  No news. Using demo.")
        analyzer.news_data = [{
            "title": "Apple Announces Record Earnings",
            "content": "Apple reported record quarterly earnings.",
            "source": "Reuters",
            "published_at": datetime.now().isoformat(),
            "url": "https://example.com"
        }]
    
    # 2. วเคราะห
    print(f"\n[2/4] Analyzing {len(analyzer.news_data)} news...")
    analyzer.analyze_all_news()
    
    # 3. หาหุ้น
    print("\n[3/4] Finding top stocks...")
    top_stocks = analyzer.get_top_stocks(min_score=0.3, limit=10)
    
    # 4. แสดงผล
    print("\n" + "=" * 60)
    print("🏆 TOP STOCKS")
    print("=" * 60)
    
    for i, stock in enumerate(top_stocks, 1):
        print(f"\n{i}. {stock['symbol']}")
        print(f"   Score: {stock['avg_score']:.2f}")
        print(f"   News: {stock['news_count']}")
    
    if not top_stocks:
        print("\n⚠️  No stocks found with positive sentiment")
    
    # 5. บันทก
    print("\n[4/4] Saving...")
    analyzer.save_results()
    
    print("\n✅ Complete!")


if __name__ == "__main__":
    main()