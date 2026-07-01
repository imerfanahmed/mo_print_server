import os
import sys

def get_base_path():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

def get_config_path(filename):
    # Determine the AppData directory to store configuration safely 
    # (since writing to Program Files requires admin rights)
    appdata_dir = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'MagicOfficeRMS')
    os.makedirs(appdata_dir, exist_ok=True)
    return os.path.join(appdata_dir, filename)
