import requests
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

NEWS_API_URL = os.getenv(
    "NEWS_API_URL",
    "http://43.134.96.131:9090/api/newsarticles/search",
)

def fetch_news(
    category: str,
    start_dt: Optional[datetime] = None,
    end_dt: Optional[datetime] = None,
):
    """
    调用外部 API 获取新闻数据
    """
    url = NEWS_API_URL
    headers = {"Content-Type": "application/json"}
    
    # 默认构造过去 24 小时 UTC 时间窗口；实验场景可外部传入固定时间
    if end_dt is None:
        end_dt = datetime.now(timezone.utc)
    if start_dt is None:
        start_dt = end_dt - timedelta(hours=24)
    start_dt_str = start_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    end_dt_str = end_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    
    payload = {
        "keyword": category,
        "category": category,
        "startDateTime": start_dt_str,
        "endDateTime": end_dt_str,
        "sortOrder": "latest",
        "includeContent": False  # 只拿标题摘要，省 token
    }
    
    try:
        print(
            f"🌍 Fetching news category={category}, "
            f"startDateTime={start_dt_str}, endDateTime={end_dt_str}"
        )
        resp = requests.post(url, headers=headers, json=payload, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            # 假设返回的是列表，或者 data 字段里是列表
            # 这里先原样返回，后续观察数据结构微调
            return data
        else:
            return f"Error: API status {resp.status_code}"
    except Exception as e:
        return f"Fetch exception: {str(e)}"

if __name__ == "__main__":
    # 本地测试
    print(fetch_news("AI"))
