import os
import oracledb
import logging
from utils.db import DATA_DIR, get_db_connection, get_setting
import datetime
import time
import threading
import sys

# ================= MASTER SWITCH =================
_oracle_enabled_cache = {'value': None, 'ts': 0}

def is_oracle_enabled():
    """Master kill-switch: when off there are no connections, no queueing, no log noise."""
    now = time.time()
    if _oracle_enabled_cache['value'] is None or (now - _oracle_enabled_cache['ts']) > 10:
        try:
            _oracle_enabled_cache['value'] = str(get_setting('oracle_enabled', '1')).strip() == '1'
        except Exception:
            _oracle_enabled_cache['value'] = True
        _oracle_enabled_cache['ts'] = now
    return _oracle_enabled_cache['value']
# =================================================

def _oracle_conf(key, env_key, default):
    """DB-stored setting wins; empty falls back to the environment variable."""
    try:
        v = str(get_setting(key, '') or '').strip()
    except Exception:
        v = ''
    return v if v else os.environ.get(env_key, default)

# Configure a specific logger for Oracle Sync
from logging import FileHandler as RotatingFileHandler
oracle_log_file = os.path.join(DATA_DIR, 'oracle_sync.log')
oracle_logger = logging.getLogger('oracle_sync')
oracle_logger.setLevel(logging.INFO)

# Create rotating file handler (10MB per file, 5 backups)
if not oracle_logger.handlers:
    fh = RotatingFileHandler(oracle_log_file, encoding='utf-8')
    fh.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    fh.setFormatter(formatter)
    oracle_logger.addHandler(fh)

    # Console handler for terminal view
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(formatter)
    oracle_logger.addHandler(ch)

# Enable Thick mode for oracle client to bypass cryptography dependency and support older verifiers
try:
    if not is_oracle_enabled():
        raise RuntimeError('__oracle_disabled__')
    # Check if already in thick mode to avoid DPY-2017
    is_thick = False
    try:
        is_thick = oracledb.get_is_thick_mode()
    except AttributeError:
        # Older versions of oracledb might not have this method
        pass

    if not is_thick:
        # 1. Check if we have an explicit override in settings
        lib_dir = get_setting("oracle_lib_dir", "").strip()
        
        # 2. Read from db_config.json if it exists (like the tester tool)
        if not lib_dir:
            try:
                import json
                config_path = os.path.join(os.path.abspath("."), 'db_config.json')
                if os.path.exists(config_path):
                    with open(config_path, 'r', encoding='utf-8') as f:
                        config = json.load(f)
                        lib_dir = config.get('lib_dir', '').strip()
            except Exception:
                pass
                
        # 3. Try hardcoded common paths
        if not lib_dir:
            if os.path.exists("C:\\IBR-ZTKTECO-ORCAL\\instantclient"):
                lib_dir = "C:\\IBR-ZTKTECO-ORCAL\\instantclient"
        
        # 4. If no override, try to find bundled instantclient
        if not lib_dir:
            # Check for PyInstaller bundle path
            base_dir = getattr(sys, '_MEIPASS', os.path.abspath("."))
            possible_paths = [
                os.path.join(base_dir, "instantclient"), # Bundled in root or _internal
                os.path.join(os.path.abspath("."), "instantclient"), # Local dev
            ]
            
            for path in possible_paths:
                if os.path.exists(path) and any(f.endswith('.dll') for f in os.listdir(path)):
                    lib_dir = path
                    break

        if lib_dir:
            lib_dir_abs = os.path.abspath(lib_dir)
            oracle_logger.info(f"Initializing Oracle Client (Thick Mode) with lib_dir: {lib_dir_abs}")
            oracledb.init_oracle_client(lib_dir=lib_dir_abs)
        else:
            # Fallback to standard initialization if found in PATH
            oracledb.init_oracle_client()
    else:
        oracle_logger.debug("Oracle Client already initialized in Thick Mode.")
except Exception as e:
    # Catch the specific "already called" warning/error to be silent
    if '__oracle_disabled__' in str(e):
        pass
    elif "DPY-2017" in str(e) or "already called" in str(e).lower():
        oracle_logger.debug(f"Oracle Thick Mode already initialized: {e}")
    else:
        # Most likely client not found or other issues
        oracle_logger.warning(f"Oracle Thick Mode init info/error: {e}")
    pass

