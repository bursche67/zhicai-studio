import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / '.env')

def db_path():
    return Path(os.getenv('APP_DB', str(ROOT / 'data/app.db')))

def storage_path():
    return Path(os.getenv('APP_STORAGE', str(ROOT / 'data/uploads')))
