"""
飞书文档写入辅助类
用于将机器人生内容写入飞书云文档（支持Wiki）
"""
import requests
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

class FeishuDocWriter:
    """飞书文档写入器"""
    
    BASE_URL = "https://open.larkoffice.com/open-apis"
    MAX_CHILDREN_PER_REQUEST = 50
    SUMMARY_MAX_LEN = 180
    DESCRIPTION_MAX_LEN = 220
    
    def __init__(self, app_id: str, app_secret: str):
        self.app_id = app_id
        self.app_secret = app_secret
        self._tenant_access_token: Optional[str] = None
        self._token_expire_time: Optional[datetime] = None
        self._wiki_doc_cache: Dict[str, str] = {}  # wiki_token -> document_id

    def get_tenant_access_token(self) -> str:
        """获取应用访问凭证"""
        if (self._tenant_access_token and self._token_expire_time and 
            datetime.now() < self._token_expire_time):
            return self._tenant_access_token
        
        url = f"{self.BASE_URL}/auth/v3/tenant_access_token/internal"
        payload = {"app_id": self.app_id, "app_secret": self.app_secret}
        
        try:
            response = requests.post(url, json=payload, timeout=10)
            result = response.json()
            if result.get("code") != 0:
                print(f"❌ 获取access_token失败: {result.get('msg')}")
                return ""
            
            self._tenant_access_token = result["tenant_access_token"]
            expire_seconds = result.get("expire", 7200) - 300
            self._token_expire_time = datetime.now() + timedelta(seconds=expire_seconds)
            return self._tenant_access_token
        except Exception as e:
            print(f"❌ 获取access_token异常: {e}")
            return ""

    def _get_headers(self) -> Dict[str, str]:
        token = self.get_tenant_access_token()
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8"
        }

    def get_document_id_from_wiki(self, wiki_token: str) -> str:
        """从Wiki token获取实际的文档ID"""
        if wiki_token in self._wiki_doc_cache:
            return self._wiki_doc_cache[wiki_token]
        
        url = f"{self.BASE_URL}/wiki/v2/spaces/get_node"
        params = {"token": wiki_token}
        
        try:
            response = requests.get(url, headers=self._get_headers(), params=params, timeout=10)
            result = response.json()
            
            if result.get("code") != 0:
                print(f"❌ 获取Wiki节点信息失败: {result.get('msg')}")
                return ""
            
            node = result.get("data", {}).get("node", {})
            obj_token = node.get("obj_token")
            
            if obj_token:
                self._wiki_doc_cache[wiki_token] = obj_token
                return obj_token
            return ""
        except Exception as e:
            print(f"❌ 获取Wiki ID异常: {e}")
            return ""

    def find_first_callout_index(self, document_id: str) -> int:
        """查找第一个高亮块（Callout, block_type=19）的位置"""
        url = f"{self.BASE_URL}/docx/v1/documents/{document_id}/blocks/{document_id}/children"
        try:
            # 获取前50个块，假设高亮块在开头
            response = requests.get(url, headers=self._get_headers(), params={"page_size": 50})
            if response.status_code != 200:
                return -1
                
            items = response.json().get("data", {}).get("items", [])
            for i, block in enumerate(items):
                # block_type: 19=Callout, 18=Quote, 17=Equation? 
                # 文档通常用 Callout (19) 做提示
                if block.get("block_type") in [17, 18, 19]:
                    print(f"📍 找到高亮块 (Type {block.get('block_type')}) at index {i}")
                    return i + 1
            return -1
        except Exception as e:
            print(f"⚠️ find_first_callout_index exception: {e}")
            return -1

    def append_blocks(self, document_id: str, children: List[Dict], index: int = -1) -> bool:
        """批量写入Block (默认追加到末尾，指定 index 则插入)"""
        block_id = document_id
        url = f"{self.BASE_URL}/docx/v1/documents/{document_id}/blocks/{block_id}/children"
        
        payload = {
            "children": children,
            "index": index
        }
        
        try:
            response = requests.post(url, headers=self._get_headers(), json=payload, timeout=20)
            result = response.json()
            if result.get("code") != 0:
                print(f"❌ 写入Block失败: {result.get('msg')}")
                field_violations = result.get("error", {}).get("field_violations")
                if field_violations:
                    print(f"❌ 字段校验详情: {field_violations}")
                return False
            return True
        except Exception as e:
            print(f"❌ 写入Block异常: {e}")
            return False

    def append_blocks_in_batches(
        self,
        document_id: str,
        children: List[Dict[str, Any]],
        index: int = -1,
        batch_size: int = MAX_CHILDREN_PER_REQUEST
    ) -> bool:
        """分批写入，避免单次 children 超过飞书接口上限。"""
        if not children:
            return True

        current_index = index
        total = len(children)
        for start in range(0, total, batch_size):
            end = min(start + batch_size, total)
            chunk = children[start:end]
            chunk_index = current_index if current_index != -1 else -1
            print(f"🧩 写入批次 {start // batch_size + 1}: blocks {start + 1}-{end}/{total}")
            if not self.append_blocks(document_id, chunk, index=chunk_index):
                return False
            if current_index != -1:
                current_index += len(chunk)
        return True

    def create_heading_block(self, text: str, level: int = 1) -> Dict:
        """构建标题Block"""
        block_type = 2 + level # 3=H1, 4=H2...
        return {
            "block_type": block_type,
            f"heading{level}": {
                "elements": [{"text_run": {"content": text}}],
                "style": {}
            }
        }

    def create_text_block(self, text: str) -> Dict:
        """构建普通文本Block"""
        return self.create_rich_text_block([{"text_run": {"content": text}}])

    def create_rich_text_block(self, elements: List[Dict[str, Any]]) -> Dict:
        """构建富文本Block"""
        return {
            "block_type": 2,
            "text": {
                "elements": elements,
                "style": {}
            }
        }

    @staticmethod
    def truncate_text(text: Any, max_len: int) -> str:
        if text is None:
            return ""
        clean_text = str(text).strip()
        if len(clean_text) <= max_len:
            return clean_text
        return f"{clean_text[:max_len].rstrip()}..."

    @staticmethod
    def safe_score(item: Dict[str, Any]) -> float:
        raw_score = item.get("score", 0)
        try:
            return float(raw_score)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def safe_score_value(raw_score: Any) -> float:
        try:
            return float(raw_score)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def normalize_http_url(url: Any) -> str:
        if not url:
            return ""
        cleaned = str(url).strip()
        if cleaned.startswith("http://") or cleaned.startswith("https://"):
            return cleaned
        return ""

    def create_news_item_block(
        self,
        idx: int,
        title: Any,
        summary: Any = "",
        url: Any = "",
        score: Any = None
    ) -> Dict:
        """构建新闻条目Block（标题支持链接）"""
        safe_title = str(title).strip() if title else "无标题"
        safe_summary = self.truncate_text(summary, self.SUMMARY_MAX_LEN)
        safe_url = self.normalize_http_url(url)

        title_run: Dict[str, Any] = {"content": safe_title}
        if safe_url:
            title_run["text_element_style"] = {"link": {"url": safe_url}}

        line1 = f"{idx}. "
        if score is not None:
            line1 += f"[{self.safe_score_value(score):.0f}] "

        elements: List[Dict[str, Any]] = [
            {"text_run": {"content": line1}},
            {"text_run": title_run}
        ]
        if safe_summary:
            elements.append({"text_run": {"content": f"\n   摘要：{safe_summary}"}})

        return self.create_rich_text_block(elements)

    def create_divider_block(self) -> Dict:
        """构建分割线Block"""
        return {
            "block_type": 22,
            "divider": {}
        }

    def create_bold_text_block(self, text: str) -> Dict:
        """构建加粗文本Block（非目录标题）"""
        return {
            "block_type": 2,
            "text": {
                "elements": [{
                    "text_run": {
                        "content": text,
                        "text_element_style": {"bold": True}
                    }
                }],
                "style": {}
            }
        }

    def create_ordered_list_block(self, text: str, url: str = "") -> Dict:
        """构建有序列表项Block（自动编号1,2,3，支持超链接）"""
        text_run: Dict[str, Any] = {"content": text}
        if url:
            text_run["text_element_style"] = {"link": {"url": url}}
        return {
            "block_type": 13,
            "ordered": {
                "elements": [{"text_run": text_run}],
                "style": {}
            }
        }

    def create_body_text_block(self, text: str, url: str = "") -> Dict:
        """构建普通文本Block（支持超链接）"""
        text_run: Dict[str, Any] = {"content": text}
        if url:
            text_run["text_element_style"] = {"link": {"url": url}}
        return {
            "block_type": 2,
            "text": {
                "elements": [{"text_run": text_run}],
                "style": {}
            }
        }

    def create_body_bold_text_block(self, text: str, url: str = "") -> Dict:
        """构建普通文本加粗Block（支持超链接）"""
        text_run: Dict[str, Any] = {"content": text}
        text_style: Dict[str, Any] = {"bold": True}
        if url:
            text_style["link"] = {"url": url}
        text_run["text_element_style"] = text_style
        return {
            "block_type": 2,
            "text": {
                "elements": [{"text_run": text_run}],
                "style": {}
            }
        }

    def write_daily_news_to_wiki(self, wiki_token: str, all_categories_news: Dict[str, Dict]) -> bool:
        """
        写入每日新闻到Wiki (插入到第一个高亮块之后)
        all_categories_news: {"AI": briefing_dict, "MUSIC": ...}
        briefing_dict 结构: {"headlines": [...], "clusters": [{"name": str, "items": [...]}]}
        """
        # 1. 获取文档ID
        document_id = self.get_document_id_from_wiki(wiki_token)
        if not document_id:
            return False

        current_date = datetime.now().strftime("%Y-%m-%d")
        blocks_to_write = []

        # 2. 构建内容
        # 分割线（区分上一次写入）
        blocks_to_write.append(self.create_divider_block())
        
        # 写入日期 H2
        blocks_to_write.append(self.create_heading_block(current_date, level=2))

        # 遍历类别
        cn_nums = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十",
                    "十一", "十二", "十三", "十四", "十五"]
        
        for category, briefing in all_categories_news.items():
            # 赛道标题 H3
            blocks_to_write.append(self.create_heading_block(str(category), level=3))

            if not briefing or not isinstance(briefing, dict):
                blocks_to_write.append(self.create_text_block("暂无数据"))
                continue
            
            # 自动检测数据格式：NewsDigest (有 events) vs NewsBriefing (有 headlines)
            events = briefing.get("events")
            if isinstance(events, list) and events:
                # --- 新格式 (NewsDigest): 阿拉伯数字 + 超链接标题 + 点号子要点 ---
                for i, event in enumerate(events, 1):
                    if not isinstance(event, dict):
                        continue
                    headline = str(event.get("headline") or "未命名事件")
                    event_url = self.normalize_http_url(event.get("url"))
                    blocks_to_write.append(
                        self.create_body_bold_text_block(f"{i}、{headline}", event_url)
                    )
                    points = event.get("points")
                    if isinstance(points, list):
                        for pt in points:
                            if isinstance(pt, dict):
                                pt_text = str(pt.get("text") or "")
                            else:
                                pt_text = str(pt)
                            if pt_text:
                                blocks_to_write.append(
                                    self.create_body_text_block(f"· {pt_text}")
                                )
            else:
                # --- 旧格式 (NewsBriefing): headlines + clusters ---
                headlines = briefing.get("headlines")
                blocks_to_write.append(self.create_bold_text_block("── 🔥 今日头条 ──"))
                if isinstance(headlines, list) and headlines:
                    for hl in headlines:
                        if isinstance(hl, dict):
                            safe_title = str(hl.get("title") or "无标题").strip()
                            safe_url = self.normalize_http_url(hl.get("url"))
                            blocks_to_write.append(
                                self.create_ordered_list_block(safe_title, safe_url)
                            )
                else:
                    blocks_to_write.append(self.create_text_block("暂无数据"))

                clusters = briefing.get("clusters")
                if not isinstance(clusters, list):
                    clusters = []

                blocks_to_write.append(self.create_bold_text_block("── 📂 深度专题 ──"))
                if not clusters:
                    blocks_to_write.append(self.create_text_block("暂无数据"))
                    continue

                valid_cluster_count = 0
                for cluster in clusters:
                    if not isinstance(cluster, dict):
                        continue
                    valid_cluster_count += 1
                    cluster_name = str(cluster.get("name") or "未命名专题")

                    blocks_to_write.append(self.create_bold_text_block(f"▸ {cluster_name}"))

                    cluster_items = cluster.get("items")
                    if not isinstance(cluster_items, list) or not cluster_items:
                        blocks_to_write.append(self.create_text_block("暂无条目"))
                        continue

                    for item in cluster_items:
                        if not isinstance(item, dict):
                            continue
                        safe_summary = str(item.get("summary") or "无摘要").strip()
                        safe_url = self.normalize_http_url(item.get("url"))
                        blocks_to_write.append(
                            self.create_ordered_list_block(safe_summary, safe_url)
                        )

                if valid_cluster_count == 0:
                    blocks_to_write.append(self.create_text_block("暂无数据"))

        # 3. 确定插入位置
        insert_index = self.find_first_callout_index(document_id)
        if insert_index == -1:
            print("⚠️ 未找到高亮块，将追加到文档末尾")
        else:
            print(f"📝 将插入到索引 {insert_index} (高亮块之后)")
            
        # 4. 写入（分批，单批<=50）
        return self.append_blocks_in_batches(
            document_id=document_id,
            children=blocks_to_write,
            index=insert_index,
            batch_size=self.MAX_CHILDREN_PER_REQUEST,
        )
