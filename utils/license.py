import hashlib
import base64
import sqlite3
from datetime import date, datetime
from cryptography.fernet import Fernet
import requests
import time
import hmac
import json
from utils.db import get_db_connection
from utils.system_id import get_system_hwid

# Secrets and Keys
LICENSE_SECRET = 'hr-system-license-secret-2024'
LICENSE_KEYS = []
LICENSE_DB_ENABLED = True
LICENSE_WORDS = ['Maped$', 'Mabed #', 'comaped#']

# Online Config
API_URL = "https://onz.one/PHP/license_api.php"
CLIENT_LOGIN_URL = "https://onz.one/PHP/client_login.php"
API_SECRET = 'hr-system-license-secret-2024' # Shared with PHP
ONLINE_CHECK_ENABLED = True

def _get_fernet():
    try:
        key = base64.urlsafe_b64encode(hashlib.sha256(LICENSE_SECRET.encode('utf-8')).digest())
        return Fernet(key)
    except Exception:
        return None

def save_secure_max_devices(count):
    from utils.db import set_setting
    f = _get_fernet()
    if f:
        try:
            encrypted = f.encrypt(str(count).encode()).decode()
            set_setting('max_devices_secure', encrypted)
        except:
            pass
    set_setting('max_devices', str(count))

def get_effective_max_devices():
    """حدّ الأجهزة المعتمد — من الرخصة الموقّعة أولًا.

    الترتيب مقصود: الرخصة الموقّعة لا يمكن تعديلها عند العميل، أما القيمة
    المحلية فكانت تُغيَّر بصفٍّ واحد في قاعدة البيانات. فالمحلية لا تُستخدم
    إلا في غياب رخصة موقّعة — أي أثناء الترحيل من الصيغة القديمة.
    """
    try:
        from utils.db import get_setting
        from utils.license_verify import verify_license_token
        token = get_setting('license_token')
        if token:
            hwid = None
            try:
                from utils.system_id import get_system_hwid
                hwid = get_system_hwid()
            except Exception:
                pass
            r = verify_license_token(token, current_hwid=hwid)
            if r['ok']:
                return int(r['max_devices'])
            # رخصة موجودة لكنها فشلت: لا يُرقَّى إلى المسار القديم، وإلا صار
            # إتلاف الرخصة وسيلةً لتجاوز الحدّ.
            return 0
    except Exception:
        pass
    # لا رخصة موقّعة بعد — مسار الترحيل من النسخ السابقة
    return get_secure_max_devices()


def get_license_state():
    """حالة الرخصة للعرض والتشخيص. لا يرفع استثناءً."""
    out = {'mode': 'legacy', 'ok': False, 'reason': None,
           'days_left': None, 'max_devices': 0}
    try:
        from utils.db import get_setting
        from utils.license_verify import verify_license_token
        token = get_setting('license_token')
        if not token:
            out['max_devices'] = get_secure_max_devices()
            out['reason'] = 'no_signed_license'
            return out
        hwid = None
        try:
            from utils.system_id import get_system_hwid
            hwid = get_system_hwid()
        except Exception:
            pass
        r = verify_license_token(token, current_hwid=hwid)
        out.update({'mode': 'signed', 'ok': r['ok'], 'reason': r['reason'],
                    'days_left': r['days_left'],
                    'max_devices': r['max_devices']})
    except Exception as e:
        out['reason'] = 'state_error:' + type(e).__name__
    return out


def get_secure_max_devices():
    from utils.db import get_setting
    secure_val = get_setting('max_devices_secure')
    f = _get_fernet()
    if secure_val and f:
        try:
            decrypted = f.decrypt(secure_val.encode()).decode()
            return int(decrypted)
        except:
            return 3
    
    plain_val = get_setting('max_devices', '3')
    try:
        return int(plain_val)
    except:
        return 3

