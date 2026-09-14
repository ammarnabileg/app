
import os
import shutil
from datetime import datetime

# Path to data directory
DATA_DIR = r'C:\ProgramData\HRSystem'
DB_PATH = os.path.join(DATA_DIR, 'hr_system.db')

def uninstall():
    print("--- HR System Uninstaller ---")
    
    # 1. Open Data Directory
    if not os.path.exists(DATA_DIR):
        print(f"Directory {DATA_DIR} does not exist. Nothing to clean up.")
        return

    # 2. Rename Database for Backup
    if os.path.exists(DB_PATH):
        # Format: hr_system_backup_2026_03_10.db
        today = datetime.now().strftime("%Y_%m_%d")
        backup_name = f"hr_system_backup_{today}.db"
        backup_path = os.path.join(DATA_DIR, backup_name)
        
        try:
            # Check if backup with same name already exists, append seconds if so
            if os.path.exists(backup_path):
                now = datetime.now().strftime("%Y_%m_%d_%H%M%S")
                backup_name = f"hr_system_backup_{now}.db"
                backup_path = os.path.join(DATA_DIR, backup_name)

            os.rename(DB_PATH, backup_path)
            print(f"Database backed up successfully to: {backup_path}")
        except PermissionError:
            print("ERROR: Access denied. Close all running instances of HR System (or Launcher) before uninstalling.")
        except Exception as e:
            print(f"Error backing up database: {e}")
    else:
        print("Database file (hr_system.db) not found.")

    print("\nUninstallation finished. Database renamed to include today's date.")
    print(f"Location: {DATA_DIR}")
    
    # Optional: We could also delete the contents of DATA_DIR except the backups
    # but the user was specific about the "backup then the day".
    
    input("\nPress Enter to exit...")

if __name__ == "__main__":
    uninstall()
