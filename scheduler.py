import logging
import asyncio
import threading
import time
import os
import glob
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger
from apscheduler.executors.pool import ThreadPoolExecutor

from models import ScheduledMessage
from telegram_client import run_async, send_message, init_loop, ensure_client_connected, delete_message
from config import MEDIA_FOLDER

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("data/scheduler.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# 全局调度器
scheduler = None

def init_scheduler():
    """初始化调度器"""
    global scheduler
    
    # 确保事件循环已初始化
    init_loop()
    
    if scheduler is None:
        # 使用线程池执行器避免阻塞主线程
        executors = {
            'default': ThreadPoolExecutor(max_workers=3)
        }
        
        job_defaults = {
            'coalesce': True,  # 合并延迟的任务
            'max_instances': 1, # 同一个任务最多只有一个实例在运行
            'misfire_grace_time': 60  # 错过的任务最多延迟60秒执行
        }
        
        # 创建调度器并配置作业存储和执行器
        scheduler = BackgroundScheduler(
            executors=executors,
            job_defaults=job_defaults
        )
        
        # 启动调度器
        scheduler.start()
        logger.info("定时任务调度器已启动")
        
        # 添加消息检查任务，每10秒运行一次
        scheduler.add_job(check_pending_messages, 'interval', seconds=10, 
                          max_instances=1, coalesce=True)
        
        # 添加清理任务，每7天运行一次
        scheduler.add_job(clean_old_files, 'interval', days=7,
                         next_run_time=datetime.now() + timedelta(minutes=5),  # 首次运行在5分钟后
                         max_instances=1, coalesce=True)
        logger.info("已添加文件清理任务，每7天运行一次，首次运行在5分钟后")
        
        # 添加消息撤回任务，每30秒运行一次
        scheduler.add_job(check_messages_to_delete, 'interval', seconds=30,
                         max_instances=1, coalesce=True)
        logger.info("已添加消息撤回任务，每30秒运行一次")

def check_messages_to_delete():
    """检查并撤回到达撤回时间的消息"""
    # 创建一个独立的线程来执行撤回操作
    t = threading.Thread(target=_delete_messages_thread)
    t.daemon = True
    t.start()

def _delete_messages_thread():
    """在独立线程中执行消息撤回操作"""
    from models import ScheduledMessage
    
    try:
        # 获取需要撤回的消息
        messages = ScheduledMessage.get_messages_to_delete()
        
        if not messages or len(messages) == 0:
            return
        
        logger.info(f"找到 {len(messages)} 条需要撤回的消息")
        
        # 确保客户端已连接 - 使用 run_async 包装
        try:
            client_ready = run_async(ensure_client_connected())
            if not client_ready:
                logger.warning("Telegram客户端未登录，无法撤回消息")
                return
        except RuntimeError as re:
            if "This event loop is already running" in str(re):
                logger.warning("撤回消息时检测到事件循环已运行，将在下一个周期重试")
                return
            else:
                raise re
        
        # 处理每条需要撤回的消息
        for msg in messages:
            try:
                # 撤回消息 - 使用 run_async 包装
                try:
                    success, result = run_async(delete_message(
                        msg['chat_id'],
                        msg['telegram_message_id']
                    ))
                    
                    if success:
                        logger.info(f"消息 {msg['id']} (Telegram ID: {msg['telegram_message_id']}) 已成功撤回")
                        # 更新消息状态
                        ScheduledMessage.update_status(msg['id'], 'deleted')
                    else:
                        logger.error(f"撤回消息 {msg['id']} 失败: {result}")
                except RuntimeError as re:
                    if "This event loop is already running" in str(re):
                        logger.warning(f"撤回消息 {msg['id']} 时检测到事件循环已运行，将在下一个周期重试")
                        break
                    else:
                        raise re
            except Exception as e:
                logger.error(f"处理待撤回消息 {msg['id']} 时出错: {str(e)}")
    
    except Exception as e:
        logger.error(f"检查并撤回消息时出错: {str(e)}")

def check_pending_messages():
    """检查等待发送的消息"""
    # 不在调度器线程中运行Telegram请求
    # 而是将其包装在一个专用的线程中
    t = threading.Thread(target=_check_messages_thread)
    t.daemon = True
    t.start()

def _check_messages_thread():
    """在独立线程中检查并发送消息的实现"""
    from datetime import datetime, timedelta
    
    # 首先确保客户端已连接
    try:
        client_ready = run_async(ensure_client_connected())
        if not client_ready:
            logger.warning("Telegram客户端未登录，无法发送消息")
            return
    except Exception as e:
        logger.error(f"检查客户端连接状态时出错: {str(e)}")
        return
    
    messages = ScheduledMessage.get_pending_messages()
    
    for msg in messages:
        # 发送消息（一次性发送指定的重复次数）
        try:
            repeat_count = msg['repeat_count']
            logger.info(f"准备发送消息 {msg['id']}，重复次数: {repeat_count}")
            
            success_count = 0
            last_message_id = None
            
            for i in range(repeat_count):
                try:
                    # 添加轻微延迟避免Telegram限流
                    if i > 0:
                        time.sleep(1.5)
                    
                    logger.info(f"发送消息 {msg['id']} 第 {i+1}/{repeat_count} 次")
                    try:
                        success, result = run_async(send_message(
                            msg['chat_id'],
                            msg['message_type'],
                            msg['content'],
                            msg['format'],
                            msg['media_path']
                        ))
                        
                        if success:
                            success_count += 1
                            # 保存最后一条成功发送的消息ID，用于可能的撤回操作
                            if isinstance(result, dict) and 'message_id' in result:
                                last_message_id = result['message_id']
                        else:
                            logger.error(f"消息 {msg['id']} 第 {i+1} 次发送失败: {result}")
                    except RuntimeError as re:
                        if "This event loop is already running" in str(re):
                            # 这里我们需要一个替代方法来处理已运行事件循环的情况
                            logger.warning(f"发送消息时检测到事件循环已运行，使用替代方法")
                            # 这种情况下，我们可能需要其他方式来确保消息发送
                            # 例如，将消息标记为失败并在下一个循环重试
                            break
                        else:
                            raise re
                except Exception as e:
                    logger.error(f"发送消息 {msg['id']} 第 {i+1} 次时出错: {str(e)}")
            
            # 更新消息状态
            if success_count > 0:
                logger.info(f"消息 {msg['id']} 成功发送 {success_count}/{repeat_count} 次")
                # 所有重复次数已完成，直接标记为完成状态
                ScheduledMessage.update_status(msg['id'], 'completed', increment_count=repeat_count, telegram_message_id=last_message_id)
                
                # 如果消息设置了自动撤回，则安排撤回任务
                if msg['deleted_after_seconds'] > 0 and last_message_id:
                    # 计算撤回时间
                    delete_time = datetime.now() + timedelta(seconds=msg['deleted_after_seconds'])
                    delete_time_str = delete_time.strftime('%Y-%m-%d %H:%M:%S')
                    
                    # 安排消息撤回
                    ScheduledMessage.schedule_delete(msg['id'], delete_time_str)
                    logger.info(f"已安排消息 {msg['id']} 在 {delete_time_str} 撤回")
            else:
                logger.error(f"消息 {msg['id']} 所有发送尝试均失败")
                ScheduledMessage.update_status(msg['id'], 'failed')
            
        except Exception as e:
            logger.exception(f"处理消息 {msg['id']} 时出错: {str(e)}")
            ScheduledMessage.update_status(msg['id'], 'failed')

def schedule_message(chat_id, message_type, content, schedule_time, format=None, 
                     media_path=None, repeat_count=1, deleted_after_seconds=0):
    """安排新的定时消息"""
    try:
        # 创建定时消息记录
        msg_id = ScheduledMessage.create(
            chat_id=chat_id,
            message_type=message_type,
            content=content,
            schedule_time=schedule_time,
            format=format,
            media_path=media_path,
            repeat_count=repeat_count,
            deleted_after_seconds=deleted_after_seconds
        )
        
        logger.info(f"已创建定时消息 {msg_id}, 计划时间: {schedule_time}")
        return True, msg_id
    
    except Exception as e:
        logger.exception(f"创建定时消息失败: {str(e)}")
        return False, str(e)

def stop_scheduler():
    """停止调度器"""
    global scheduler
    
    if scheduler:
        scheduler.shutdown()
        scheduler = None
        logger.info("定时任务调度器已停止")

def clean_old_files():
    """清理旧的媒体文件和日志文件（7天前）"""
    logger.info("开始清理旧文件...")
    
    # 创建一个独立的线程来执行清理操作
    cleanup_thread = threading.Thread(target=_clean_files_thread)
    cleanup_thread.daemon = True
    cleanup_thread.start()

def _clean_files_thread():
    """在独立线程中执行文件清理"""
    try:
        # 计算7天前的时间戳
        cutoff_time = datetime.now() - timedelta(days=7)
        cutoff_timestamp = cutoff_time.timestamp()
        
        # 清理媒体文件
        cleaned_media = _clean_directory(MEDIA_FOLDER, cutoff_timestamp)
        
        # 清理日志文件（保留.log但删除旧内容）
        log_files = glob.glob(os.path.join('data', '*.log'))
        truncated_logs = 0
        
        for log_file in log_files:
            try:
                # 获取文件修改时间
                file_stats = os.stat(log_file)
                
                # 如果文件修改时间早于截止时间，则截断文件（保留文件但清空内容）
                if file_stats.st_mtime < cutoff_timestamp:
                    # 读取最后100行保留
                    with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                        lines = f.readlines()
                        last_lines = lines[-100:] if len(lines) > 100 else lines
                    
                    # 截断文件并只写入最后100行
                    with open(log_file, 'w', encoding='utf-8') as f:
                        f.write(f"--- 日志在 {datetime.now()} 被截断，只保留最后100行 ---\n")
                        f.writelines(last_lines)
                    
                    truncated_logs += 1
            except Exception as e:
                logger.error(f"截断日志文件 {log_file} 失败: {str(e)}")
        
        logger.info(f"文件清理完成: 删除了 {cleaned_media} 个媒体文件, 截断了 {truncated_logs} 个日志文件")
    
    except Exception as e:
        logger.error(f"清理文件过程中出错: {str(e)}")

def _clean_directory(directory, cutoff_timestamp):
    """清理指定目录中的旧文件"""
    cleaned_count = 0
    
    try:
        # 确保目录存在
        if not os.path.exists(directory):
            return 0
        
        # 获取目录中的所有文件
        for root, dirs, files in os.walk(directory):
            for file in files:
                file_path = os.path.join(root, file)
                
                try:
                    # 获取文件的修改时间
                    file_stats = os.stat(file_path)
                    
                    # 如果文件修改时间早于截止时间，则删除
                    if file_stats.st_mtime < cutoff_timestamp:
                        os.remove(file_path)
                        cleaned_count += 1
                except Exception as e:
                    logger.error(f"删除文件 {file_path} 失败: {str(e)}")
    
    except Exception as e:
        logger.error(f"清理目录 {directory} 失败: {str(e)}")
    
    return cleaned_count 