def activate_license_by_login(username, password):
    """
    Authenticates user online and fetches license key.
    """
    try:
        hwid = get_system_hwid()
        payload = {
            'username': username,
            'password': password,
            'hwid': hwid
        }
        
        response = requests.post(CLIENT_LOGIN_URL, json=payload, timeout=10)
        
        if response.status_code == 200:
            res = response.json()
            if res.get('success'):
                key = res.get('license_key')
                api_key = res.get('api_key') # Fetch api_key from the server response
                if key:
                    # Update max_devices instantly from login API if provided
                    if 'max_devices' in res:
                        try:
                            max_devs = int(res['max_devices'])
                            save_secure_max_devices(max_devs)
                        except Exception as e:
                            print(f"Error saving max_devices from login: {e}")
                            
                    # Validate and Save
                    save_license_key(key, username, api_key) # Pass api_key
                    # Verify immediately
                    ok, msg = verify_license_full_flow(key)
                    if ok:
                        return True, "تم التفعيل بنجاح"
                    else:
                        return False, f"تم جلب المفتاح ولكنه غير صالح: {msg}"
                return False, "لم يتم العثور على مفتاح مرتبط بالحساب"
            else:
                return False, res.get('message', 'فشل تسجيل الدخول')
        elif response.status_code == 404:
             return False, f"لم يتم العثور على ملف الربط على السيرفر ({CLIENT_LOGIN_URL}). تأكد من رفع الملف للمسار الصحيح."
        elif response.status_code == 500:
             return False, f"خطأ داخلي في السيرفر (500). راجع سجلات السيرفر أو تأكد من إعدادات قاعدة البيانات."
        else:
            return False, f"Server Error: {response.status_code} - {response.text[:50]}"
            
    except Exception as e:
        return False, f"Connection Error: {str(e)}"

def encode_license_payload_fernet(date_part: str, token: str) -> str:
    """Produces LE2-<base64-url> for encrypted date and token."""
    f = _get_fernet()
    if not f:
        raise RuntimeError('cryptography not installed')
    blob = f"{date_part}|{token}".encode('utf-8')
    ct = f.encrypt(blob)
    return 'LE2-' + ct.decode('utf-8')

def decode_license_payload_fernet(payload: str):
    f = _get_fernet()
    if not f:
        return None, None
    try:
        pt = f.decrypt(payload.encode('utf-8'), ttl=None)
        txt = pt.decode('utf-8', errors='ignore')
        if '|' not in txt:
            return None, None
        date_part, token = txt.split('|', 1)
        return date_part, token
    except Exception:
        return None, None

