import json
from datetime import datetime, timedelta, timezone
from group_config_loader import load_group_configs
from group_push_service import _collect_group_news, _deduplicate_articles
from group_message_formatter import format_group_news_message
from messaging import send_message

def preview_last_24h(chat_id: str):
    configs = load_group_configs()
    config = next((c for c in configs if c["chat_id"] == chat_id), None)
    if not config:
        print("Config not found.")
        return

    now_utc = datetime.now(timezone.utc)
    # 扩大搜索窗口到 24 小时，保证能捞到新闻来预览效果
    window_start = now_utc - timedelta(hours=24)
    window_end = now_utc

    print(f"Fetching news from {window_start} to {window_end} for keywords: {config.get('keyword_groups')}")
    news_by_category, errors = _collect_group_news(
        selected_categories=config["preferences"],
        keyword_groups=config.get("keyword_groups", []),
        keyword_group_mode=config.get("keyword_group_mode", "OR"),
        window_start=window_start,
        window_end=window_end,
    )

    if errors:
        print("Errors fetching:", errors)

    deduplicated = _deduplicate_articles(news_by_category)
    has_news = any(articles for articles in deduplicated.values())
    
    if not has_news:
        print("Still no news found matching these keywords in the last 24 hours!")
        return
        
    print(f"Found news! Processing {(deduplicated)}...")
    message_text = format_group_news_message(
        news_by_category=deduplicated,
        window_start=window_start,
        window_end=window_end,
        timezone_name=config["timezone"],
    )
    
    print("Pushing preview to group...")
    sent = send_message(chat_id, message_text, receive_id_type="chat_id")
    if sent:
        print("✅ Preview sent successfully!")
    else:
        print("❌ Failed to send preview.")

if __name__ == "__main__":
    preview_last_24h("oc_39ec90b2c09a72ae32fa16fd5a3dc77c")
