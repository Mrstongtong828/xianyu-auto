"""
飞书事件回调处理器 - 聊一聊（议价）功能

接收飞书机器人消息，解析闲鱼链接，使用指定闲鱼账号向卖家发送议价消息
"""

import asyncio
import base64
import hashlib
import hmac
import json
import random
import re
import time
from typing import Dict, Optional, Tuple

import aiohttp
from loguru import logger


# ==================== 防封机制 - 全局频率限制 ====================

class BargainRateLimiter:
    """议价消息频率限制器
    
    防止短时间内发送过多消息导致封号。
    限制策略：
    - 每个账号每分钟最多发送 3 条消息
    - 每个账号每小时最多发送 10 条消息
    - 连续发送间隔至少 10 秒
    """
    
    def __init__(self):
        # 记录每个账号的发送历史: {account_id: [timestamp1, timestamp2, ...]}
        self._send_history: Dict[str, list] = {}
        self._last_send_time: Dict[str, float] = {}
    
    def can_send(self, account_id: str) -> Tuple[bool, str]:
        """检查是否可以发送消息
        
        Returns:
            (是否可以发送, 原因说明)
        """
        now = time.time()
        
        # 检查连续发送间隔（至少 10 秒）
        last_time = self._last_send_time.get(account_id, 0)
        if now - last_time < 10:
            wait_time = int(10 - (now - last_time))
            return False, f"发送太频繁，请等待 {wait_time} 秒后再试"
        
        # 获取该账号的发送历史
        history = self._send_history.get(account_id, [])
        
        # 清理超过1小时的历史记录
        history = [t for t in history if now - t < 3600]
        self._send_history[account_id] = history
        
        # 检查每小时限制（10条）
        if len(history) >= 10:
            oldest = min(history)
            wait_time = int(3600 - (now - oldest))
            minutes = wait_time // 60
            return False, f"每小时最多发送10条议价消息，请等待 {minutes} 分钟后再试"
        
        # 检查每分钟限制（3条）
        recent_minute = [t for t in history if now - t < 60]
        if len(recent_minute) >= 3:
            oldest = min(recent_minute)
            wait_time = int(60 - (now - oldest))
            return False, f"每分钟最多发送3条议价消息，请等待 {wait_time} 秒后再试"
        
        return True, ""
    
    def record_send(self, account_id: str):
        """记录一次发送"""
        now = time.time()
        if account_id not in self._send_history:
            self._send_history[account_id] = []
        self._send_history[account_id].append(now)
        self._last_send_time[account_id] = now


# 全局频率限制器实例
_bargain_rate_limiter = BargainRateLimiter()


# ==================== 防封机制 - 模拟真人行为 ====================


def extract_xianyu_item_id(text: str) -> Optional[str]:
    """从文本中提取闲鱼商品ID"""
    if not text:
        return None

    patterns = [
        r'(?:goofish\.com|闲鱼|taobao\.com)/item\?.*?\bid=(\d+)',
        r'(?:goofish\.com|闲鱼|taobao\.com)/item/(\d+)',
        r'(?:goofish\.com|闲鱼\.com)/.*?/detail/(\d+)',
        r'itemId[=:](\d+)',
        r'(?:item/|id=)(\d{10,})',
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1)

    long_ids = re.findall(r'(\d{12,})', text)
    if long_ids:
        return long_ids[0]

    return None


def extract_bargain_text_from_message(text: str, item_url: str) -> str:
    """从飞书消息中提取议价文本（链接后面的文本）"""
    if not text:
        return ""

    cleaned = text.strip()

    url_pattern = r'https?://[^\s]+'
    urls = re.findall(url_pattern, cleaned)
    for url in urls:
        cleaned = cleaned.replace(url, '', 1)

    bargain_text = cleaned.strip()
    return bargain_text


def decrypt_feishu_event(encrypted_body: str, encrypt_key: str) -> Optional[str]:
    """解密飞书事件消息体

    飞书使用 AES-256-CBC 加密，密钥为 encrypt_key 的 SHA256 hash
    """
    if not encrypt_key or not encrypted_body:
        return None

    try:
        from Crypto.Cipher import AES

        key_hash = hashlib.sha256(encrypt_key.encode('utf-8')).digest()
        encrypted_bytes = base64.b64decode(encrypted_body)

        iv = encrypted_bytes[:16]
        ciphertext = encrypted_bytes[16:]

        cipher = AES.new(key_hash, AES.MODE_CBC, iv)
        padded = cipher.decrypt(ciphertext)

        pad_len = padded[-1]
        if isinstance(pad_len, int) and 0 <= pad_len <= 16:
            padded = padded[:-pad_len]

        return padded.decode('utf-8')
    except ImportError:
        logger.error("需要安装 pycryptodome 库来解密飞书事件: pip install pycryptodome")
        return None
    except Exception as e:
        logger.error(f"飞书事件解密失败: {e}")
        return None


