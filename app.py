import os
import logging
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session
from werkzeug.utils import secure_filename

from config import SECRET_KEY, DEBUG, MEDIA_FOLDER
from models import Chat, ScheduledMessage
from telegram_client import run_async, create_client, login, get_all_dialogs, disconnect, init_loop
from scheduler import init_scheduler, schedule_message, stop_scheduler, clean_old_files

# 初始化全局事件循环
init_loop()

# 创建Flask应用
app = Flask(__name__)
app.secret_key = SECRET_KEY
app.config['DEBUG'] = DEBUG
app.config['UPLOAD_FOLDER'] = MEDIA_FOLDER

# 确保上传目录存在
os.makedirs(MEDIA_FOLDER, exist_ok=True)

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("data/app.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# 允许上传的文件类型
ALLOWED_EXTENSIONS = {'txt', 'pdf', 'png', 'jpg', 'jpeg', 'gif', 'mp4', 'mp3', 'wav'}

def allowed_file(filename):
    """检查文件是否为允许的类型"""
    # 如果文件名中没有点，可能是没有扩展名
    if '.' not in filename:
        return True  # 允许上传，后续会根据MIME类型添加扩展名
    
    return filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/')
def index():
    """主页"""
    if not session.get('logged_in'):
        return redirect(url_for('login_page'))
    
    chats = Chat.get_all_chats()
    messages = ScheduledMessage.get_all()
    
    return render_template('index.html', chats=chats, messages=messages)

@app.route('/login', methods=['GET', 'POST'])
def login_page():
    """登录页面"""
    if request.method == 'POST':
        if 'phone_submit' in request.form:
            # 第一步：发送验证码
            phone = request.form['phone']
            session['phone'] = phone
            
            try:
                # 尝试创建客户端并发送验证码
                is_logged_in = run_async(create_client())
                
                if is_logged_in:
                    # 已登录
                    session['logged_in'] = True
                    flash('已登录到Telegram账号', 'success')
                    
                    # 初始化对话列表
                    run_async(get_all_dialogs())
                    
                    return redirect(url_for('index'))
                else:
                    # 需要验证码
                    flash('验证码已发送到您的Telegram账号', 'info')
                    return render_template('login.html', need_code=True)
            except Exception as e:
                logger.error(f"创建客户端时出错: {str(e)}")
                flash(f'连接Telegram失败: {str(e)}', 'danger')
                return render_template('login.html')
        
        elif 'code_submit' in request.form:
            # 第二步：输入验证码
            code = request.form['code']
            password = request.form.get('password', '')
            
            try:
                # 尝试登录
                success, message = run_async(login(code, password))
                
                if success:
                    session['logged_in'] = True
                    flash(message, 'success')
                    
                    # 初始化对话列表
                    try:
                        run_async(get_all_dialogs())
                    except Exception as e:
                        logger.error(f"获取对话列表失败: {str(e)}")
                    
                    return redirect(url_for('index'))
                else:
                    flash(message, 'danger')
                    return render_template('login.html', need_code=True, need_password=True)
            except Exception as e:
                logger.error(f"登录时出错: {str(e)}")
                flash(f'登录失败: {str(e)}', 'danger')
                return render_template('login.html', need_code=True, need_password=True)
    
    return render_template('login.html', need_code=False)

@app.route('/logout')
def logout():
    """退出登录"""
    # 断开Telegram客户端连接
    run_async(disconnect())
    
    # 清除会话
    session.clear()
    
    flash('已退出登录', 'info')
    return redirect(url_for('login_page'))

@app.route('/refresh_chats')
def refresh_chats():
    """刷新对话列表"""
    if not session.get('logged_in'):
        return redirect(url_for('login_page'))
    
    # 获取对话列表
    chats = run_async(get_all_dialogs())
    
    if chats:
        flash('对话列表已更新', 'success')
    else:
        flash('无法获取对话列表，请检查登录状态', 'danger')
    
    return redirect(url_for('index'))

@app.route('/add_user', methods=['POST'])
def add_user():
    """手动添加用户"""
    if not session.get('logged_in'):
        return redirect(url_for('login_page'))
    
    input_type = request.form.get('input_type', 'id')
    display_name = request.form.get('display_name', '')
    
    if input_type == 'id':
        user_id = request.form.get('user_id', '')
        if not user_id:
            flash('用户ID不能为空', 'danger')
            return redirect(url_for('index'))
        
        username = None
        chat_id = str(user_id)  # 确保是字符串
    else:  # input_type == 'username'
        username = request.form.get('username', '')
        if not username:
            flash('用户名不能为空', 'danger')
            return redirect(url_for('index'))
        
        # 对用户名处理，确保没有@前缀
        username = username.lstrip('@')
        chat_id = username
    
    # 添加用户到数据库
    try:
        Chat.save_chat(
            chat_id=chat_id,
            title=display_name,
            username=username,
            chat_type='user'
        )
        
        flash(f'用户 {display_name} 已添加', 'success')
    except Exception as e:
        flash(f'添加用户失败: {str(e)}', 'danger')
        logger.error(f"添加用户失败: {str(e)}")
    
    return redirect(url_for('index'))

@app.route('/schedule', methods=['POST'])
def schedule():
    """创建定时消息"""
    if not session.get('logged_in'):
        return redirect(url_for('login_page'))
    
    chat_id = request.form['chat_id']
    message_type = request.form['message_type']
    content = request.form['content']
    format_type = request.form.get('format', 'plain')
    schedule_time = request.form['schedule_time']
    
    # 确保时间格式正确
    if 'T' in schedule_time:
        # 如果是ISO格式，转换为标准格式
        logger.info(f"原始计划时间: {schedule_time}")
        schedule_time = schedule_time.replace('T', ' ')
        # 如果只有时和分，添加秒
        if len(schedule_time.split(':')) == 2:
            schedule_time = f"{schedule_time}:00"
        logger.info(f"转换后的计划时间: {schedule_time}")
    
    repeat_count = int(request.form.get('repeat_count', 1))
    
    # 获取撤回设置
    auto_delete = request.form.get('auto_delete', 'no')
    deleted_after_seconds = 0
    if auto_delete == 'yes':
        try:
            deleted_after_seconds = int(request.form.get('deleted_after_seconds', 0))
            if deleted_after_seconds < 0:
                deleted_after_seconds = 0
        except:
            deleted_after_seconds = 0
    
    # 处理媒体文件上传
    media_path = None
    if message_type == 'media' and 'media_file' in request.files:
        file = request.files['media_file']
        if file and allowed_file(file.filename):
            # 获取原始文件名和扩展名
            original_filename = secure_filename(file.filename)
            file_ext = os.path.splitext(original_filename)[1].lower()
            
            # 如果没有获取到扩展名，根据MIME类型设置默认扩展名
            if not file_ext:
                mime_type = file.content_type
                if mime_type:
                    if 'image/jpeg' in mime_type:
                        file_ext = '.jpg'
                    elif 'image/png' in mime_type:
                        file_ext = '.png'
                    elif 'image/gif' in mime_type:
                        file_ext = '.gif'
                    elif 'video/' in mime_type:
                        file_ext = '.mp4'
                    elif 'audio/' in mime_type:
                        file_ext = '.mp3'
                    else:
                        file_ext = '.bin'  # 默认二进制文件
                else:
                    file_ext = '.bin'  # 无法确定类型时默认为二进制文件
            
            # 使用时间戳作为前缀，确保扩展名使用点号
            timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
            filename = f"{timestamp}{file_ext}"
            
            # 记录文件信息以便调试
            logger.info(f"保存文件: 原始文件名={original_filename}, 扩展名={file_ext}, 最终文件名={filename}")
            
            media_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(media_path)
    
    # 安排定时消息
    success, result = schedule_message(
        chat_id=chat_id,
        message_type=message_type,
        content=content,
        schedule_time=schedule_time,
        format=format_type if format_type != 'plain' else None,
        media_path=media_path,
        repeat_count=repeat_count,
        deleted_after_seconds=deleted_after_seconds
    )
    
    if success:
        auto_delete_msg = ""
        if deleted_after_seconds > 0:
            auto_delete_msg = f"，将在发送后 {deleted_after_seconds} 秒自动撤回"
        flash(f'定时消息已创建，ID: {result}{auto_delete_msg}', 'success')
    else:
        flash(f'创建定时消息失败: {result}', 'danger')
    
    return redirect(url_for('index'))

@app.route('/delete_message/<int:msg_id>', methods=['POST'])
def delete_message(msg_id):
    """删除定时消息"""
    if not session.get('logged_in'):
        return redirect(url_for('login_page'))
    
    ScheduledMessage.delete(msg_id)
    flash(f'消息 {msg_id} 已删除', 'success')
    
    return redirect(url_for('index'))

@app.route('/cleanup', methods=['POST'])
def cleanup_files():
    """手动触发文件清理"""
    if not session.get('logged_in'):
        return redirect(url_for('login_page'))
    
    # 触发清理任务
    clean_old_files()
    
    flash('文件清理任务已触发，7天前的媒体文件和日志将被清理', 'info')
    return redirect(url_for('index'))

@app.route('/chats')
def get_chats():
    """获取对话列表（API）"""
    if not session.get('logged_in'):
        return jsonify({"error": "未登录"}), 401
    
    chats = Chat.get_all_chats()
    return jsonify([dict(chat) for chat in chats])

# 初始化调度器
init_scheduler()

@app.before_request
def before_request():
    """请求前处理"""
    # 需要登录的路由
    login_required_routes = ['index', 'refresh_chats', 'add_user', 'schedule', 'delete_message', 'get_chats']
    
    if request.endpoint in login_required_routes and not session.get('logged_in'):
        return redirect(url_for('login_page'))

@app.teardown_appcontext
def teardown_appcontext(exception=None):
    """应用上下文结束时的处理"""
    pass

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000) 