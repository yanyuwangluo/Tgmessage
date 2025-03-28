import os
import asyncio
import logging
from telethon import TelegramClient, functions
from telethon.tl.types import InputPeerChannel, InputPeerChat, InputPeerUser
from telethon.tl.functions.messages import GetDialogsRequest
from telethon.tl.functions.channels import GetChannelsRequest
from telethon.errors import SessionPasswordNeededError

from config import API_ID, API_HASH, PHONE, MEDIA_FOLDER
from models import Chat

# 配置日志记录器
logger = logging.getLogger(__name__)

# 全局客户端实例
client = None
# 全局事件循环
loop = None
# 保存登录状态
is_logged_in = False

async def create_client():
    """创建Telegram客户端实例"""
    global client
    
    # 创建会话目录
    os.makedirs('data/sessions', exist_ok=True)
    
    # 创建客户端，使用自定义session_name并添加连接超时
    session_path = 'data/sessions/user_session'
    
    # 如果客户端已存在且已连接，先断开连接
    if client:
        try:
            if client.is_connected():
                await client.disconnect()
        except:
            pass
    
    # 使用连接超时和重试逻辑创建客户端
    client = TelegramClient(
        session_path, 
        API_ID, 
        API_HASH,
        connection_retries=5,  # 增加重试次数
        retry_delay=1,         # 每次重试等待1秒
        auto_reconnect=True,   # 自动重连
        request_retries=3      # 请求重试次数
    )
    
    # 连接到Telegram，添加超时处理
    try:
        await client.connect()
    except Exception as e:
        logger.error(f"连接Telegram失败: {str(e)}")
        return False
    
    # 检查是否需要登录
    if not await client.is_user_authorized():
        try:
            # 发送验证码
            await client.send_code_request(PHONE)
        except Exception as e:
            logger.error(f"发送验证码失败: {str(e)}")
            return False
        
        return False
    
    return True

async def login(code, password=None):
    """用验证码登录"""
    global client
    
    try:
        await client.sign_in(PHONE, code)
    except SessionPasswordNeededError:
        if not password:
            return False, "需要两步验证密码"
        await client.sign_in(password=password)
    
    return True, "登录成功"

async def get_all_dialogs():
    """获取所有对话（频道、群组和用户）"""
    global client
    
    if not client or not await client.is_user_authorized():
        return None
    
    # 获取所有对话
    dialogs = await client(GetDialogsRequest(
        offset_date=None,
        offset_id=0,
        offset_peer=InputPeerUser(0, 0),
        limit=200,
        hash=0
    ))
    
    # 保存所有对话到数据库
    for dialog in dialogs.dialogs:
        entity = await client.get_entity(dialog.peer)
        
        if hasattr(entity, 'title'):  # 频道或群组
            chat_type = 'channel' if hasattr(entity, 'megagroup') else 'group'
            username = entity.username if hasattr(entity, 'username') else None
            access_hash = str(entity.access_hash) if hasattr(entity, 'access_hash') else None
            
            Chat.save_chat(
                chat_id=entity.id,
                title=entity.title,
                username=username,
                chat_type=chat_type,
                access_hash=access_hash
            )
    
    return Chat.get_all_chats()

async def ensure_client_connected():
    """确保客户端已连接并登录"""
    global client, is_logged_in
    
    # 如果客户端为空，创建一个新的
    if client is None:
        # 创建会话目录
        os.makedirs('data/sessions', exist_ok=True)
        
        # 使用连接超时和重试逻辑创建客户端
        client = TelegramClient(
            'data/sessions/user_session',
            API_ID, 
            API_HASH,
            connection_retries=5,
            retry_delay=1,
            auto_reconnect=True,
            request_retries=3
        )
    
    # 如果客户端未连接，尝试连接
    if not client.is_connected():
        try:
            await client.connect()
        except Exception as e:
            logger.error(f"连接Telegram失败: {str(e)}")
            # 短暂延迟后重试一次
            await asyncio.sleep(2)
            try:
                await client.connect()
            except Exception as e:
                logger.error(f"重试连接Telegram失败: {str(e)}")
                return False
    
    # 检查是否已授权，使用异常处理包装
    try:
        if not await client.is_user_authorized():
            return False
    except Exception as e:
        logger.error(f"检查授权状态失败: {str(e)}")
        return False
    
    # 标记为已登录
    is_logged_in = True
    return True