# ================= BACKGROUND HEARTBEAT =================
def check_oracle_connection():
    """Quick check if Oracle is reachable"""
    if not is_oracle_enabled():
        return False
    conn = get_oracle_connection()
    if conn:
        try:
            pass # conn.close() removed to prevent leak in Flask g
            return True
        except:
            return True
    return False

def process_sync_queue(oracle_conn=None):
    """Scan local SQLite queue and flush to Oracle"""
    if not is_oracle_enabled():
        return False
    should_close = False
    if oracle_conn is None:
        oracle_conn = get_oracle_connection()
        if not oracle_conn:
            return False
        should_close = True
        
    try:
        sqlite_conn = get_db_connection()
        records = sqlite_conn.execute("SELECT * FROM oracle_sync_queue WHERE status = 'PENDING' LIMIT 100").fetchall()
        
        if not records:
            sqlite_pass # conn.close() removed to prevent leak in Flask g
            return
            
        oracle_logger.info(f"Found {len(records)} pending records in local queue. Attempting to flush to Oracle...")
        ORACLE_TABLE_NAME = _oracle_conf('oracle_table', "ORACLE_TABLE_NAME", "ATTENDANCE_LOGS")
        oracle_cursor = oracle_conn.cursor()
        
        processed_ids = []
        for row in records:
            raw_user_id = str(row['user_id'])
            # Ensure USERID is numeric for Oracle if possible (prevents ORA-01722)
            user_id = "".join(filter(str.isdigit, raw_user_id))
            
            check_time = row['check_time']
            # Truncate check_type to 2 chars to prevent ORA-12899 (User changed to 3, but error said max 2)
            check_type = str(row['check_type'])[:2]
            
            verify_code = row['verify_code']
            
            # Map IP address (stored in device_id column of queue) to numeric sensor_id (max 5 chars)
            ip_addr = str(row['device_id'])
            sensor_id = "1" # Default
            try:
                # Find the numeric ID assigned to this IP in our local DB
                device_row = sqlite_conn.execute("SELECT id FROM fingerprint_devices WHERE device_ip = ?", (ip_addr,)).fetchone()
                if device_row:
                    sensor_id = str(device_row['id'])
                else:
                    # If it's already numeric (some legacy records), use it
                    if ip_addr.isdigit():
                        sensor_id = ip_addr[:5]
                    else:
                        # Fallback: use last segment of IP
                        sensor_id = ip_addr.split('.')[-1][:5]
            except:
                pass
            
            if not user_id:
                oracle_logger.warning(f"Skipping record for non-numeric user_id: {raw_user_id}")
                processed_ids.append(row['id']) # Mark as processed so we don't keep failing
                continue
            
            try:
                try:
                    parsed_time = datetime.datetime.strptime(check_time, '%Y-%m-%d %H:%M:%S')
                except ValueError:
                    parsed_time = check_time
                    
                insert_sql = f"""
                    INSERT INTO {ORACLE_TABLE_NAME} (USERID, CHECKTIME, CHECKTYPE, VERIFYCODE, SENSORID, WORKCODE) 
                    VALUES (:1, :2, :3, :4, :5, :6)
                """
                oracle_cursor.execute(insert_sql, (user_id, parsed_time, check_type, verify_code, sensor_id, 1))
                processed_ids.append(row['id'])
                
            except oracledb.IntegrityError:
                # Duplicate, considered processed
                processed_ids.append(row['id'])
                
            except Exception as e:
                oracle_logger.error(f"Failed to flush queued record {user_id}: {e}")
                # Increment retry count
                sqlite_conn.execute("UPDATE oracle_sync_queue SET retry_count = retry_count + 1 WHERE id = ?", (row['id'],))
        
        oracle_conn.commit()
        
        # Mark processed as synced
        if processed_ids:
            # ? parameters hack for IN clause
            placeholders = ','.join('?' * len(processed_ids))
            sqlite_conn.execute(f"UPDATE oracle_sync_queue SET status = 'SYNCED' WHERE id IN ({placeholders})", processed_ids)
            sqlite_conn.commit()
            oracle_logger.info(f"Flushed {len(processed_ids)} records from queue to Oracle.")
            
        if should_close:
            oracle_pass # conn.close() removed to prevent leak in Flask g
            
    except Exception as e:
        oracle_logger.error(f"Error processing sync queue: {e}")
        return False
    return True

