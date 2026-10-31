# ตรวจสอบโครงสร้าง response
response = requests.post(
    "http://192.168.1.242:4000/v1/chat/completions",
    headers={"Authorization": "Bearer your-key"},
    json={
        "model": "qwen35-think",
        "messages": [{"role": "user", "content": "Hello"}],
        "max_tokens": 10
    }
)

import json
print(json.dumps(response.json(), indent=2))