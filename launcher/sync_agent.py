import json
import time
import requests
import sqlite3
import os
import sys
import logging
from datetime import datetime

# --- Configuration ---
# --- Configuration ---
# Resolve config relative to the script directory
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(SCRIPT_DIR, 'config.json')
LOG_FILE = os.path.join(SCRIPT_DIR, 'sync_agent.log')

# Setup Logging
# Configure STDOUT handler explicitly
stdout_handler = logging.StreamHandler(sys.stdout)
stdout_handler.flush = sys.stdout.flush

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        stdout_handler
    ]
)

def load_config():
    if not os.path.exists(CONFIG_FILE):
        logging.info(f"Config file not found at: {CONFIG_FILE}")
        return None
    with open(CONFIG_FILE, 'r') as f:
        return json.load(f)

def save_config(config):
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=4)

def get_credentials_wizard():
    print("\n--- HR System Sync Agent Setup ---")
    print("Please enter your Client Portal credentials to fetch your API Key.")
    
    api_base = input("Enter Portal URL (e.g., http://onz.one): ").strip()
    if not api_base:
        api_base = "http://localhost:8000" # Default dev
    
    # Ensure no trailing slash
    api_base = api_base.rstrip('/')
    
    username = input("Username: ").strip()
    password = input("Password: ").strip()
    
    login_url = f"{api_base}/api/get_credentials"
    
    try:
        response = requests.post(login_url, json={'username': username, 'password': password})
        
        if response.status_code == 200:
            data = response.json()
            if data.get('success'):
                print("\n✅ Authentication Successful!")
                print(f"Client ID: {data['client_id']}")
                print(f"API Key: {data['api_key']}")
                
                return {
                    "api_url": data.get('api_url', f"{api_base}/api/sync"),
                    "client_id": data['client_id'],
                    "api_key": data['api_key'],
                    "sync_interval_seconds": 60,
                    "db_path": r"C:\Users\It\Desktop\hr ma\hr_system.db"
                }
            else:
                print(f"\n❌ Error: {data.get('message')}")
        else:
            print(f"\n❌ Server Error ({response.status_code}): {response.text}")
            
    except Exception as e:
        print(f"\n❌ Connection Error: {e}")
        
    return None

def fetch_local_data(db_path, last_sync_times=None):
    if not os.path.exists(db_path):
        logging.error(f"Database not found at: {db_path}")
        return {}, last_sync_times

    if not isinstance(last_sync_times, dict):
        last_sync_times = {}

    data = {}
    new_max_times = last_sync_times.copy()

    # Table -> Timestamp Column mapping
    # Used for both filtering (> last_sync) and finding new max
    sync_config = {
        'employees': 'created_at',
        'departments_master': 'created_at',
        'positions_master': 'created_at',
        'fingerprint_devices': 'created_at',
        'attendance_records': 'check_time',
        'leave_requests': 'created_at'
    }

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        for table, time_col in sync_config.items():
            try:
                table_last_sync = last_sync_times.get(table)
                
                # Build query based on whether we have a checkpoint
                if table_last_sync:
                    # Incremental fetch
                    query = f"SELECT * FROM {table} WHERE {time_col} > ? ORDER BY {time_col} ASC"
                    # Limit attendance heavy tables to avoid huge chunks (batches handle sending, but memory is finite)
                    if table == 'attendance_records':
                         query += " LIMIT 500"
                    
                    cursor.execute(query, (table_last_sync,))
                else:
                    # Initial Load
                    query = f"SELECT * FROM {table} ORDER BY {time_col} ASC"
                     # Limit large tables on first load to prevent choking
                    if table == 'attendance_records':
                         query += " LIMIT 1000"
                    
                    cursor.execute(query)

                rows = [dict(row) for row in cursor.fetchall()]
                
                # Only add to payload if we have data
                if rows:
                    data[table] = rows
                    
                    # Update High Water Mark (Checkpoint)
                    # We look at the 'latest' timestamp in this batch
                    # Note: SQLite dates are strings. string comparison works for ISO8601.
                    timestamps = [r[time_col] for r in rows if r.get(time_col)]
                    if timestamps:
                        batch_max = max(timestamps)
                        current_max = new_max_times.get(table)
                        if not current_max or batch_max > current_max:
                            new_max_times[table] = batch_max
                            
            except sqlite3.OperationalError as e:
                # Table might not exist or column missing
                # logging.warning(f"Skipping table {table}: {e}")
                pass
            except Exception as e:
                logging.error(f"Error syncing table {table}: {e}")

        conn.close()
        return data, new_max_times

    except Exception as e:
        logging.error(f"Error reading local DB: {e}")
        return {}, last_sync_time

