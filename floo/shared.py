from collections import defaultdict

__VERSION__ = '0.02'

DEBUG = False

PLUGIN_PATH = None

MAX_RETRIES = 20
INITIAL_RECONNECT_DELAY = 500  # milliseconds

CONNECTED = False

COLAB_DIR = ''
PROJECT_PATH = ''
DEFAULT_HOST = ''
DEFAULT_PORT = ''
SECURE = True

USERNAME = ''
SECRET = ''

ALERT_ON_MSG = True

PERMS = []

ROOM_WINDOW = None

CHAT_VIEW = None
CHAT_VIEW_PATH = None

FOLLOW_MODE = False

LOCKED_VIEWS = defaultdict(int)
