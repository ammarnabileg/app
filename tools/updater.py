
import sys
import os
import time
import zipfile
import shutil
import subprocess

def log(msg):
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    full_msg = f"[{timestamp}] [Updater] {msg}"
    print(full_msg)
    try:
        # Log to a file for field debugging
        with open("update.log", "a", encoding="utf-8") as f:
            f.write(full_msg + "\n")
    except:
        pass

def main():
    # Usage: updater.py <zip_path> <install_dir> <exe_name_to_restart>
    if len(sys.argv) < 4:
        log("Usage: updater.py <zip_path> <install_dir> <exe_name>")
        input("Press Enter to exit...")
        sys.exit(1)

    zip_path = sys.argv[1]
    install_dir = sys.argv[2]
    exe_name = sys.argv[3]

    log("------------------------------------------")
    log(f"Starting update process...")
    log(f"Zip: {zip_path}")
    log(f"Target: {install_dir}")
    log(f"Process to restart: {exe_name}")

    # 1. Wait for main app to close (Retry loop for extraction)
    log("Waiting for application to release files (max 30s)...")
    success = False
    max_retries = 30
    
    for i in range(max_retries):
        try:
            # Try to extract the whole package. If files are locked, this throws PermissionError.
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(install_dir)
            success = True
            log(f"Extraction complete on attempt {i+1}.")
            break
        except PermissionError:
            if i % 5 == 0: log(f"Files still locked/app still running, retrying... ({i+1}/{max_retries})")
            time.sleep(1)
        except Exception as e:
            log(f"Critical error during extraction: {e}")
            break

    if not success:
        log("FAILED: Could not extract update package. App might still be running.")
        input("Update failed. Please ensure the app is closed and try again. Press Enter...")
        sys.exit(1)

    # 3. Clean up Zip
    try:
        if os.path.exists(zip_path):
            os.remove(zip_path)
            log("Cleanup: Update package removed.")
    except Exception as e:
        log(f"Warning: Could not remove zip: {e}")

    # 4. Restart App
    exe_path = os.path.join(install_dir, exe_name)
    if os.path.exists(exe_path):
        log(f"Restarting {exe_name}...")
        try:
            if exe_name.lower().endswith('.py'):
                subprocess.Popen([sys.executable, exe_path], cwd=install_dir)
            else:
                subprocess.Popen([exe_path], cwd=install_dir)
            log("App restarted successfully.")
        except Exception as e:
            log(f"Failed to restart app: {e}")
    else:
        log(f"Executable not found: {exe_path}")

    log("Update process finished.")
    time.sleep(1)
    sys.exit(0)

if __name__ == "__main__":
    main()