def start_oracle_heartbeat():
    def heartbeat():
        while True:
            time.sleep(300)  # Check every 5 minutes instead of 1 hour
            if not is_oracle_enabled():
                continue
            try:
                conn = get_oracle_connection()
                if conn:
                    # oracle_logger.info("Oracle Connection Status: ALIVE") # Too noisy every 5 mins
                    process_sync_queue(conn)
                    pass # conn.close() removed to prevent leak in Flask g
                else:
                    oracle_logger.error("Oracle Heartbeat: Connection DEAD OR UNREACHABLE")
            except Exception as e:
                oracle_logger.error(f"Oracle Heartbeat Error: {e}")
                
    t = threading.Thread(target=heartbeat, daemon=True)
    t.start()

# Start heartbeat upon initialization
start_oracle_heartbeat()
# ========================================================

def get_oracle_connection():
    """Returns a connection to the Oracle database using python-oracledb"""
    
    # Reload environment variables to catch launcher changes
    ORACLE_HOST = _oracle_conf('oracle_host', "ORACLE_HOST", "localhost")
    ORACLE_PORT = _oracle_conf('oracle_port', "ORACLE_PORT", "1521")
    ORACLE_SERVICE = _oracle_conf('oracle_service', "ORACLE_SERVICE", "XEPDB1")
    ORACLE_USER = _oracle_conf('oracle_user', "ORACLE_USER", "HR")
    ORACLE_PASSWORD = _oracle_conf('oracle_password', "ORACLE_PASSWORD", "password")
    
    try:
        # Thick mode connection (Instant Client)
        dsn = f"{ORACLE_HOST}:{ORACLE_PORT}/{ORACLE_SERVICE}"
        oracle_logger.debug(f"Attempting to connect to Oracle: {ORACLE_HOST}:{ORACLE_PORT}/{ORACLE_SERVICE} as {ORACLE_USER}")
        
        # Adding tcp_connect_timeout=3 to prevent ORA-12170 long hangs
        # This will fail fast if the database is unreachable, avoiding UI freezing
        connection = oracledb.connect(
            user=ORACLE_USER, 
            password=ORACLE_PASSWORD, 
            dsn=dsn,
            tcp_connect_timeout=3
        )
        return connection
    except oracledb.DatabaseError as e:
        error_obj, = e.args
        if error_obj.code == 1017:
             oracle_logger.critical("CRITICAL: Oracle Login Failed - Check Credentials or Account Status (ORA-01017).")
        else:
             oracle_logger.error(f"Oracle Database Error ({error_obj.code}): {error_obj.message}")
        return None
    except oracledb.OperationalError as e:
        # Handles connection reset/closed issues like DPY-6005
        oracle_logger.error(f"Oracle Operational Error (Possible connection reset): {e}")
        return None
    except Exception as e:
        oracle_logger.error(f"Oracle Connection Error (Unknown): {e}")
        return None

def add_to_sync_queue(user_id, check_time, check_type, verify_code, device_id, sqlite_conn=None):
    """Adds a failed attendance record to the local SQLite DB to be retried later"""
    if not is_oracle_enabled():
        return
    try:
        conn = sqlite_conn or get_db_connection()
        conn.execute('''
            INSERT INTO oracle_sync_queue (user_id, check_time, check_type, verify_code, device_id, status)
            VALUES (?, ?, ?, ?, ?, 'PENDING')
        ''', (user_id, check_time, check_type, verify_code, device_id))
        if not sqlite_conn:
            conn.commit()
            pass # conn.close() removed to prevent leak in Flask g
        oracle_logger.info(f"Record added to local offline queue: UserID={user_id} at {check_time}")
    except Exception as e:
        oracle_logger.error(f"CRITICAL: Failed to add record to offline queue: {e}")

