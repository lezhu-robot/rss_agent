import requests
import json
import os
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

app_id = os.getenv("LARK_APP_ID")
app_secret = os.getenv("LARK_APP_SECRET")


def _prepare_message_payload(content):
    msg_type = "text"
    final_content = content

    try:
        if isinstance(content, str) and content.strip().startswith("{") and '"header"' in content:
            msg_type = "interactive"
            final_content = content
        else:
            text_content = str(content) if content is not None else ""
            final_content = json.dumps({"text": text_content}, ensure_ascii=False)
    except Exception as e:
        print(f"⚠️ Message Payload Build Error: {e}")
        final_content = json.dumps({"text": str(content)}, ensure_ascii=False)

    return msg_type, final_content

def get_tenant_access_token():
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    headers = {"Content-Type": "application/json; charset=utf-8"}
    data = {
        "app_id": app_id.strip() if app_id else "",
        "app_secret": app_secret.strip() if app_secret else ""
    }
    resp = requests.post(url, headers=headers, json=data)
    if resp.status_code == 200:
        return resp.json().get("tenant_access_token")
    else:
        print(f"❌ Failed to get token: {resp.text}")
        return None

def reply_message(message_id, content):
    """
    调用飞书 API 回复用户 (Raw HTTP)
    """
    try:
        token = get_tenant_access_token()
        if not token:
            print("❌ Cannot send message without token")
            return False
            
        url = f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/reply"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8"
        }
        msg_type, final_content = _prepare_message_payload(content)

        payload = {
            "content": final_content,
            "msg_type": msg_type
        }
        
        print(f"📤 Sending Reply: type={msg_type}, content_len={len(final_content)}")
        
        resp = requests.post(url, headers=headers, json=payload)
        
        if resp.status_code != 200:
            print(f"❌ Lark API Error: {resp.text}")
            return False
        else:
            # 飞书 API 即使 200 也可能在 body 里报错
            res_json = resp.json()
            if res_json.get("code") != 0:
                print(f"❌ Lark Logic Error: {res_json}")
                return False
            else:
                short_content = str(content)[:20].replace('\n', ' ')
                print(f"✅ Reply Sent: {short_content}...")
                return True
            
    except Exception as e:
        print(f"❌ Exception in reply_message: {str(e)}")
        return False

def send_message(receive_id, content, receive_id_type="open_id"):
    """主动发送消息 (支持用户 open_id 和群 chat_id)。"""
    try:
        token = get_tenant_access_token()
        if not token:
            return False
        
        url = "https://open.feishu.cn/open-apis/im/v1/messages"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8"
        }
        
        params = {"receive_id_type": receive_id_type}
        msg_type, final_content = _prepare_message_payload(content)

        payload = {
            "receive_id": receive_id,
            "content": final_content,
            "msg_type": msg_type
        }
        
        resp = requests.post(url, headers=headers, params=params, json=payload)
        if resp.status_code != 200:
            print(f"❌ Push Failed: {resp.text}")
            return False

        body = resp.json()
        if body.get("code") != 0:
            print(f"❌ Push Failed: {body}")
            return False
        else:
            print(f"📤 Pushed to {receive_id_type}:{receive_id}: {msg_type}")
            return True
            
    except Exception as e:
        print(f"❌ Exception in send_message: {str(e)}")
        return False


def update_message(message_id, content):
    """原位更新消息内容（主要用于更新卡片状态）。"""
    try:
        token = get_tenant_access_token()
        if not token:
            return False

        url = f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8"
        }

        msg_type, final_content = _prepare_message_payload(content)

        payload = {
            "content": final_content,
            "msg_type": msg_type,
        }

        resp = requests.patch(url, headers=headers, json=payload)
        if resp.status_code != 200:
            print(f"❌ Update Message HTTP Error: {resp.text}")
            return False

        body = resp.json()
        if body.get("code") != 0:
            print(f"❌ Update Message Logic Error: {body}")
            return False

        print(f"📝 Updated message: {message_id}")
        return True
    except Exception as e:
        print(f"❌ Exception in update_message: {str(e)}")
        return False