def send_data(config, data):
    url = config['api_url']
    headers = {
        'Content-Type': 'application/json',
        'X-API-KEY': config['api_key']
    }

    BATCH_SIZE = 50

    for table, rows in data.items():
        if not rows:
            continue

        total_rows = len(rows)
        logging.info(f"Syncing table '{table}' ({total_rows} records)...")

        for i in range(0, total_rows, BATCH_SIZE):
            batch = rows[i:i + BATCH_SIZE]
            
            # Construct payload for just this batch
            payload = {
                'client_id': config['client_id'],
                'timestamp': datetime.now().isoformat(),
                'data': {
                    table: batch
                }
            }

            try:
                logging.info(f"  Sending batch {i//BATCH_SIZE + 1} ({len(batch)} rows)...")
                response = requests.post(url, json=payload, headers=headers)
                
                if response.status_code == 200:
                    # Optional: Log success if needed, but keep it quiet for successful batches
                    pass 
                else:
                    logging.error(f"  ❌ Batch Failed ({response.status_code}): {response.text}")
            
            except Exception as e:
                logging.error(f"  ❌ Network Error sending batch: {e}")
                # Optional: break or continue? Continue allows other batches to succeed.




def main():
    config = load_config()
    
    if not config or not config.get('api_key') or "YOUR_CLIENT_API_KEY_HERE" in config.get('api_key'):
        logging.info("Configuration missing or invalid.")
        new_config = get_credentials_wizard()
        if new_config:
            save_config(new_config)
            config = new_config
        else:
            logging.error("Setup failed. Exiting.")
            return

    logging.info("Starting Sync Agent...")
    logging.info(f"Target: {config['api_url']}")
    logging.info(f"Client ID: {config['client_id']}")
    
    # Get last sync times from config
    last_sync_times = config.get('last_sync_timestamps', {})
    if not isinstance(last_sync_times, dict):
        last_sync_times = {}

    while True:
        logging.info("Fetching local data...")
        data, new_max_times = fetch_local_data(config['db_path'], last_sync_times)
        
        has_data = any(len(rows) > 0 for rows in data.values())
        
        if has_data:
            total_records = sum(len(v) for v in data.values())
            logging.info(f"Sending {total_records} records...")
            
            send_data(config, data)
            
            # Update Config if timestamp advanced for any table
            updated = False
            for table, max_time in new_max_times.items():
                old_time = last_sync_times.get(table)
                if max_time and (not old_time or max_time > old_time):
                    last_sync_times[table] = max_time
                    updated = True
                    
            if updated:
                config['last_sync_timestamps'] = last_sync_times
                save_config(config)
                logging.info(f"Updated checkpoints: {last_sync_times}")
        else:
            logging.info("No new data to sync.")

        logging.info(f"Waiting {config['sync_interval_seconds']} seconds for next sync...")
        try:
            for remaining in range(config['sync_interval_seconds'], 0, -1):
                sys.stdout.write(f"\r⏳ Next sync in {remaining} seconds...   ")
                sys.stdout.flush()
                time.sleep(1)
            sys.stdout.write("\r" + " " * 40 + "\r") # Clear line
        except KeyboardInterrupt:
            print("\nStopping Sync Agent...")
            return

if __name__ == "__main__":
    main()
