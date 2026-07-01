import sys
import os
import winreg

def set_autostart(enable=True):
    key = winreg.HKEY_CURRENT_USER
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    app_name = "RMS Print Server"
    
    if getattr(sys, 'frozen', False):
        exe_path = f'"{sys.executable}" --hide'
    else:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        main_script = os.path.join(base_dir, "main.py")
        exe_path = f'"{sys.executable}" "{main_script}" --hide'

    try:
        registry_key = winreg.OpenKey(key, key_path, 0, winreg.KEY_SET_VALUE)
        if enable:
            winreg.SetValueEx(registry_key, app_name, 0, winreg.REG_SZ, exe_path)
        else:
            winreg.DeleteValue(registry_key, app_name)
        winreg.CloseKey(registry_key)
        return True
    except Exception as e:
        print(f"Autostart Error: {e}")
        return False

def check_autostart():
    key = winreg.HKEY_CURRENT_USER
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    app_name = "RMS Print Server"
    try:
        registry_key = winreg.OpenKey(key, key_path, 0, winreg.KEY_READ)
        value, _ = winreg.QueryValueEx(registry_key, app_name)
        winreg.CloseKey(registry_key)
        return True
    except WindowsError:
        return False
