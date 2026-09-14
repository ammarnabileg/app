import PyInstaller.__main__
import os
import shutil
import sys

def build_updater():
    print("Building Updater...")
    PyInstaller.__main__.run([
        'tools/updater.py',
        '--onefile',
        '--name=updater',
        '--distpath=dist/tools',
        '--workpath=build/updater',
        '--specpath=build/updater',
        '--clean',
        '--log-level=WARN'
    ])

def build_uninstaller():
    print("Building Uninstaller...")
    PyInstaller.__main__.run([
        'tools/uninstall.py',
        '--onefile',
        '--name=uninstall',
        '--distpath=dist/tools',
        '--workpath=build/uninstall',
        '--specpath=build/uninstall',
        '--clean',
        '--log-level=WARN'
    ])

def build_main_app(thin=False):
    print(f"Building HR System ({'THIN' if thin else 'FULL'})...")
    
    # Automatic detection of customtkinter path
    import customtkinter
    ctk_path = os.path.dirname(customtkinter.__file__)
    
    # Define hidden imports
    hidden_imports = [
        'babel.numbers',
        'babel.dates',
        'openpyxl',
        'requests',
        'jinja2.ext',
        'pystray',
        'PIL.Image',
        'cryptography.hazmat.primitives.kdf.pbkdf2',
        'cryptography.hazmat.backends.openssl',
        'cryptography.hazmat.bindings.openssl.binding',
        'cryptography.hazmat.primitives.ciphers',
        'cryptography',
        'zk',
        'oracledb',
        'pandas',
        'flask_babel',
        'adms_server',
        'fingerprint_sync'
    ]
    
    args = [
        'launcher.py',
        '--noconfirm',
        '--onedir',
        '--name=HRSystem',
        '--windowed',
        f'--icon={os.path.abspath("app_icon.ico")}',
        f'--add-data={os.path.abspath("templates")};templates',
        f'--add-data={os.path.abspath("static")};static',
        f'--add-data={os.path.abspath("translations")};translations',
        f'--add-data={ctk_path};customtkinter',
        '--distpath=dist' ,
        '--workpath=build/main',
        '--specpath=build/main',
        '--clean',
        # Add instantclient to search path for DLL resolution
        f'--paths={os.path.abspath("instantclient")}'
    ]
    
    # Add instantclient to build if it exists and NOT a thin build
    if not thin:
        if os.path.exists("instantclient"):
            args.append(f'--add-data={os.path.abspath("instantclient")};instantclient')
    else:
        print("!!! SKIP bundling instantclient (Thin Build) !!!")
    
    for imp in hidden_imports:
        args.append(f'--hidden-import={imp}')
        
    PyInstaller.__main__.run(args)

def is_running(process_name):
    import subprocess
    try:
        output = subprocess.check_output('tasklist /FI "IMAGENAME eq ' + process_name + '"', shell=True).decode()
        return process_name.lower() in output.lower()
    except:
        return False

if __name__ == "__main__":
    # Check if app is running
    if is_running("HRSystem.exe"):
        print("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
        print("ERROR: HRSystem.exe is still running!")
        print("Please close the application (and its tray icon) before building.")
        print("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
        sys.exit(1)

    # Clean prev build
    if os.path.exists('dist'):
        try:
            shutil.rmtree('dist', ignore_errors=True)
        except Exception as e:
            print(f"Warning: Could not clean dist folder: {e}")
        
    # 1. Build Updater
    build_updater()
    
    # 2. Build Uninstaller
    build_uninstaller()
    
    # 3. Build Main App
    is_thin = "--thin" in sys.argv
    build_main_app(thin=is_thin)
    
    print("\n------------------------------------------------")
    print("Build Complete!")
    print("Output: dist/HRSystem/HRSystem.exe")
    print("Updater: dist/tools/updater.exe")
    print("------------------------------------------------")
    
    # Move tools to main app tools folder
    dest_tools = 'dist/HRSystem/tools'
    if not os.path.exists(dest_tools):
        os.makedirs(dest_tools)
        
    for tool in ['updater.exe', 'uninstall.exe']:
        src = os.path.join('dist/tools', tool)
        if os.path.exists(src):
            shutil.copy(src, os.path.join(dest_tools, tool))
            print(f"Copied {tool} to dist/HRSystem/tools/")
