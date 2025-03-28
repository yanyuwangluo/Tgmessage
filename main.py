#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Telegram定时消息发送工具
------------------------
本工具可以使用Telegram用户API（非机器人API）定时发送消息到频道、群组或用户。
支持普通文本、Markdown格式、HTML格式和媒体文件，可以设置定时任务和重复次数。

作者: 
日期: 2023年
"""

import os
import logging
import sys
import atexit
import signal
import tempfile
import time

from app import app
from scheduler import stop_scheduler
from telegram_client import disconnect, run_async, init_loop

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("data/main.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# 程序退出时的清理操作
def cleanup():
    """程序退出时的清理操作"""
    logger.info("程序正在退出，执行清理操作...")
    
    # 停止定时任务调度器
    try:
        stop_scheduler()
    except Exception as e:
        logger.error(f"停止调度器失败: {str(e)}")
    
    # 断开Telegram客户端连接
    try:
        run_async(disconnect())
    except Exception as e:
        logger.error(f"断开Telegram连接失败: {str(e)}")
    
    # 删除进程锁文件
    try:
        if os.path.exists(LOCK_FILE):
            os.remove(LOCK_FILE)
    except Exception as e:
        logger.error(f"删除锁文件失败: {str(e)}")
    
    logger.info("清理操作完成")

# 创建进程锁，确保程序只运行一个实例
LOCK_FILE = os.path.join(tempfile.gettempdir(), 'tgmessage.lock')

def check_running():
    """检查程序是否已经在运行"""
    if os.path.exists(LOCK_FILE):
        # 检查锁文件内的进程ID是否还在运行
        try:
            with open(LOCK_FILE, 'r') as f:
                pid = int(f.read().strip())
            
            # 在Windows上使用tasklist命令检查进程是否存在
            import subprocess
            output = subprocess.check_output(f'tasklist /FI "PID eq {pid}"', shell=True).decode()
            
            if f"PID {pid}" in output or str(pid) in output:
                logger.error(f"程序已经在运行 (PID: {pid})，如需强制启动，请删除锁文件: {LOCK_FILE}")
                return True
        except:
            # 如果无法检查进程或读取失败，假设锁文件无效
            pass
    
    # 创建新的锁文件
    with open(LOCK_FILE, 'w') as f:
        f.write(str(os.getpid()))
    
    return False

# 信号处理函数
def signal_handler(sig, frame):
    """处理中断信号"""
    logger.info(f"接收到信号 {sig}，准备退出...")
    cleanup()
    sys.exit(0)

# 注册信号处理器
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

# 注册退出处理函数
atexit.register(cleanup)

if __name__ == "__main__":
    try:
        # 检查程序是否已经在运行
        if check_running():
            sys.exit(1)
        
        # 确保数据目录存在
        os.makedirs("data", exist_ok=True)
        os.makedirs("data/media", exist_ok=True)
        os.makedirs("data/sessions", exist_ok=True)
        
        # 确保事件循环已初始化
        init_loop()
        
        # 启动Web应用，禁用调试模式的reloader功能，启用多线程
        logger.info("启动Telegram定时消息发送工具...")
        app.run(host='0.0.0.0', port=5000, use_reloader=False, threaded=True)
        
    except KeyboardInterrupt:
        logger.info("接收到中断信号，程序退出")
        sys.exit(0)
    except Exception as e:
        logger.exception(f"程序发生错误: {str(e)}")
        sys.exit(1)
