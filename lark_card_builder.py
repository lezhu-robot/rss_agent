from agent_graph import NewsBriefing, NewsDigest
import json
from datetime import datetime

def build_cover_card(briefing: NewsBriefing, generated_at: str = None, category: str = "AI") -> str:
    """
    构建飞书早报封面卡片
    新结构：今日头条 + 深度专题按钮
    """
    
    # 0. 动态标题映射
    title_map = {
        "AI": "AI每日新闻",
        "GAMES": "游戏每日新闻",
        "MUSIC": "音乐每日新闻",
        "SHORT_DRAMA": "短剧每日新闻"
    }
    # 默认兜底
    card_title = title_map.get(category, f"☕️ {category} 行业早报")
    
    # 1. 格式化时间字符串
    time_str = datetime.now().strftime('%H:%M')
    if generated_at:
        try:
            if isinstance(generated_at, str):
                dt = datetime.fromisoformat(generated_at)
            else:
                dt = generated_at
            time_str = dt.strftime('%H:%M')
        except:
            pass
    
    # 2. 组装今日头条文本（来自 headlines）
    headlines_md = "**🔥 今日头条**\n"
    for i, headline in enumerate(briefing.headlines, 1):
        headlines_md += f"{i}. [{headline.title}]({headline.url})\n"

    # 3. 组装深度专题按钮（每个 Cluster 一个按钮）
    actions = []
    for cluster in briefing.clusters:
        btn_text = f"👉 {cluster.name} ({len(cluster.items)})"
        action_btn = {
            "tag": "button",
            "text": {
                "tag": "plain_text",
                "content": btn_text
            },
            "type": "default",
            "value": {"command": "expand", "target": cluster.name, "category": category}
        }
        actions.append(action_btn)
    
    # 4. 组装最终 Card JSON
    card = {
        "config": {
            "wide_screen_mode": True
        },
        "header": {
            "template": "blue",
            "title": {
                "content": card_title,
                "tag": "plain_text"
            }
        },
        "elements": [
            {
                "tag": "div",
                "text": {
                    "content": headlines_md,
                    "tag": "lark_md"
                }
            },
            {
                "tag": "hr"
            },
            {
                "tag": "div",
                "text": {
                    "content": "👇 **深度专题 (点击下方按钮展开)**",
                    "tag": "lark_md"
                }
            },
            {
                "tag": "action",
                "actions": actions
            },
            {
                "tag": "note",
                "elements": [
                    {
                        "content": f"⏰ 生成于 {time_str}",
                        "tag": "plain_text"
                    }
                ]
            }
        ]
    }
    
    return json.dumps(card, ensure_ascii=False)


def build_manage_subscribe_card(current_subs: list, all_categories: list, status_msg: str = None) -> str:
    """构建订阅管理卡片（独立于日报卡片）。
    使用互动按钮，每次点击实时保存并推送新卡片。
    """
    # 区分已生效的订阅和正在选的订阅
    active_subs_text = "、".join([cat for cat in (current_subs or []) if cat in all_categories]) or "无"

    # 生成复选框的选项
    options = []
    for category in all_categories:
        options.append({
            "text": {
                "tag": "plain_text",
                "content": category
            },
            "value": category
        })

    elements = [
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"**当前有效订阅：** <font color='green'>{active_subs_text}</font>\n请点击下方按钮直接切换您的订阅领域（实时生效）：",
            },
        },
        {
            "tag": "action",
            "actions": [
                {
                    "tag": "button",
                    "text": {
                        "tag": "plain_text",
                        "content": f"{'☑' if cat in (current_subs or []) else '☐'} {cat}"
                    },
                    "type": "primary" if cat in (current_subs or []) else "default",
                    "value": {
                        "command": "manage_subscribe_toggle",
                        "category": cat
                    }
                }
                for cat in all_categories
            ]
        }
    ]

    if status_msg:
        elements.append({
            "tag": "note",
            "elements": [
                {"tag": "plain_text", "content": status_msg}
            ]
        })

    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "turquoise",
            "title": {
                "content": "订阅管理",
                "tag": "plain_text",
            },
        },
        "elements": elements,
    }
    return json.dumps(card, ensure_ascii=False)


# --- 中文数字编号 ---
_CN_NUMS = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十",
            "十一", "十二", "十三", "十四", "十五"]


def build_digest_card(digest: NewsDigest, category: str = "AI", generated_at: str = None) -> str:
    """
    构建新闻速递卡片（v2 格式）
    结构：大标题 + 编号子要点，风格对标腾讯 AI 新闻速递
    """
    # 0. 动态标题映射
    title_map = {
        "AI": "AI 新闻速递",
        "PGC": "PGC 行业动态",
    }
    card_title = title_map.get(category, f"{category} 新闻速递")

    # 1. 格式化时间字符串
    time_str = datetime.now().strftime('%m-%d %H:%M')
    if generated_at:
        try:
            if isinstance(generated_at, str):
                dt = datetime.fromisoformat(generated_at)
            else:
                dt = generated_at
            time_str = dt.strftime('%m-%d %H:%M')
        except:
            pass

    # 2. 组装速递内容（lark_md 格式）
    content_parts = []
    for i, event in enumerate(digest.events, 1):
        # 大标题：阿拉伯数字 + 加粗 + 超链接（如有 URL）
        headline_text = event.headline
        event_url = getattr(event, 'url', '') or ''
        if event_url:
            content_parts.append(f"**{i}、[{headline_text}]({event_url})**")
        else:
            content_parts.append(f"**{i}、{headline_text}**")
        # 子要点：点号前缀，紧跟标题不换行
        for point in event.points:
            point_text = point.text if hasattr(point, 'text') else str(point)
            content_parts.append(f"· {point_text}")
        content_parts.append("")  # 空行分隔不同事件

    full_content = "\n".join(content_parts).strip()

    # 3. 组装最终 Card JSON
    card = {
        "config": {
            "wide_screen_mode": True
        },
        "header": {
            "template": "blue",
            "title": {
                "content": f"{card_title}  {time_str}",
                "tag": "plain_text"
            }
        },
        "elements": [
            {
                "tag": "div",
                "text": {
                    "content": full_content,
                    "tag": "lark_md"
                }
            },
            {
                "tag": "note",
                "elements": [
                    {
                        "content": f"⏰ 生成于 {time_str}",
                        "tag": "plain_text"
                    }
                ]
            }
        ]
    }

    return json.dumps(card, ensure_ascii=False)
