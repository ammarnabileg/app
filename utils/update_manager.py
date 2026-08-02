import requests
import os
import sys
import subprocess
import threading
from utils.version_info import CURRENT_VERSION

UPDATE_API_URL = "https://onz.one/PHP/version_api.php"

def parse_version(v):
    """تحويل الإصدار إلى صفٍّ للمقارنة.

    يُكمَّل إلى أربعة أجزاء لا ثلاثة: صيغة الإصدار صارت
    MAJOR.MINOR.PATCH.BUILD، وقصّها عند ثلاثة يجعل 2.6.0.1 و2.6.0.9
    متساويين — فلا يُرى إصلاح عاجل تحديثًا.

    والمقارنة على صفٍّ لا قائمة: القائمة تُقارن عنصرًا عنصرًا كذلك، لكن
    الصفّ يمنع تعديلها سهوًا.
    """
    parts = [int(x) for x in str(v).split('.') if x.isdigit()]
    while len(parts) < 4:
        parts.append(0)
    return tuple(parts[:4])

def check_for_updates():
    """
    Returns: (update_available: bool, download_url: str, notes: str, mandatory: bool)
    """
    try:
        resp = requests.get(UPDATE_API_URL, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            if data.get('success'):
                remote_ver_str = data['version']
                download_url = data['download_url']
                notes = data.get('release_notes', '')
                mandatory = data.get('mandatory', False)
                
                local = parse_version(CURRENT_VERSION)
                remote = parse_version(remote_ver_str)
                
                # Compare semantic versions
                if remote > local:
                    return True, download_url, notes, mandatory
                    
    except Exception as e:
        print(f"[UpdateManager] Error checking updates: {e}")
        
    return False, None, None, False

def download_and_install_update(url, install_dir=None):
    """
    Downloads zip and launches updater.py
    """
    if not install_dir:
        install_dir = os.getcwd()
        
    try:
        print(f"[UpdateManager] Downloading update from {url}...")
        resp = requests.get(url, stream=True)
        if resp.status_code != 200:
            print("[UpdateManager] Failed to download file.")
            return False
            
        # Save to temp zip
        zip_path = os.path.join(install_dir, "update_pkg.zip")
        with open(zip_path, 'wb') as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
                
        print("[UpdateManager] Download complete. Launching updater...")
        
        print("[UpdateManager] Download complete. Launching updater...")
        
        # Determine app executable to restart
        app_name = os.path.basename(sys.argv[0])
        updater_cwd = os.path.join(install_dir, "tools")
        
        if getattr(sys, 'frozen', False):
            # Running as EXE -> Expect tools/updater.exe
            updater_exe = os.path.join(install_dir, "tools", "updater.exe")
            if os.path.exists(updater_exe):
                subprocess.Popen([updater_exe, zip_path, install_dir, app_name], cwd=install_dir)
                return True
            else:
                print(f"[UpdateManager] Error: {updater_exe} not found.")
                return False
        else:
            # Running as Python Script
            python_exe = sys.executable
            updater_script = os.path.join(install_dir, "tools", "updater.py")
            subprocess.Popen([python_exe, updater_script, zip_path, install_dir, app_name], cwd=install_dir)
            return True
        
    except Exception as e:
        print(f"[UpdateManager] Install failed: {e}")
        return False
