import os
import yaml

# 加载配置文件
def load_config():
    config_path = os.path.join(os.path.dirname(__file__), 'config.yaml')
    if os.path.exists(config_path):
        with open(config_path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    else:
        raise FileNotFoundError(f"配置文件 {config_path} 不存在")

# 加载配置
config = load_config()

# Telegram API设置
API_ID = config['telegram']['api_id']
API_HASH = config['telegram']['api_hash']
PHONE = config['telegram']['phone']

# Flask配置
SECRET_KEY = config['flask']['secret_key']
DEBUG = config['flask']['debug']

# 数据库配置
DATABASE = config['database']['path']

# 媒体文件存储路径
MEDIA_FOLDER = config['media']['folder']

# 确保目录存在
os.makedirs(MEDIA_FOLDER, exist_ok=True) 