def insert_attendance_to_oracle(user_id, check_time, check_type, verify_code=None, device_id=0):
    """
    Inserts a single attendance record into the Oracle Database.
    Avoids duplicate entries by using a simple SELECT check or relying on Oracle UNIQUE constraints.
    """
    if not is_oracle_enabled():
        return True  # Oracle integration disabled on this installation
    start_time = time.time()
    ORACLE_TABLE_NAME = _oracle_conf('oracle_table', "ORACLE_TABLE_NAME", "ATTENDANCE_LOGS")
    
    conn = get_oracle_connection()
    if not conn:
        oracle_logger.error(f"Failed to get Oracle connection. Adding user {user_id} ({check_time}) to offline queue.")
        add_to_sync_queue(user_id, check_time, check_type, verify_code, device_id)
        return False

    cursor = None
    try:
        cursor = conn.cursor()
        
        # Parse the string into python datetime object to avoid NLS_DATE_FORMAT issues in Oracle
        try:
            # Assuming format "2025-01-01 09:00:00"
            parsed_time = datetime.datetime.strptime(check_time, '%Y-%m-%d %H:%M:%S')
        except ValueError:
            # Fallback to string if the format doesn't match
            parsed_time = check_time
            oracle_logger.warning(f"Could not parse datetime string '{check_time}', falling back to string insert.")
        
        insert_sql = f"""
            INSERT INTO {ORACLE_TABLE_NAME} (USERID, CHECKTIME, CHECKTYPE, VERIFYCODE, SENSORID, WORKCODE) 
            VALUES (:1, :2, :3, :4, :5, :6)
        """
        
        # Use execute mapping
        cursor.execute(insert_sql, (user_id, parsed_time, check_type, verify_code, str(device_id), 1))
        conn.commit()
        
        duration = round(time.time() - start_time, 2)
        oracle_logger.info(f"Successfully synced: UserID={user_id} | DeviceID={device_id} | Latency={duration}s")
        return True

    except oracledb.IntegrityError as e:
        # This handles the "Unique constraint already exists" issue 
        # that happens if the device sends the same log multiple times.
        oracle_logger.warning(f"Record already exists (Duplicate ignored) for {user_id} at {check_time}. Details: {e}")
        return True # Treat as success since the data is already there
        
    except oracledb.DatabaseError as e:
        error_obj, = e.args
        if error_obj.code == 942:
            oracle_logger.error(f"Table or view does not exist: {ORACLE_TABLE_NAME}")
        elif error_obj.code == 904:
            oracle_logger.error(f"Invalid column name while inserting to {ORACLE_TABLE_NAME}. Check columns USERID, CHECKTIME, CHECKTYPE, VERIFYCODE, SENSORID, WORKCODE")
        elif error_obj.code == 12899:
            oracle_logger.error(f"Value too large for column. UserID: {user_id}")
            # Do NOT queue if there's a schema mismatch (it'll always fail)
        else:
            oracle_logger.error(f"Oracle Database Error ({error_obj.code}): {error_obj.message} - Data: UserID={user_id}, Time={check_time}")
            add_to_sync_queue(user_id, check_time, check_type, verify_code, device_id)
        return False
        
    except Exception as e:
        oracle_logger.error(f"Unexpected error formatting or inserting record into Oracle: {type(e).__name__}: {str(e)}")
        add_to_sync_queue(user_id, check_time, check_type, verify_code, device_id)
        return False
        
    finally:
        if cursor:
            try:
                cursor.close()
            except:
                pass
        if conn:
            try:
                pass # conn.close() removed to prevent leak in Flask g
            except:
                pass

def get_sync_queue_stats():
    """Returns a summary of pending vs synced records in the local queue"""
    try:
        conn = get_db_connection()
        pending = conn.execute("SELECT count(*) FROM oracle_sync_queue WHERE status = 'PENDING'").fetchone()[0]
        synced = conn.execute("SELECT count(*) FROM oracle_sync_queue WHERE status = 'SYNCED'").fetchone()[0]
        failed = conn.execute("SELECT count(*) FROM oracle_sync_queue WHERE retry_count > 0 AND status = 'PENDING'").fetchone()[0]
        pass # conn.close() removed to prevent leak in Flask g
        return {
            'pending': pending,
            'synced': synced,
            'failed': failed
        }
    except Exception as e:
        oracle_logger.error(f"Error getting sync stats: {e}")
        return {'pending': 0, 'synced': 0, 'failed': 0}
