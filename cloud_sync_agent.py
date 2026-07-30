import sqlite3
import requests
import json
import time
from datetime import datetime, timedelta, timezone
import uuid
import sys
import os
import logging

# -------------------------------------------------------------
# OnPoint HR System - Universal Sync Agent (Simple + Secure)
# -------------------------------------------------------------

DATA_DIR = r'C:\ProgramData\HRSystem'
DB_PATH = os.path.join(DATA_DIR, 'hr_system.db')
CLOUD_API_BASE = "https://open.onz.one/public/api/v1/sync"
COMPANY_API_KEY = "dummy-company-api-key-123"
COMPANY_ID = 0

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("sync_log_universal.txt", encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
print = logging.info

# Mapping local table names to cloud table names
SYNC_TABLES = {
    'departments_master': 'departments',
    'positions_master': 'positions',
    'leave_types': 'leave_types',
    'shift_types': 'shift_types',
    'official_holidays': 'official_holidays',
    'employees': 'employees',
    'attendance_records': 'attendance_logs',
    'leave_requests': 'leave_requests',
    'salaries': 'salaries'
}

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def perform_handshake():
    global COMPANY_API_KEY, COMPANY_ID
    print("🤝 Performing Handshake with Cloud SaaS...")
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute("SELECT license_key, api_key FROM license_settings LIMIT 1")
            license_row = cursor.fetchone()
            if license_row and 'api_key' in license_row.keys() and license_row['api_key']:
                license_key = license_row['api_key']
            else:
                license_key = license_row['license_key'] if license_row and license_row['license_key'] else "DEFAULT_LICENSE_123"
        except Exception:
            cursor.execute("SELECT license_key FROM license_settings LIMIT 1")
            license_row = cursor.fetchone()
            license_key = license_row['license_key'] if license_row and license_row['license_key'] else "DEFAULT_LICENSE_123"
        
        cursor.execute("SELECT company_name FROM system_settings LIMIT 1")
        company_row = cursor.fetchone()
        company_name = company_row['company_name'] if company_row and company_row['company_name'] else "Unknown Local Company"
        pass # conn.close() removed to prevent leak in Flask g

        payload = {"license_key": license_key, "company_name": company_name}
        response = requests.post(f"{CLOUD_API_BASE}/handshake", json=payload, timeout=10)
        
        result = response.json()
        if result.get("status") == "success":
            COMPANY_API_KEY = result.get("token")
            # Decode token safely just to extract company_id locally if needed
            # For simplicity, we just use the token in headers and let the server handle identity.
            print(f"✅ Handshake successful! Registered as: {company_name}")
            
            # Extract company_id from token payload if possible, otherwise we rely on server.
            # Using a simple hack to get company_id since it's base64 encoded by server
            try:
                import base64
                decoded = base64.b64decode(COMPANY_API_KEY).decode('utf-8')
                token_data = json.loads(decoded)
                COMPANY_ID = token_data.get('company_id', 0)
                print(f"🏢 Company ID: {COMPANY_ID}")
            except:
                pass
                
            return True
        else:
            print(f"❌ Handshake failed: {result}")
            return False

    except Exception as e:
        print(f"❌ Handshake Exception: {e}")
        return False

def get_server_time_offset():
    print("🕒 Fetching Server Time for Drift Alignment...")
    try:
        response = requests.get(f"{CLOUD_API_BASE}/time", timeout=10)
        result = response.json()
        if result.get("status") == "success":
            server_time_str = result.get("server_time")
            server_time = datetime.strptime(server_time_str, "%Y-%m-%d %H:%M:%S")
            # Replace utcnow with timezone-aware now
            local_time = datetime.now(timezone.utc).replace(tzinfo=None)
            offset = server_time - local_time
            print(f"✅ Time Drift Offset Calculated: {offset.total_seconds()} seconds.")
            return offset
        else:
            print("❌ Failed to get server time. Assuming 0 drift.")
            return timedelta(0)
    except Exception as e:
        print(f"❌ Exception getting server time: {e}")
        return timedelta(0)

def get_corrected_time(offset):
    return datetime.now(timezone.utc).replace(tzinfo=None) + offset

def get_last_sync_time(conn):
    cursor = conn.cursor()
    cursor.execute("SELECT last_sync_time FROM sync_state WHERE company_id = ?", (COMPANY_ID,))
    row = cursor.fetchone()
    return row['last_sync_time'] if row else '1970-01-01 00:00:00'

def update_last_sync_time(conn, time_str):
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO sync_state (company_id, last_sync_time) VALUES (?, ?)", (COMPANY_ID, time_str))
    conn.commit()

def push_sync(conn, offset, last_sync_time):
    print("📤 Starting PUSH (Local -> Cloud)...")
    headers = {"Authorization": f"Bearer {COMPANY_API_KEY}", "Content-Type": "application/json"}
    
    for local_table, cloud_table in SYNC_TABLES.items():
        cursor = conn.cursor()
        cursor.execute(f"SELECT * FROM {local_table} WHERE updated_at > ? LIMIT 500", (last_sync_time,))
        records = cursor.fetchall()
        
        if not records:
            continue
            
        print(f"📦 Pushing {len(records)} records for {cloud_table}...")
        
        payload_records = []
        for row in records:
            row_dict = dict(row)
            row_dict['company_id'] = COMPANY_ID
            payload_records.append(row_dict)
            
        payload = {"table": cloud_table, "records": payload_records}
        
        try:
            response = requests.post(f"{CLOUD_API_BASE}/universal", json=payload, headers=headers, timeout=30)
            res_json = response.json()
            if res_json.get("status") == "success":
                print(f"✅ Push OK for {cloud_table}: Processed {res_json.get('processed')}, Ignored {res_json.get('ignored')}")
            else:
                print(f"❌ Push error for {cloud_table}: {res_json}")
        except Exception as e:
            print(f"❌ Network Error pushing {cloud_table}: {e}")

def pull_sync(conn, offset, last_sync_time):
    print("📥 Starting PULL (Cloud -> Local)...")
    headers = {"Authorization": f"Bearer {COMPANY_API_KEY}"}
    
    # We record the time right before pulling so we don't miss records created during the pull
    new_sync_time = get_corrected_time(offset).strftime("%Y-%m-%d %H:%M:%S")

    for local_table, cloud_table in SYNC_TABLES.items():
        try:
            response = requests.get(f"{CLOUD_API_BASE}/universal?table={cloud_table}&last_sync_time={last_sync_time}", headers=headers, timeout=30)
            res_json = response.json()
            
            if res_json.get("status") == "success":
                records = res_json.get("records", [])
                if not records:
                    continue
                
                print(f"📥 Pulling {len(records)} records for {local_table}...")
                cursor = conn.cursor()
                
                for r in records:
                    # Resolve Conflict: Last Write Wins
                    global_id = r['global_id']
                    cursor.execute(f"SELECT updated_at FROM {local_table} WHERE global_id = ?", (global_id,))
                    local_existing = cursor.fetchone()
                    
                    if local_existing:
                        # Compare timestamps
                        if r['updated_at'] <= local_existing['updated_at']:
                            continue # Local is newer, ignore cloud
                        
                        # Update Local
                        set_clauses = []
                        values = []
                        for k, v in r.items():
                            if k not in ('id', 'company_id') and k in local_existing.keys():
                                set_clauses.append(f"{k} = ?")
                                values.append(v)
                        
                        if set_clauses:
                            values.append(global_id)
                            sql = f"UPDATE {local_table} SET {', '.join(set_clauses)} WHERE global_id = ?"
                            cursor.execute(sql, values)
                    else:
                        # Insert Local
                        # Check schema
                        cursor.execute(f"PRAGMA table_info({local_table})")
                        local_cols = [c['name'] for c in cursor.fetchall()]
                        
                        fields = []
                        placeholders = []
                        values = []
                        for k, v in r.items():
                            if k in local_cols and k not in ('id', 'company_id'):
                                fields.append(k)
                                placeholders.append('?')
                                values.append(v)
                                
                        if fields:
                            sql = f"INSERT INTO {local_table} ({', '.join(fields)}) VALUES ({', '.join(placeholders)})"
                            cursor.execute(sql, values)
                            
                conn.commit()
                print(f"💾 Applied {len(records)} pulled records for {local_table}.")
            else:
                print(f"❌ Pull error for {cloud_table}: {res_json}")
        except Exception as e:
            print(f"❌ Network Error pulling {cloud_table}: {e}")
            
    return new_sync_time

if __name__ == "__main__":
    print("🚀 Initializing Universal Sync Agent (Simple & Secure)...")
    
    while not perform_handshake():
        time.sleep(10)

    while True:
        try:
            time_offset = get_server_time_offset()
            conn = get_db_connection()
            
            last_sync_time = get_last_sync_time(conn)
            print(f"\n🔄 Starting Sync Cycle... Last Sync: {last_sync_time}")
            
            # Step 1: PUSH Local Changes
            push_sync(conn, time_offset, last_sync_time)
            
            # Step 2: PULL Cloud Changes
            new_sync_time = pull_sync(conn, time_offset, last_sync_time)
            
            # Step 3: Update Sync State
            update_last_sync_time(conn, new_sync_time)
            
            pass # conn.close() removed to prevent leak in Flask g
            print("⏳ Waiting 60 seconds for next sync cycle...")
            time.sleep(60)
            
        except Exception as e:
            print(f"⚠️ Critical error in sync loop: {e}")
            time.sleep(60)