async def send_message(chat_id, message_type, content, format=None, media_path=None):
    """发送消息到指定对话"""
    global client
    
    # 检查并确保客户端已登录
    is_connected = await ensure_client_connected()
    if not is_connected:
        return False, "客户端未登录"
    
    try:
        chat = Chat.get_chat_by_id(chat_id)
        if not chat:
            return False, f"找不到ID为{chat_id}的对话"
        
        # 构建对话实体
        if chat['type'] == 'channel':
            entity = InputPeerChannel(int(chat_id), int(chat['access_hash']))
        elif chat['type'] == 'group':
            entity = InputPeerChat(int(chat_id))
        else:  # 用户
            # 尝试判断是否为用户名（包含字母）
            is_username = not str(chat_id).isdigit()
            
            if is_username:
                # 使用用户名
                entity = chat_id if chat_id.startswith('@') else f'@{chat_id}'
            else:
                # 使用用户ID
                entity = int(chat_id)
        
        # 根据消息类型发送不同格式的消息
        if message_type == 'text':
            if format == 'md':
                sent_message = await client.send_message(entity, content, parse_mode='md')
            elif format == 'html':
                sent_message = await client.send_message(entity, content, parse_mode='html')
            else:
                sent_message = await client.send_message(entity, content)
        
        elif message_type == 'media':
            if not media_path or not os.path.exists(media_path):
                return False, "媒体文件不存在"
            
            # 获取文件扩展名
            file_ext = os.path.splitext(media_path)[1].lower()
            
            # 记录媒体文件信息
            logger.info(f"发送媒体文件: {media_path}, 扩展名: {file_ext}, 使用图片格式")
            
            # 判断文件类型
            is_video = file_ext in ['.mp4', '.avi', '.mov', '.mkv']
            is_audio = file_ext in ['.mp3', '.wav', '.ogg', '.m4a']
            is_image = file_ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp']
            
            # 根据文件类型设置参数
            kwargs = {
                'entity': entity,
                'file': media_path,
                'caption': content,
                'force_document': False,  # 强制作为媒体预览发送
                'attributes': [],  # 清空属性以让Telegram自动检测
            }
            
            # 视频文件添加流媒体支持
            if is_video:
                kwargs['supports_streaming'] = True
            
            # 发送文件
            sent_message = await client.send_file(**kwargs)
        
        # 返回发送成功的消息ID，用于后续可能的撤回操作
        return True, {"message": "消息发送成功", "message_id": sent_message.id}
    
    except Exception as e:
        logger.error(f"发送消息失败: {str(e)}")
        return False, f"发送消息失败: {str(e)}"

async def delete_message(chat_id, message_id):
    """从指定对话中删除消息"""
    global client
    
    # 检查并确保客户端已登录
    is_connected = await ensure_client_connected()
    if not is_connected:
        return False, "客户端未登录"
    
    try:
        # 获取对话实体
        chat = Chat.get_chat_by_id(chat_id)
        if not chat:
            return False, f"找不到ID为{chat_id}的对话"
        
        # 构建对话实体
        if chat['type'] == 'channel':
            entity = InputPeerChannel(int(chat_id), int(chat['access_hash']))
        elif chat['type'] == 'group':
            entity = InputPeerChat(int(chat_id))
        else:  # 用户
            # 尝试判断是否为用户名（包含字母）
            is_username = not str(chat_id).isdigit()
            
            if is_username:
                # 使用用户名
                entity = chat_id if chat_id.startswith('@') else f'@{chat_id}'
            else:
                # 使用用户ID
                entity = int(chat_id)
        
        # 删除消息
        await client.delete_messages(entity, message_id)
        logger.info(f"已从对话 {chat_id} 中删除消息 {message_id}")
        
        return True, "消息已成功删除"
    
    except Exception as e:
        logger.error(f"删除消息失败: {str(e)}")
        return False, f"删除消息失败: {str(e)}"

async def disconnect():
    """断开Telegram客户端连接"""
    global client
    
    if client:
        await client.disconnect()
        client = None

# 初始化全局事件循环
def init_loop():
    global loop
    if loop is None or loop.is_closed():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop

# 异步运行客户端的函数包装器
def run_async(coro):
    global loop
    
    # 检查当前线程是否已有运行中的事件循环
    try:
        # 尝试获取当前事件循环
        current_loop = asyncio.get_event_loop()
        # 如果当前事件循环正在运行，使用create_task代替run_until_complete
        if current_loop.is_running():
            logger.info("检测到事件循环正在运行，使用替代方法")
            # 创建一个Future对象
            future = asyncio.run_coroutine_threadsafe(coro, current_loop)
            # 等待Future完成，最多等待30秒
            return future.result(30)
    except RuntimeError:
        # 如果没有事件循环或其他错误，使用我们自己的循环
        pass
    
    # 使用全局事件循环
    if loop is None or loop.is_closed():
        init_loop()
    
    # 运行协程并返回结果
    return loop.run_until_complete(coro) 