async def get_tenant_access_token(app_id: str, app_secret: str) -> Optional[str]:
    """获取飞书 tenant_access_token"""
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    payload = {
        "app_id": app_id,
        "app_secret": app_secret
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=10) as resp:
                data = await resp.json()
                if data.get("code") == 0:
                    return data.get("tenant_access_token")
                logger.error(f"获取 tenant_access_token 失败: {data}")
                return None
    except Exception as e:
        logger.error(f"获取 tenant_access_token 异常: {e}")
        return None


async def reply_to_feishu_message(
    app_id: str, app_secret: str, message_id: str,
    content: str, msg_type: str = "text"
) -> bool:
    """回复飞书消息"""
    try:
        token = await get_tenant_access_token(app_id, app_secret)
        if not token:
            return False

        url = f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/reply"

        body_content = json.dumps({"text": content})

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8"
        }

        payload = {
            "content": body_content,
            "msg_type": msg_type
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers, timeout=10) as resp:
                data = await resp.json()
                if data.get("code") == 0:
                    logger.info(f"飞书回复消息成功: message_id={message_id}")
                    return True
                logger.error(f"飞书回复消息失败: {data}")
                return False
    except Exception as e:
        logger.error(f"飞书回复消息异常: {e}")
        return False


async def get_item_seller_info(instance, item_id: str) -> Optional[dict]:
    """通过闲鱼API获取商品卖家信息"""
    try:
        item_info = await instance.get_item_info(item_id)
        if not item_info or isinstance(item_info, dict) and item_info.get('error'):
            logger.error(f"获取商品信息失败: {item_id}")
            return None

        data = item_info.get('data', {})
        if isinstance(data, str):
            data = json.loads(data)

        result = data.get('result') or data
        if isinstance(result, str):
            result = json.loads(result)

        item_detail = result.get('item') or result.get('itemDO') or result

        seller_id = None
        seller_name = None
        title = ""
        price = ""

        user_info = item_detail.get('user') or item_detail.get('seller') or {}
        if isinstance(user_info, dict):
            seller_id = user_info.get('userId') or user_info.get('id')
            seller_name = user_info.get('nick') or user_info.get('userNickName')

        title = item_detail.get('title', '')
        price = item_detail.get('price', '')

        if not seller_id:
            seller_id = result.get('userId') or result.get('sellerId')
            if not seller_id:
                logger.error(f"无法从商品信息中提取卖家ID: {json.dumps(result, ensure_ascii=False)[:500]}")
                return None

        return {
            'seller_id': str(seller_id),
            'seller_name': seller_name or '卖家',
            'title': title or '未知商品',
            'price': str(price) if price else '未知价格'
        }
    except Exception as e:
        logger.error(f"获取商品卖家信息异常: {e}")
        return None


import random


async def simulate_typing_delay(text: str, min_delay: float = 0.05, max_delay: float = 0.15):
    """模拟打字延迟
    
    根据文本长度计算合理的打字时间，模拟真人打字速度。
    普通人打字速度：每分钟 40-80 个字，即每个字 0.75-1.5 秒
    这里使用更短的延迟，因为是"思考后输入"，不是逐字输入
    
    Args:
        text: 要发送的文本
        min_delay: 每个字符的最小延迟（秒）
        max_delay: 每个字符的最大延迟（秒）
    """
    # 基础思考时间：2-5秒（打开聊天框后的反应时间）
    base_think_time = random.uniform(2.0, 5.0)
    logger.info(f"模拟思考中... 等待 {base_think_time:.2f} 秒")
    await asyncio.sleep(base_think_time)
    
    # 模拟打字时间（根据文本长度）
    # 短文本（<10字）：快速输入
    # 中等文本（10-30字）：正常速度
    # 长文本（>30字）：较慢速度
    text_length = len(text)
    if text_length <= 10:
        typing_time = random.uniform(0.5, 1.5)
    elif text_length <= 30:
        typing_time = random.uniform(1.0, 3.0)
    else:
        typing_time = random.uniform(2.0, 5.0)
    
    logger.info(f"模拟打字中... 文本长度 {text_length} 字，预计打字时间 {typing_time:.2f} 秒")
    await asyncio.sleep(typing_time)


async def send_bargain_message(instance, seller_id: str, item_id: str, text: str) -> bool:
    """向卖家发送议价消息（带防封机制）
    
    流程：
    1. 进入聊天框后等待 2-5 秒（模拟阅读商品信息）
    2. 模拟打字延迟（根据文本长度）
    3. 发送消息
    """
    try:
        # 模拟真人打字延迟
        await simulate_typing_delay(text)
        
        await instance.send_msg_once(seller_id, item_id, text)
        logger.info(f"议价消息发送成功: seller_id={seller_id}, item_id={item_id}")
        return True
    except Exception as e:
        logger.error(f"议价消息发送失败: {e}")
        return False


