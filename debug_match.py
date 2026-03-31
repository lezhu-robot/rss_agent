from group_config_loader import load_group_configs
from group_news_client import fetch_group_news
from datetime import datetime, timezone, timedelta
import re

configs = load_group_configs()
config = configs[0]
now_utc = datetime.now(timezone.utc)
window_start = now_utc - timedelta(hours=24)
res = fetch_group_news(None, window_start, now_utc, None, "OR")

targets = ["OpenAI 押注", "特朗普组建科技"]
kws = config["keyword_groups"][0]

for r in res:
    full_text = (r["title"] + " " + r["summary"] + " " + r.get("sourceName", "")).lower()
    for t in targets:
        if t.lower() in full_text:
            print(f"\n=========\nMatched article: {r['title']}")
            for kw in kws:
                if kw.lower() in full_text:
                    idx = full_text.find(kw.lower())
                    print(f"  -> Triggers on keyword: [{kw}]\n     Snippet context: ...{full_text[max(0, idx-15):min(len(full_text), idx+15)]}...")
