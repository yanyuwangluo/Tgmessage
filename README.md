# Telegram定时消息发送工具

这是一个基于Telegram用户API（非机器人API）的定时消息发送工具，可以定时向频道、群组或用户发送各种格式的消息，并支持自动撤回功能。

## 功能特点

- 支持发送多种格式的消息：普通文本、Markdown格式、HTML格式、媒体文件
- 可以设置消息的发送时间和重复次数
- **新功能：定时撤回** - 支持在消息发送后自动撤回
- **新功能：媒体优化** - 所有媒体文件以图片/媒体预览格式发送
- **新功能：本地清理** - 自动清理7天前的媒体文件和日志
- 自动获取并存储用户的频道和群组列表
- 支持手动添加用户联系人
- 提供简洁直观的Web界面
- 使用SQLite本地存储数据

## 安装

### 前提条件

- Python 3.7+
- Telegram账号
- Telegram API密钥（API_ID和API_HASH）

### 获取Telegram API密钥

1. 访问 https://my.telegram.org/
2. 登录您的Telegram账号
3. 点击"API development tools"
4. 创建一个新应用（填写应用名称和简短描述）
5. 获取API_ID和API_HASH

### 安装步骤

1. 克隆仓库或下载源代码：

```bash
git clone https://github.com/yanyuwangluo/Tgmessage.git
cd Tgmessage
```

2. 安装依赖包：

```bash
pip install -r requirements.txt
```

3. 配置：

编辑`config.yaml`文件，填入您的Telegram API信息：

```yaml
# Telegram API设置
telegram:
  api_id: 您的API_ID
  api_hash: 您的API_HASH
  phone: 您的手机号（带国际区号，如+8613912345678）
```

## 使用方法

1. 启动应用：

```bash
python main.py
```

2. 打开浏览器访问：`http://localhost:5000`

3. 登录您的Telegram账号：
   - 输入您的手机号（带国际区号）
   - 输入Telegram发送给您的验证码
   - 如果设置了两步验证，还需要输入密码

4. 登录成功后，您可以：
   - 刷新对话列表，获取最新的频道和群组
   - 手动添加用户联系人
   - 创建定时消息，选择发送时间和重复次数
   - **设置自动撤回**：选择"发送后自动撤回"并设置撤回时间（秒）
   - 管理已创建的定时消息
   - **清理旧文件**：使用导航栏中的"清理旧文件"按钮手动触发清理

## 媒体文件支持

本工具支持以下类型的媒体文件：

- **图片**：jpg, jpeg, png, gif, webp, bmp, tiff
- **视频**：mp4, avi, mov, mkv, wmv, flv, 3gp
- **音频**：mp3, wav, ogg, m4a, flac, aac, wma

所有媒体文件都将以媒体预览格式发送，不会作为文档附件。

## 自动化维护

系统会自动执行以下维护任务：

- **文件清理**：每7天自动清理旧的媒体文件（7天前的文件）
- **日志管理**：每7天截断日志文件，仅保留最近100条记录
- **消息撤回**：按照设定的时间自动撤回已发送的消息

## 注意事项

- 本工具使用的是Telegram用户API，而非机器人API，因此可以访问您账号中的所有对话
- 请勿滥用此工具发送垃圾消息，这可能导致您的Telegram账号被限制
- 媒体文件将存储在本地，请确保服务器有足够的存储空间
- 首次登录时，Telegram可能会要求您进行额外的验证
- 自动撤回功能依赖于您的Telegram账号权限，在某些群组可能无法撤回消息

## 技术栈

- 后端：Python, Flask
- 数据库：SQLite
- Telegram API：Telethon
- 定时任务：APScheduler
- 前端：Bootstrap 5

## 许可证

[MIT License](LICENSE)