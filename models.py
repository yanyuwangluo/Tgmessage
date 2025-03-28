import sqlite3
import json
import os
import time
import threading
from datetime import datetime
from config import DATABASE
import logging

logger = logging.getLogger(__name__)

# 数据库连接锁
db_lock = threading.Lock()

def get_db_connection():
    """获取数据库连接"""
    # 确保数据库目录存在
    os.makedirs(os.path.dirname(DATABASE), exist_ok=True)
    
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def execute_db_query(query, params=(), fetchone=False, commit=True):
    """执行数据库查询，处理连接和关闭"""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(query, params)
        
        if commit:
            conn.commit()
        
        if fetchone:
            result = cursor.fetchone()
        else:
            result = cursor.fetchall()
        
        return result
    except Exception as e:
        logger.error(f"执行数据库查询失败: {str(e)}")
        raise
    finally:
        if conn:
            conn.close()

def init_db():
    """初始化数据库表"""
    # 确保数据库目录存在
    os.makedirs(os.path.dirname(DATABASE), exist_ok=True)
    
    try:
        # 创建频道和群组表
        execute_db_query('''
        CREATE TABLE IF NOT EXISTS chats (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            username TEXT,
            type TEXT NOT NULL,
            access_hash TEXT,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        
        # 创建定时消息表
        execute_db_query('''
        CREATE TABLE IF NOT EXISTS scheduled_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT NOT NULL,
            message_type TEXT NOT NULL,
            content TEXT NOT NULL,
            format TEXT,
            media_path TEXT,
            schedule_time TIMESTAMP NOT NULL,
            repeat_count INTEGER DEFAULT 1,
            completed_count INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'pending',
            deleted_after_seconds INTEGER DEFAULT 0,
            telegram_message_id INTEGER,
            delete_scheduled BOOLEAN DEFAULT 0,
            delete_time TIMESTAMP
        )
        ''')
        
        logger.info("数据库表初始化完成")
        return True
    except Exception as e:
        logger.error(f"初始化数据库失败: {str(e)}")
        return False

class Chat:
    """频道、群组或用户对话模型"""
    
    @staticmethod
    def save_chat(chat_id, title, username, chat_type, access_hash=None):
        """保存频道或群组信息"""
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
        INSERT OR REPLACE INTO chats (id, title, username, type, access_hash, last_updated)
        VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ''', (chat_id, title, username, chat_type, access_hash))
        
        conn.commit()
        conn.close()
    
    @staticmethod
    def get_all_chats():
        """获取所有已保存的对话"""
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT * FROM chats ORDER BY title')
        chats = cursor.fetchall()
        
        conn.close()
        return chats
    
    @staticmethod
    def get_chat_by_id(chat_id):
        """通过ID获取对话信息"""
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT * FROM chats WHERE id = ?', (chat_id,))
        chat = cursor.fetchone()
        
        conn.close()
        return chat

class ScheduledMessage:
    """定时消息模型"""
    
    @staticmethod
    def create(chat_id, message_type, content, schedule_time, format=None, 
               media_path=None, repeat_count=1, deleted_after_seconds=0):
        """创建新的定时消息"""
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # 标准化时间格式：将ISO格式 (2025-03-28T17:52:00) 转换为 (2025-03-28 17:52:00)
        if 'T' in schedule_time:
            schedule_time = schedule_time.replace('T', ' ')
        
        cursor.execute('''
        INSERT INTO scheduled_messages 
        (chat_id, message_type, content, format, media_path, schedule_time, repeat_count, status, deleted_after_seconds)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)
        ''', (chat_id, message_type, content, format, media_path, schedule_time, repeat_count, deleted_after_seconds))
        
        msg_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return msg_id
    
    @staticmethod
    def get_all():
        """获取所有定时消息"""
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
        SELECT sm.*, c.title as chat_title 
        FROM scheduled_messages sm
        JOIN chats c ON sm.chat_id = c.id
        ORDER BY sm.schedule_time
        ''')
        
        messages = cursor.fetchall()
        conn.close()
        
        return messages
    
    @staticmethod
    def get_pending_messages():
        """获取等待发送的消息"""
        try:
            now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            logger.info(f"检查定时消息: 当前时间 {now}")
            
            # 获取所有待处理消息，查看它们的计划时间便于调试
            pending_messages = execute_db_query('SELECT id, schedule_time FROM scheduled_messages WHERE status = "pending"')
            if pending_messages:
                for msg in pending_messages:
                    logger.info(f"待处理消息 ID: {msg['id']}, 计划时间: {msg['schedule_time']}, 当前时间: {now}")
            
            # 只获取状态为pending且时间已到的消息
            # 这里不再检查completed_count，因为现在我们一次性发送所有重复次数
            messages = execute_db_query('''
            SELECT * FROM scheduled_messages 
            WHERE status = 'pending' AND schedule_time <= ?
            ''', (now,))
            
            if messages:
                logger.info(f"找到 {len(messages)} 条待发送消息")
            
            return messages
        except Exception as e:
            logger.error(f"获取待处理消息失败: {str(e)}")
            return []
    
    @staticmethod
    def update_status(msg_id, status, increment_count=False, telegram_message_id=None):
        """更新消息状态"""
        try:
            # 基础更新参数
            params = [status]
            update_fields = "status = ?"

            # 处理completed_count字段
            if isinstance(increment_count, int) and increment_count > 1:
                # 直接设置为指定值
                params.append(increment_count)
                update_fields += ", completed_count = ?"
            elif increment_count:
                # 递增completed_count
                update_fields += ", completed_count = completed_count + 1"
            
            # 如果提供了消息ID，则更新telegram_message_id字段
            if telegram_message_id is not None:
                params.append(telegram_message_id)
                update_fields += ", telegram_message_id = ?"
            
            # 添加WHERE条件参数
            params.append(msg_id)
            
            # 执行更新
            query = f"UPDATE scheduled_messages SET {update_fields} WHERE id = ?"
            execute_db_query(query, tuple(params))
            
            return True
        except Exception as e:
            logger.error(f"更新消息状态失败 (ID: {msg_id}): {str(e)}")
            return False
    
    @staticmethod
    def delete(msg_id):
        """删除定时消息"""
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('DELETE FROM scheduled_messages WHERE id = ?', (msg_id,))
        
        conn.commit()
        conn.close()

    @staticmethod
    def schedule_delete(msg_id, delete_time):
        """安排消息删除"""
        try:
            execute_db_query('''
            UPDATE scheduled_messages 
            SET delete_scheduled = 1, delete_time = ?
            WHERE id = ?
            ''', (delete_time, msg_id))
            
            return True
        except Exception as e:
            logger.error(f"安排消息删除失败 (ID: {msg_id}): {str(e)}")
            return False

    @staticmethod
    def get_messages_to_delete():
        """获取到达删除时间的消息"""
        try:
            now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            messages = execute_db_query('''
            SELECT * FROM scheduled_messages 
            WHERE delete_scheduled = 1 
            AND delete_time <= ? 
            AND telegram_message_id IS NOT NULL
            ''', (now,))
            
            return messages
        except Exception as e:
            logger.error(f"获取待删除消息失败: {str(e)}")
            return []

# 初始化数据库
init_db() 