def init_license_table(conn):
    cur = conn.cursor()
    # Create table with new columns if it acts as a fresh create
    cur.execute('''
        CREATE TABLE IF NOT EXISTS license_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            license_key TEXT,
            api_key TEXT,
            last_check DATETIME,
            last_status_ok INTEGER DEFAULT 0
        )
    ''')
    cur.execute("INSERT OR IGNORE INTO license_settings (id, license_key) VALUES (1, NULL)")
    
    # Simple migration for existing tables using PRAGMA or try/catch ALTER
    try:
        cur.execute("ALTER TABLE license_settings ADD COLUMN last_check DATETIME")
    except sqlite3.OperationalError:
        pass # Column likely exists
        
    try:
        cur.execute("ALTER TABLE license_settings ADD COLUMN last_status_ok INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass # Column likely exists
        
    try:
        cur.execute("ALTER TABLE license_settings ADD COLUMN client_username TEXT")
    except sqlite3.OperationalError:
        pass # Column likely exists
        
    try:
        cur.execute("ALTER TABLE license_settings ADD COLUMN api_key TEXT")
    except sqlite3.OperationalError:
        pass # Column likely exists
    
    conn.commit()

def _xor_bytes(data: bytes, key: bytes) -> bytes:
    if not key:
        return data
    out = bytearray(len(data))
    for i, b in enumerate(data):
        out[i] = b ^ key[i % len(key)]
    return bytes(out)

def _encrypt_key(plain: str) -> str:
    raw = plain.encode('utf-8')
    sec = LICENSE_SECRET.encode('utf-8')
    enc = _xor_bytes(raw, sec)
    return 'E:' + base64.b64encode(enc).decode('utf-8')

def _decrypt_key(enc_val: str) -> str:
    try:
        if not enc_val or not enc_val.startswith('E:'):
            return enc_val or ''
        b = base64.b64decode(enc_val[2:].encode('utf-8'))
        sec = LICENSE_SECRET.encode('utf-8')
        dec = _xor_bytes(b, sec)
        return dec.decode('utf-8', errors='ignore')
    except Exception:
        return ''

def encode_license_payload(date_part: str, token: str) -> str:
    raw = f"{date_part}|{token}".encode('utf-8')
    sec = LICENSE_SECRET.encode('utf-8')
    enc = _xor_bytes(raw, sec)
    return base64.b64encode(enc).decode('utf-8')

def decode_license_payload(payload: str):
    try:
        b = base64.b64decode(payload.encode('utf-8'))
        sec = LICENSE_SECRET.encode('utf-8')
        dec = _xor_bytes(b, sec).decode('utf-8', errors='ignore')
        if '|' not in dec:
            return None, None
        date_part, token = dec.split('|', 1)
        return date_part, token
    except Exception:
        return None, None

def get_saved_license_key():
    # Backwards compatibility wrapper
    details = get_saved_license_details()
    return details.get('key')

def get_saved_license_details():
    conn = None
    try:
        conn = get_db_connection()
        init_license_table(conn)
        row = conn.execute('SELECT license_key, client_username, api_key FROM license_settings WHERE id=1').fetchone()
        if row:
            key = None
            user = None
            api_key = None
            if isinstance(row, sqlite3.Row):
                stored = row['license_key']
                user = row['client_username']
                api_key = row['api_key']
            else:
                stored = row[0] if row else None
                # Tuple fallback usually doesn't have index 1 if col didn't exist before, but we handle exception in init
                
            if stored:
                key = _decrypt_key(stored)
                return {'key': key, 'username': user, 'api_key': api_key}
        return {}
    except Exception as e:
        print(f"Error in get_saved_license_details: {e}")
        return {}
    finally:
        if conn:
            pass # conn.close() removed to prevent leak in Flask g

def save_license_key(key: str, username: str = None, api_key: str = None):
    conn = get_db_connection()
    try:
        init_license_table(conn)
        enc = _encrypt_key(key.strip())
        # Clear cache fields (last_check, last_status_ok) to force an immediate online check for the new key
        if username and api_key:
             conn.execute('UPDATE license_settings SET license_key=?, client_username=?, api_key=?, last_check=NULL, last_status_ok=0 WHERE id=1', (enc, username, api_key))
        elif username:
             conn.execute('UPDATE license_settings SET license_key=?, client_username=?, last_check=NULL, last_status_ok=0 WHERE id=1', (enc, username))
        else:
             conn.execute('UPDATE license_settings SET license_key=?, last_check=NULL, last_status_ok=0 WHERE id=1', (enc,))
        conn.commit()
    finally:
        pass # conn.close() removed to prevent leak in Flask g

def logout_license():
    conn = get_db_connection()
    try:
        init_license_table(conn)
        conn.execute('UPDATE license_settings SET license_key=NULL, client_username=NULL WHERE id=1')
        conn.commit()
    finally:
        pass # conn.close() removed to prevent leak in Flask g

def verify_license_key(key: str):
    try:
        if not key:
            return False, 'مفتاح غير صالح'
        date_part = None
        sig = None
        if key.startswith('LE-') and len(key) > 3:
            payload = key[3:]
            date_part, sig = decode_license_payload(payload)
            if not date_part or not sig:
                return False, 'تنسيق السيريال المشفّر غير صحيح'
        elif key.startswith('LE2-') and len(key) > 4:
            payload = key[4:]
            date_part, sig = decode_license_payload_fernet(payload)
            if not date_part or not sig:
                return False, 'تنسيق السيريال المشفّر (LE2) غير صحيح'
        else:
            if not key.startswith('L-'):
                return False, 'تنسيق المفتاح غير صحيح'
            parts = key.split('-')
            if len(parts) != 3:
                return False, 'تنسيق المفتاح غير صحيح'
            _, date_part, sig = parts
        
        if len(date_part) != 8 or not date_part.isdigit():
            return False, 'تاريخ انتهاء غير صحيح'
            
        # Allow salt in token (Token|Salt) for uniqueness
        if sig and '|' in sig:
            sig = sig.split('|')[0]

        ok_sig = False
        expected = hashlib.sha256((date_part + LICENSE_SECRET).encode('utf-8')).hexdigest()[:12].lower()
        if sig.lower() == expected:
            ok_sig = True
        if not ok_sig and sig in LICENSE_WORDS:
            ok_sig = True
        if not ok_sig:
            return False, 'توقيع/كلمة المفتاح غير معتمدة'
            
        expiry = datetime.strptime(date_part, '%Y%m%d').date()
        if date.today() > expiry:
            return False, 'انتهت مدة الترخيص'
        return True, None
    except Exception as e:
        return False, str(e)

def _extract_expiry_from_key(key: str):
    if not key:
        return False, None
    try:
        if key.startswith('LE2-'):
            payload = key[4:]
            date_part, _ = decode_license_payload_fernet(payload)
        elif key.startswith('LE-'):
            payload = key[3:]
            date_part, _ = decode_license_payload(payload)
        elif key.startswith('L-'):
            parts = key.split('-')
            if len(parts) != 3:
                return False, None
            _, date_part, _ = parts
        else:
            return False, None
            
        if not date_part or len(date_part) != 8 or not date_part.isdigit():
            return False, None
        return True, datetime.strptime(date_part, '%Y%m%d').date()
    except Exception:
        return False, None

def check_online_license_secure(key):
    """
    Verifies license with onz.one API (HMAC + HWID).
    Returns: (bool, message)
    Implements 24-hour Caching to reduce API load.
    """
    if not ONLINE_CHECK_ENABLED:
        return True, "Offline mode"

    conn = None
    try:
        conn = get_db_connection()
        init_license_table(conn)
        
        # 1. Check Cache
        row = conn.execute("SELECT last_check, last_status_ok FROM license_settings WHERE id=1").fetchone()
        
        should_check_online = True
        
        if row and row['last_check']:
            try:
                # Parse DB timestamp (format often 'YYYY-MM-DD HH:MM:SS' via SQLite default)
                # Or custom string depending on how we save. We will save as ISO or timestamp.
                last_check_str = str(row['last_check'])
                # Handle possible formats
                if '.' in last_check_str:
                     last_check_dt = datetime.strptime(last_check_str.split('.')[0], '%Y-%m-%d %H:%M:%S')
                else:
                     last_check_dt = datetime.strptime(last_check_str, '%Y-%m-%d %H:%M:%S')
                     
                elapsed = (datetime.now() - last_check_dt).total_seconds()
                
                # Check if cache is valid (less than 1 hour AND status was OK)
                if elapsed < 3600 and row['last_status_ok'] == 1:
                    should_check_online = False
                    # Return Cached Result
                    # print(f"[License] Using Cached Valid Status (Age: {int(elapsed/3600)}h)")
                    if conn: pass # conn.close() removed to prevent leak in Flask g
                    return True, "Valid (Cached)"
                    
            except Exception as e:
                # If parsing fails, force check
                print(f"[License] Cache parse error: {e}")
                should_check_online = True

        if not should_check_online:
             if conn: pass # conn.close() removed to prevent leak in Flask g
             return True, "Valid (Cached)"

        # 2. Perform Online Check
        hwid = get_system_hwid()
        timestamp = int(time.time())
        
        # HMAC Signature
        data_string = f"{key}{hwid}{timestamp}"
        signature = hmac.new(
            API_SECRET.encode('utf-8'), 
            data_string.encode('utf-8'), 
            hashlib.sha256
        ).hexdigest()
        
        payload = {
            'license_key': key,
            'hwid': hwid,
            'timestamp': timestamp,
            'signature': signature
        }
        
        # Timeout 5s
        response = requests.post(API_URL, json=payload, timeout=5)
        
        is_valid = False
        msg = "Unknown"
        
        if response.status_code == 200:
            res_json = response.json()
            if res_json.get('success'):
                is_valid = True
                msg = res_json.get('message', 'Valid')
                
                # Update max_devices limit based on license API return
                if 'max_devices' in res_json:
                    try:
                        max_devs = int(res_json['max_devices'])
                        save_secure_max_devices(max_devs)
                    except Exception as e:
                        print(f"Error saving max_devices from license: {e}")
                        
            else:
                is_valid = False
                msg = res_json.get('message', 'Invalid')
                
                if 'blocked' in msg.lower() or 'locked' in msg.lower():
                    # Block Logic
                    try:
                        conn.execute('UPDATE license_settings SET license_key = NULL WHERE id=1')
                        conn.commit()
                        print("LICENSE BLOCKED BY SERVER. LOCAL KEY DELETED.")
                    except:
                        pass
        else:
             is_valid = False
             msg = f"Server Error: {response.status_code}"

        # 3. Update Cache in DB
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        status_int = 1 if is_valid else 0
        
        conn.execute("UPDATE license_settings SET last_check = ?, last_status_ok = ? WHERE id=1", (now_str, status_int))
        conn.commit()
        
        return is_valid, msg
            
    except requests.exceptions.RequestException as e:
        # Grace Period Logic
        print(f"License Server Warning: {e}") 
        # On failure, if we had a valid cache before (even if expired recently?), maybe allow?
        # For now, standard grace logic: "Server Unreachable => Allow"
        if conn: pass # conn.close() removed to prevent leak in Flask g
        return True, "Server Unreachable (Grace Period)"
        
    except Exception as e:
        print(f"License Check Error: {e}")
        if conn: pass # conn.close() removed to prevent leak in Flask g
        return False, str(e)
    finally:
        if conn:
            try: pass # conn.close() removed to prevent leak in Flask g
            except: pass

_LICENSE_CACHE = None
_LICENSE_CACHE_TIME = 0
_LICENSE_CACHE_TTL = 60 # 1 minute cache

def get_current_license_info():
    global _LICENSE_CACHE, _LICENSE_CACHE_TIME
    
    # Check cache
    now = time.time()
    if _LICENSE_CACHE and (now - _LICENSE_CACHE_TIME < _LICENSE_CACHE_TTL):
        return _LICENSE_CACHE

    details = get_saved_license_details()
    chosen_key = details.get('key')
    username = details.get('username')
    
    # Fallback to older logic if no DB key but hardcoded keys exist (unlikely in new flow but safe to keep)
    if not chosen_key:
         for k in (LICENSE_KEYS or []):
            ok, err = verify_license_full_flow(k)
            if ok:
                chosen_key = k
                break
            
    if not chosen_key:
        return {'ok': False}
        
    ok_extract, expiry = _extract_expiry_from_key(chosen_key)
    if not ok_extract or not expiry:
        return {'ok': False}
        
    # Verify validity
    ok, err = verify_license_full_flow(chosen_key)
    if not ok:
        res = {'ok': False, 'message': err}
        _LICENSE_CACHE = res
        _LICENSE_CACHE_TIME = now
        return res

    days_left = (expiry - date.today()).days
    
    try:
        max_devices = get_secure_max_devices()
        from utils.db import get_db_connection
        conn = get_db_connection()
        try:
            current_active = conn.execute('SELECT COUNT(*) FROM fingerprint_devices WHERE is_active = 1').fetchone()[0]
        except Exception:
            current_active = 0
        pass # conn.close() removed to prevent leak in Flask g
    except Exception:
        max_devices = 3
        current_active = 0
        
    res = {'ok': True, 'expiry': expiry, 'days_left': days_left, 'username': username, 'max_devices': max_devices, 'active_devices': current_active}
    
    # Update cache
    _LICENSE_CACHE = res
    _LICENSE_CACHE_TIME = now
    
    return res

def verify_license_full_flow(key):
    """
    1. Local Format/Date Check
    2. Online HWID Check (Cached)
    """
    # 1. Local Check
    # print(f"[License] Checking Key Locally: {key}")
    local_ok, local_msg = verify_license_key(key)
    if not local_ok:
        # print(f"[License] Local Check Failed: {local_msg}")
        return False, local_msg
        
    # 2. Online Check (Now Cached)
    # print(f"[License] Verify Online (Cached 24h)...")
    online_ok, online_msg = check_online_license_secure(key)
    # print(f"[License] Result: {online_ok}, Msg: {online_msg}")
    
    if not online_ok:
        return False, f"Online Check Failed: {online_msg}"
        
    return True, "Valid"
