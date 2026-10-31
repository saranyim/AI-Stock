import requests
import json
from datetime import datetime

AI_CONFIG = {
    "base_url": "http://192.168.1.242:4000/v1",
    "api_key": "sk-wd0WciDnVko1ZhyOP__zaw",
    "model": "qwen35-think"

}

def debug_ai_response():
    """ตรวจสอบโครงสร้าง Response จริงจาก AI"""
    
    print("=" * 60)
    print("🔍 AI Response Structure Debug")
    print("=" * 60)
    
    test_prompt = "Hello, respond with 'OK' only"
    
    # Test 1: ตรวจสอบ Response แบบละเอียด
    print("\n[Test 1] Full Response Structure...")
    
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
                    {"role": "system", "content": "You are helpful"},
                    {"role": "user", "content": test_prompt}
                ],
                "temperature": 0.1,
                "max_tokens": 50
            },
            timeout=120
        )
        
        print(f"Status Code: {response.status_code}")
        print(f"Headers: {dict(response.headers)}")
        print(f"\nFull Response JSON:")
        print(json.dumps(response.json(), indent=2, ensure_ascii=False))
        
        # Check different paths
        result = response.json()
        
        print("\n" + "=" * 60)
        print("🔎 Checking Content Paths:")
        print("=" * 60)
        
        # Path 1: choices[0].message.content
        content1 = result.get("choices", [{}])[0].get("message", {}).get("content")
        print(f"choices[0].message.content: {content1}")
        
        # Path 2: choices[0].delta.content (streaming)
        content2 = result.get("choices", [{}])[0].get("delta", {}).get("content")
        print(f"choices[0].delta.content: {content2}")
        
        # Path 3: message.content directly
        content3 = result.get("message", {}).get("content")
        print(f"message.content: {content3}")
        
        # Path 4: text field
        content4 = result.get("text")
        print(f"text: {content4}")
        
        # Path 5: response field
        content5 = result.get("response")
        print(f"response: {content5}")
        
        # Check if streaming
        is_stream = result.get("object") == "chat.completion.chunk"
        print(f"\nIs streaming response: {is_stream}")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
    
    # Test 2: ตรวจสอบ Streaming
    print("\n" + "=" * 60)
    print("[Test 2] Test Streaming Response...")
    print("=" * 60)
    
    try:
        response = requests.post(
            f"{AI_CONFIG['base_url']}/chat/completions",
            headers={
                "Authorization": f"Bearer {AI_CONFIG['api_key']}",
                "Content-Type": "application/json"
            },
            json={
                "model": AI_CONFIG["model"],
                "messages": [{"role": "user", "content": "Hello"}],
                "stream": True,
                "max_tokens": 10
            },
            timeout=120,
            stream=True
        )
        
        print(f"Status: {response.status_code}")
        print("Streaming chunks:")
        
        for line in response.iter_lines():
            if line:
                chunk = line.decode('utf-8')
                print(f"  {chunk[:200]}")
                
    except Exception as e:
        print(f"❌ Streaming Error: {e}")
    
    print("\n" + "=" * 60)
    print("✅ Debug Complete!")
    print("=" * 60)

if __name__ == "__main__":
    debug_ai_response()