async def run_bargain_on_instance(
    instance,
    feishu_config: dict,
    message_content: str,
    message_id: str,
    sender_open_id: str,
) -> Tuple[bool, str]:
    """处理闲鱼议价流程（必须在 CookieManager 事件循环中执行）

    Args:
        instance: XianyuLive 实例
        feishu_config: 飞书配置 dict
        message_content: 飞书消息文本内容
        message_id: 飞书消息ID
        sender_open_id: 发送者 open_id

    Returns:
        (success, result_message)
    """
    if not message_content:
        return False, "消息内容为空，请发送闲鱼链接"

    item_id = extract_xianyu_item_id(message_content)
    if not item_id:
        return False, "未识别到闲鱼商品链接，请发送正确的闲鱼链接（如 https://m.goofish.com/item?id=123456）"

    bargain_text = extract_bargain_text_from_message(message_content, "")
    if not bargain_text:
        bargain_text = feishu_config.get('default_bargain_text', '')
    if not bargain_text:
        bargain_text = "老板你好！请问这个还在吗？"

    logger.info(f"议价流程: item_id={item_id}, bargain_text={bargain_text}")

    seller_info = await get_item_seller_info(instance, item_id)
    if not seller_info:
        return False, f"无法获取商品信息（item_id={item_id}），请检查链接是否正确"

    seller_id = seller_info['seller_id']

    if seller_id == instance.myid:
        return False, "不能给自己的商品议价，请更换链接"

    seller_name = seller_info['seller_name']
    title = seller_info['title']
    price = seller_info['price']

    logger.info(
        f"商品信息: seller_id={seller_id}, seller_name={seller_name}, "
        f"title={title}, price={price}"
    )

    # 检查频率限制
    account_id = getattr(instance, 'cookie_id', 'unknown')
    can_send, limit_reason = _bargain_rate_limiter.can_send(account_id)
    if not can_send:
        logger.warning(f"议价频率限制: {limit_reason}")
        return False, f"操作太频繁，{limit_reason}"

    success = await send_bargain_message(instance, seller_id, item_id, bargain_text)
    
    if success:
        # 记录发送成功
        _bargain_rate_limiter.record_send(account_id)
        result_msg = (
            f"议价消息已发送！\n"
            f"商品: {title}\n"
            f"价格: ¥{price}\n"
            f"卖家: {seller_name}\n"
            f"内容: {bargain_text}"
        )
        return True, result_msg
    else:
        return False, "消息发送失败，请检查闲鱼账号状态后重试"


async def handle_feishu_callback(
    body: bytes,
    headers: dict,
    config: dict,
) -> dict:
    """处理飞书事件回调，返回需要执行的操作

    返回格式:
        {"action": "challenge", "challenge": "xxx"}  — 需要返回 URL 验证
        {"action": "bargain", "message_id": "...", "text": "...", ...}  — 需要执行议价
        {"action": "ignore"}  — 无需处理
        {"action": "error", "message": "..."}  — 错误

    """
    try:
        body_str = body.decode('utf-8')
        event_data = json.loads(body_str)
    except Exception as e:
        logger.error(f"飞书回调解析失败: {e}")
        return {"action": "error", "message": "invalid body"}

    encrypt_key = config.get('encrypt_key', '')

    encrypted = event_data.get('encrypt')
    if encrypted and encrypt_key:
        decrypted = decrypt_feishu_event(encrypted, encrypt_key)
        if decrypted:
            event_data = json.loads(decrypted)
        else:
            return {"action": "error", "message": "decrypt failed"}

    challenge = event_data.get('challenge')
    if challenge:
        logger.info("飞书 URL 验证挑战")
        return {"action": "challenge", "challenge": challenge}

    event_type = event_data.get('type', '')
    if event_type != 'event_callback':
        return {"action": "ignore"}

    event = event_data.get('event', {})
    event_subtype = event.get('type', '')

    if event_subtype != 'im.message.receive_v1':
        return {"action": "ignore"}

    message = event.get('message', {})
    message_id = message.get('message_id', '')
    content_str = message.get('content', '{}')
    sender_id = event.get('sender', {}).get('sender_id', {}).get('open_id', '')

    try:
        content_obj = json.loads(content_str)
        text = content_obj.get('text', '')
    except json.JSONDecodeError:
        text = content_str

    if not text:
        return {
            "action": "reply",
            "message_id": message_id,
            "text": "请发送闲鱼商品链接，系统将自动帮你议价"
        }

    item_id = extract_xianyu_item_id(text)
    if not item_id:
        return {
            "action": "reply",
            "message_id": message_id,
            "text": "未识别到闲鱼商品链接，请发送正确的链接（如 https://m.goofish.com/item?id=123456）"
        }

    logger.info(f"收到飞书议价请求: text={text[:100]}, message_id={message_id}, item_id={item_id}")

    return {
        "action": "bargain",
        "message_id": message_id,
        "text": text,
        "sender_id": sender_id,
        "item_id": item_id,
    }
