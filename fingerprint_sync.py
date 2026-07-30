"""
نظام مزامنة أجهزة البصمة المحدث
=====================================
يقوم بجلب بيانات المستخدمين والحضور من أجهزة ZKTeco ويحفظها في قاعدة البيانات
"""

import logging
import sqlite3
import time
import json
import os
from datetime import datetime
from typing import List, Dict, Tuple
from dataclasses import dataclass
from utils.db import DB_PATH, DATA_DIR
from utils.license import get_secure_max_devices

try:
    from zk import ZK
    ZK_AVAILABLE = True
except ImportError:
    ZK_AVAILABLE = False
    print("⚠️ مكتبة pyzk غير مثبتة. سيتم تشغيل النظام في وضع المحاكاة")

# ---- تكوين السجلات ----
LOG_FILE = os.path.join(DATA_DIR, 'fingerprint_sync.log')
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    encoding='utf-8'
)

@dataclass
class SyncLog:
    """فئة لحفظ سجلات المزامنة"""
    timestamp: datetime
    level: str
    device_ip: str
    message: str
    details: str = ""

class FingerprintSyncManager:
    """مدير مزامنة أجهزة البصمة"""
    
    def __init__(self, db_path=None):
        self.db_path = db_path if db_path else DB_PATH
        self.sync_logs = []
        self.batch_size = 1000
        
    def log_message(self, level: str, device_ip: str, message: str, details: str = ""):
        """إضافة رسالة للسجل"""
        log_entry = SyncLog(
            timestamp=datetime.now(),
            level=level,
            device_ip=device_ip,
            message=message,
            details=details
        )
        self.sync_logs.append(log_entry)
        
        # كتابة في ملف السجل أيضاً
        log_msg = f"{device_ip} - {message}"
        if details:
            log_msg += f" - {details}"
            
        try:
            if level == "INFO":
                logging.info(log_msg)
            elif level == "ERROR":
                logging.error(log_msg)
            elif level == "WARNING":
                logging.warning(log_msg)
        except UnicodeEncodeError:
            # Prevent console encoding issues (e.g. cp1252) from crashing the thread
            try:
                safe_msg = log_msg.encode('ascii', 'replace').decode('ascii')
                if level == "INFO":
                    logging.info(safe_msg)
                elif level == "ERROR":
                    logging.error(safe_msg)
                elif level == "WARNING":
                    logging.warning(safe_msg)
            except:
                pass
    
    def get_db_connection(self):
        """إنشاء اتصال بقاعدة البيانات"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
    
    def ensure_tables_exist(self):
        """التأكد من وجود الجداول المطلوبة مع تحسين منع التكرار"""
        conn = self.get_db_connection()
        cursor = conn.cursor()
        
        try:
            # جدول أجهزة البصمة
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS fingerprint_devices (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_name TEXT NOT NULL,
                    device_ip TEXT UNIQUE NOT NULL,
                    device_port INTEGER DEFAULT 4370,
                    is_active BOOLEAN DEFAULT 1,
                    last_sync_time DATETIME,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # جدول سجلات الحضور والانصراف (بدون UNIQUE constraint)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS attendance_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    employee_id INTEGER NOT NULL,
                    device_id INTEGER NOT NULL,
                    check_time DATETIME NOT NULL,
                    check_type INTEGER NOT NULL,
                    verify_code INTEGER DEFAULT 0,
                    sensor_id INTEGER DEFAULT 1,
                    work_code INTEGER DEFAULT 0,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (employee_id) REFERENCES employees (id),
                    FOREIGN KEY (device_id) REFERENCES fingerprint_devices (id)
                )
            ''')
            
            # جدول مستخدمي البصمة (للمطابقة مع الموظفين)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS fingerprint_users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    device_id INTEGER NOT NULL,
                    name TEXT,
                    privilege INTEGER DEFAULT 0,
                    password TEXT,
                    group_id TEXT,
                    card_number TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (device_id) REFERENCES fingerprint_devices (id),
                    UNIQUE(user_id, device_id)
                )
            ''')
            
            # جدول سجلات البصمة (للعرض في صفحة السجلات)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS sync_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    level TEXT NOT NULL,
                    device_ip TEXT,
                    message TEXT NOT NULL,
                    details TEXT
                )
            ''')
            
            conn.commit()
            self.log_message("INFO", "SYSTEM", "تم إنشاء الجداول بنجاح")
            
            # تحسين الفهارس لمنع التكرار وتحسين الأداء
            self.optimize_attendance_indexes()
            
        except Exception as e:
            self.log_message("ERROR", "SYSTEM", "خطأ في إنشاء الجداول", str(e))
            raise
        finally:
            pass # conn.close() removed to prevent leak in Flask g
    
    def get_active_devices(self) -> List[Dict]:
        """جلب الأجهزة النشطة من قاعدة البيانات"""
        conn = self.get_db_connection()
        try:
            # Retrieve max_devices limit
            max_devices = get_secure_max_devices()
            
            devices = conn.execute('''
                SELECT * FROM fingerprint_devices 
                WHERE is_active = 1 
                ORDER BY created_at ASC
                LIMIT ?
            ''', (max_devices,)).fetchall()
            
            device_list = []
            for device in devices:
                device_dict = dict(device)
                device_list.append(device_dict)
            
            self.log_message("INFO", "SYSTEM", f"تم جلب {len(device_list)} جهاز نشط")
            return device_list
            
        except Exception as e:
            self.log_message("ERROR", "SYSTEM", "خطأ في جلب الأجهزة", str(e))
            return []
        finally:
            pass # conn.close() removed to prevent leak in Flask g
    
    def connect_to_device(self, device_info: Dict, timeout: int = 20) -> Tuple[object, str]:
        """الاتصال بجهاز البصمة"""
        if not ZK_AVAILABLE:
            return None, "مكتبة pyzk غير متوفرة"
        
        device_ip = device_info['device_ip']
        device_port = device_info.get('device_port', 4370)
        
        try:
            self.log_message("INFO", device_ip, "بدء الاتصال بالجهاز")
            zk = ZK(device_ip, port=device_port, timeout=timeout)
            conn_dev = zk.connect()
            
            if not conn_dev:
                error_msg = "فشل في الاتصال بالجهاز"
                self.log_message("ERROR", device_ip, error_msg)
                return None, error_msg
            
            self.log_message("INFO", device_ip, "تم الاتصال بنجاح")
            return conn_dev, None
            
        except Exception as e:
            error_msg = f"خطأ في الاتصال: {str(e)}"
            self.log_message("ERROR", device_ip, error_msg)
            return None, error_msg
    
    def sync_device_users(self, device_info: Dict) -> int:
        """مزامنة مستخدمي جهاز البصمة"""
        device_ip = device_info['device_ip']
        device_id = device_info['id']
        
        # ADMS CHECK
        if device_info.get('is_adms'):
            self.log_message("INFO", device_ip, "جهاز ADMS: إرسال أمر تحديث المستخدمين...")
            try:
                db_conn = self.get_db_connection()
                # Queue Query Command
                db_conn.execute('''
                    INSERT INTO adms_commands (device_id, command_type, payload, status)
                    VALUES (?, 'DATA QUERY USERINFO', '{}', 'PENDING')
                ''', (device_id,))
                # Also request templates
                db_conn.execute('''
                    INSERT INTO adms_commands (device_id, command_type, payload, status)
                    VALUES (?, 'DATA QUERY FINGERTMP', '{}', 'PENDING')
                ''', (device_id,))
                
                db_conn.commit()
                pass # conn.close() removed to prevent leak in Flask g
                self.log_message("INFO", device_ip, "تم إدراج أوامر التحديث في قائمة الانتظار")
                return 0 # Async process
            except Exception as e:
                 self.log_message("ERROR", device_ip, "خطأ في إرسال أوامر ADMS", str(e))
                 return 0

        conn_dev, error = self.connect_to_device(device_info)
        if error:
            return 0
        
        try:
            # تعطيل الجهاز مؤقتاً
            conn_dev.disable_device()
            
            # جلب المستخدمين
            users = conn_dev.get_users()
            self.log_message("INFO", device_ip, f"تم جلب {len(users)} مستخدم")
            
            if not users:
                return 0
            
            # حفظ المستخدمين في قاعدة البيانات
            db_conn = self.get_db_connection()
            saved_count = 0
            
            for user in users:
                try:
                    db_conn.execute('''
                        INSERT OR REPLACE INTO fingerprint_users 
                        (user_id, device_id, name, privilege)
                        VALUES (?, ?, ?, ?)
                    ''', (user.uid, device_id, user.name, user.privilege))
                    saved_count += 1
                except Exception as e:
                    self.log_message("WARNING", device_ip, f"خطأ في حفظ مستخدم {user.uid}", str(e))
            
            db_conn.commit()
            pass # conn.close() removed to prevent leak in Flask g
            
            self.log_message("INFO", device_ip, f"تم حفظ {saved_count} مستخدم")
            return saved_count
            
        except Exception as e:
            self.log_message("ERROR", device_ip, "خطأ في مزامنة المستخدمين", str(e))
            return 0
        finally:
            try:
                if conn_dev:
                    conn_dev.enable_device()
                    conn_dev.disconnect()
            except:
                pass
    
    def get_check_type_description(self, check_type: int) -> str:
        """الحصول على وصف نوع البصمة حسب CHECKTYPE"""
        check_types = {
            0: "خروج",
            1: "دخول", 
            2: "بداية راحة",
            3: "نهاية راحة",
            4: "عمل إضافي",
            5: "انتهاء عمل إضافي",
            255: "غير معرف"
        }
        return check_types.get(check_type, f"غير معروف ({check_type})")

    def sync_device_attendance(self, device_info: Dict) -> int:
        """مزامنة سجلات الحضور من جهاز البصمة مع منع التكرار الكامل"""
        device_ip = device_info['device_ip']
        device_id = device_info['id']
        
        conn_dev, error = self.connect_to_device(device_info)
        if error:
            return 0
        
        try:
            # تعطيل الجهاز مؤقتاً
            conn_dev.disable_device()
            
            # جلب سجلات الحضور
            attendance_logs = conn_dev.get_attendance()
            self.log_message("INFO", device_ip, f"تم جلب {len(attendance_logs)} سجل حضور من الجهاز")
            
            if not attendance_logs:
                return 0
            
            # فلترة السجلات الجديدة فقط (بعد آخر مزامنة)
            last_sync = device_info.get('last_sync_time')
            if last_sync:
                last_sync_dt = datetime.fromisoformat(last_sync) if isinstance(last_sync, str) else last_sync
                attendance_logs = [log for log in attendance_logs if log.timestamp > last_sync_dt]
                self.log_message("INFO", device_ip, f"بعد فلترة آخر مزامنة: {len(attendance_logs)} سجل جديد")
            
            if not attendance_logs:
                self.log_message("INFO", device_ip, "لا توجد سجلات جديدة منذ آخر مزامنة")
                return 0
            
            # حفظ السجلات في قاعدة البيانات مع منع التكرار الكامل
            db_conn = self.get_db_connection()
            saved_count = 0
            duplicate_count = 0
            employee_not_found_count = 0
            max_timestamp = None
            
            for i in range(0, len(attendance_logs), self.batch_size):
                batch = attendance_logs[i:i + self.batch_size]
                
                for log in batch:
                    try:
                        # التحقق من وجود الموظف في النظام
                        employee_exists = db_conn.execute('''
                            SELECT id FROM employees WHERE employee_number = ?
                        ''', (str(log.user_id),)).fetchone()
                        
                        if employee_exists:
                            employee_id = employee_exists['id']
                            
                            # تحديد نوع البصمة الصحيح
                            check_type = getattr(log, 'status', 0)
                            check_type_desc = self.get_check_type_description(check_type)
                            
                            # تنسيق التاريخ بدون الـ T
                            formatted_time = log.timestamp.strftime('%Y-%m-%d %H:%M:%S')
                            
                            # التحقق من عدم وجود السجل مسبقاً باستخدام EXISTS (أكثر كفاءة)
                            record_exists = db_conn.execute('''
                                SELECT EXISTS(
                                    SELECT 1 FROM attendance_records 
                                    WHERE employee_id = ? 
                                    AND device_id = ? 
                                    AND check_time = ? 
                                    AND check_type = ?
                                ) as record_exists
                            ''', (employee_id, device_id, formatted_time, check_type)).fetchone()['record_exists']
                            
                            if record_exists:
                                duplicate_count += 1
                                self.log_message("INFO", device_ip, 
                                               f"سجل مكرر تم تجاهله: موظف {log.user_id} - {check_type_desc} في {log.timestamp.strftime('%H:%M:%S')}")
                            else:
                                # إدراج سجل الحضور الجديد
                                try:
                                    db_conn.execute('''
                                        INSERT INTO attendance_records
                                        (employee_id, device_id, check_time, check_type, verify_code, sensor_id, work_code)
                                        VALUES (?, ?, ?, ?, ?, ?, ?)
                                    ''', (
                                        employee_id, device_id, formatted_time, check_type,
                                        getattr(log, 'punch', 0), getattr(log, 'sensor_id', 1), 
                                        getattr(log, 'workcode', 0)
                                    ))
                                    saved_count += 1
                                    
                                    # سجل تفصيلي للمزامنة
                                    self.log_message("INFO", device_ip, 
                                                   f"حفظ سجل جديد: موظف {log.user_id} - {check_type_desc} في {log.timestamp.strftime('%H:%M:%S')}")
                                    
                                except Exception as insert_error:
                                    self.log_message("ERROR", device_ip, 
                                                   f"فشل في إدراج سجل: موظف {log.user_id} - {check_type_desc}", str(insert_error))
                            
                            # تتبع أحدث وقت (مع حماية ضد التواريخ المستقبلية الخاطئة)
                            current_server_time = datetime.now()
                            safe_timestamp = log.timestamp if log.timestamp <= current_server_time else current_server_time
                            if not max_timestamp or safe_timestamp > max_timestamp:
                                max_timestamp = safe_timestamp
                        else:
                            employee_not_found_count += 1
                            self.log_message("WARNING", device_ip, 
                                           f"موظف غير موجود برقم {log.user_id}")
                    
                    except Exception as e:
                        self.log_message("ERROR", device_ip, 
                                       f"خطأ في معالجة سجل للموظف {log.user_id}", str(e))
                
                # حفظ الدفعة
                db_conn.commit()
            
            # تحديث وقت آخر مزامنة
            if max_timestamp:
                formatted_sync_time = max_timestamp.strftime('%Y-%m-%d %H:%M:%S')
                db_conn.execute('''
                    UPDATE fingerprint_devices 
                    SET last_sync_time = ? 
                    WHERE id = ?
                ''', (formatted_sync_time, device_id))
                db_conn.commit()
            
            pass # conn.close() removed to prevent leak in Flask g
            
            # إحصائيات مفصلة
            total_processed = saved_count + duplicate_count + employee_not_found_count  
            summary_msg = f"مزامنة مكتملة: {saved_count} سجل جديد محفوظ"
            if duplicate_count > 0:
                summary_msg += f"، {duplicate_count} سجل مكرر تم تجاهله"
            if employee_not_found_count > 0:
                summary_msg += f"، {employee_not_found_count} موظف غير موجود"
            
            self.log_message("INFO", device_ip, summary_msg)
            return saved_count
            
        except Exception as e:
            self.log_message("ERROR", device_ip, "خطأ في مزامنة الحضور", str(e))
            return 0
        finally:
            try:
                if conn_dev:
                    conn_dev.enable_device()
                    conn_dev.disconnect()
            except:
                pass
    
    def sync_all_devices(self) -> Dict:
        """مزامنة جميع الأجهزة النشطة"""
        start_time = time.time()
        
        # التأكد من وجود الجداول
        self.ensure_tables_exist()
        
        # مسح السجلات السابقة
        self.sync_logs.clear()
        
        # جلب الأجهزة النشطة
        devices = self.get_active_devices()
        
        if not devices:
            self.log_message("WARNING", "SYSTEM", "لا توجد أجهزة نشطة للمزامنة")
            return {
                'success': False,
                'total_devices': 0,
                'users_synced': 0,
                'attendance_synced': 0,
                'duration': 0,
                'logs': self.sync_logs
            }
        
        total_users = 0
        total_attendance = 0
        successful_devices = 0
        
        # مزامنة كل جهاز
        for device in devices:
            device_ip = device['device_ip']
            
            try:
                self.log_message("INFO", device_ip, "بدء مزامنة الجهاز")
                
                # مزامنة المستخدمين
                users_count = self.sync_device_users(device)
                total_users += users_count
                
                # مزامنة الحضور
                attendance_count = self.sync_device_attendance(device)
                total_attendance += attendance_count
                
                if users_count > 0 or attendance_count > 0:
                    successful_devices += 1
                    self.log_message("INFO", device_ip, 
                                   f"انتهت المزامنة: {users_count} مستخدم، {attendance_count} سجل حضور")
                else:
                    self.log_message("INFO", device_ip, "لا توجد بيانات جديدة للمزامنة")
                
            except Exception as e:
                self.log_message("ERROR", device_ip, "خطأ عام في مزامنة الجهاز", str(e))
        
        duration = time.time() - start_time
        
        # النتيجة النهائية
        result = {
            'success': successful_devices > 0,
            'total_devices': len(devices),
            'successful_devices': successful_devices,
            'users_synced': total_users,
            'attendance_synced': total_attendance,
            'duration': round(duration, 2),
            'logs': self.sync_logs
        }
        
        self.log_message("INFO", "SYSTEM", 
                        f"انتهت المزامنة الكاملة: {successful_devices}/{len(devices)} أجهزة، "
                        f"{total_users} مستخدم، {total_attendance} سجل حضور في {duration:.2f} ثانية")
        
        return result
    
    def get_sync_logs(self, limit: int = 100) -> List[Dict]:
        """جلب آخر سجلات المزامنة"""
        try:
            if not hasattr(self, 'sync_logs') or self.sync_logs is None:
                return []
            logs = []
            for log in self.sync_logs[-limit:]:
                try:
                    logs.append({
                        'timestamp': log.timestamp.strftime('%Y-%m-%d %H:%M:%S') if hasattr(log, 'timestamp') else '',
                        'level': getattr(log, 'level', 'INFO'),
                        'device_ip': getattr(log, 'device_ip', ''),
                        'message': getattr(log, 'message', ''),
                        'details': getattr(log, 'details', '')
                    })
                except Exception as e:
                    print(f"خطأ في معالجة سجل: {e}")
                    continue
            return logs
        except Exception as e:
            print(f"خطأ في get_sync_logs: {e}")
            import traceback
            traceback.print_exc()
            return []
    
    def sync_users_to_employees(self) -> Dict:
        """مزامنة جميع المستخدمين من أجهزة البصمة وإضافتهم لجدول الموظفين"""
        start_time = time.time()
        self.sync_logs.clear()
        
        self.log_message("INFO", "SYSTEM", "بدء مزامنة المستخدمين إلى جدول الموظفين")
        
        # جلب الأجهزة النشطة
        devices = self.get_active_devices()
        
        if not devices:
            self.log_message("WARNING", "SYSTEM", "لا توجد أجهزة نشطة للمزامنة")
            return {
                'success': False,
                'message': 'لا توجد أجهزة نشطة للمزامنة',
                'total_devices': 0,
                'users_processed': 0,
                'employees_added': 0,
                'employees_skipped': 0,
                'logs': self.sync_logs
            }
        
        total_users_processed = 0
        total_employees_added = 0
        total_employees_skipped = 0
        successful_devices = 0
        
        # الحصول على الموظفين الحاليين لمنع التكرار
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # التحقق من وجود جدول الموظفين وإنشاؤه إذا لم يكن موجود
        try:
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='employees'")
            if not cursor.fetchone():
                self.log_message("INFO", "SYSTEM", "جدول الموظفين غير موجود - سيتم إنشاؤه")
                self.create_employees_table(cursor)
                existing_employee_numbers = set()
            else:
                # جلب أرقام الموظفين الموجودة
                cursor.execute('SELECT employee_number FROM employees')
                existing_employee_numbers = {row[0] for row in cursor.fetchall()}
        except Exception as e:
            self.log_message("ERROR", "SYSTEM", "خطأ في فحص جدول الموظفين", str(e))
            # إنشاء الجدول في حالة وجود خطأ
            self.create_employees_table(cursor)
            existing_employee_numbers = set()
        
        # قائمة لتتبع المستخدمين من جميع الأجهزة لمنع التكرار
        all_users_from_devices = {}  # user_id -> {name, devices, privilege}
        
        # المرحلة الأولى: جمع جميع المستخدمين من كل الأجهزة
        self.log_message("INFO", "SYSTEM", f"بدء جمع المستخدمين من {len(devices)} جهاز")
        
        for device in devices:
            device_ip = device['device_ip']
            device_id = device['id']
            device_port = device.get('device_port', 4370)
            
            try:
                self.log_message("INFO", device_ip, "بدء جمع المستخدمين من الجهاز")
                
                users = []
                
                # ADMS Logic
                if device.get('is_adms'):
                    self.log_message("INFO", device_ip, "جهاز ADMS: قراءة البيانات المحلية وإرسال أمر تحديث")
                    # Queue Command
                    try:
                        import json
                        cmd_conn = self.get_db_connection()
                        employees = cmd_conn.execute('SELECT employee_number FROM employees WHERE is_active = 1').fetchall()
                        if employees:
                            for emp in employees:
                                pin = str(emp['employee_number'])
                                payload = json.dumps({"PIN": pin})
                                cmd_conn.execute("INSERT INTO adms_commands (device_id, command_type, payload, status) VALUES (?, 'DATA QUERY USERINFO', ?, 'PENDING')", (device_id, payload))
                                cmd_conn.execute("INSERT INTO adms_commands (device_id, command_type, payload, status) VALUES (?, 'DATA QUERY FINGERTMP', ?, 'PENDING')", (device_id, payload))
                        else:
                            cmd_conn.execute("INSERT INTO adms_commands (device_id, command_type, payload, status) VALUES (?, 'DATA QUERY USERINFO', '{}', 'PENDING')", (device_id,))
                            cmd_conn.execute("INSERT INTO adms_commands (device_id, command_type, payload, status) VALUES (?, 'DATA QUERY FINGERTMP', '{}', 'PENDING')", (device_id,))
                        
                        cmd_conn.commit()
                        cmd_pass # conn.close() removed to prevent leak in Flask g
                    except Exception as e:
                        self.log_message("ERROR", device_ip, "فشل إرسال أوامر ADMS", str(e))
                        
                    # Load Local Users
                    local_conn = self.get_db_connection()
                    local_rows = local_conn.execute('SELECT * FROM fingerprint_users WHERE device_id = ?', (device_id,)).fetchall()
                    local_pass # conn.close() removed to prevent leak in Flask g
                    
                    class MockUser:
                        def __init__(self, uid, name, priv, pwd, grp, card):
                            self.user_id = uid
                            self.name = name
                            self.privilege = priv
                            self.password = pwd
                            self.group_id = grp
                            self.card = card
                            
                    for row in local_rows:
                        users.append(MockUser(
                            row['user_id'], row['name'], row['privilege'], 
                            row['password'], row['group_id'], row['card_number']
                        ))
                        
                else:
                    # Normal ZK Logic
                    zk = ZK(device_ip, port=device_port, timeout=30)
                    conn_device = zk.connect()
                    conn_device.disable_device()
                    
                    # جلب المستخدمين
                    users = conn_device.get_users()
                    
                    conn_device.enable_device()
                    conn_device.disconnect()
                
                if not users:
                    self.log_message("INFO", device_ip, "لا توجد مستخدمين (تم الجلب)")
                    continue
                
                self.log_message("INFO", device_ip, f"تم جلب {len(users)} مستخدم")
                
                # إضافة المستخدمين للقائمة المجمعة
                for user in users:
                    user_id = str(user.user_id)
                    user_name = user.name or f"مستخدم_{user_id}"
                    
                    if user_id not in all_users_from_devices:
                        all_users_from_devices[user_id] = {
                            'name': user_name,
                            'devices': [device_ip],
                            'privilege': user.privilege,
                            'password': user.password,
                            'group_id': user.group_id,
                            'card': user.card,
                            'device_ids': [device_id]
                        }
                    else:
                        # إضافة الجهاز للقائمة إذا لم يكن موجود
                        if device_ip not in all_users_from_devices[user_id]['devices']:
                            all_users_from_devices[user_id]['devices'].append(device_ip)
                            all_users_from_devices[user_id]['device_ids'].append(device_id)
                
                successful_devices += 1
                
            except Exception as e:
                self.log_message("ERROR", device_ip, "خطأ في جمع المستخدمين من الجهاز", str(e))
        
        # المرحلة الثانية: معالجة المستخدمين المجمعين وإضافتهم للموظفين
        self.log_message("INFO", "SYSTEM", f"بدء معالجة {len(all_users_from_devices)} مستخدم مجمع من {successful_devices} جهاز")
        
        for user_id, user_info in all_users_from_devices.items():
            total_users_processed += 1
            user_name = user_info['name']
            user_devices = user_info['devices']
            device_ids = user_info['device_ids']
            
            # التحقق من عدم وجود الموظف مسبقاً
            if user_id in existing_employee_numbers:
                total_employees_skipped += 1
                devices_str = ', '.join(user_devices)
                self.log_message("INFO", "SYSTEM", f"الموظف {user_name} ({user_id}) موجود مسبقاً - موجود في الأجهزة: {devices_str}")
                
                # تحديث سجلات fingerprint_users للأجهزة الجديدة
                for device_id in device_ids:
                    try:
                        cursor.execute('''
                            INSERT OR IGNORE INTO fingerprint_users (
                                user_id, device_id, name, privilege, password, group_id, card_number, created_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                        ''', (int(user_id), device_id, user_name, user_info['privilege'], user_info.get('password'), user_info.get('group_id'), user_info.get('card')))
                    except Exception as e:
                        self.log_message("ERROR", "SYSTEM", f"خطأ في تحديث fingerprint_users للموظف {user_name}", str(e))
                
                continue
            
            # إضافة الموظف الجديد
            try:
                cursor.execute('''
                    INSERT INTO employees (
                        employee_number, name, department, position, 
                        hire_date, salary, default_start_time, default_end_time, 
                        shift_type, password, group_id, card_number, privilege, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ''', (
                    user_id,
                    user_name,
                    'غير محدد',  # department
                    'موظف',     # position
                    datetime.now().strftime('%Y-%m-%d'),  # hire_date
                    0.0,        # salary
                    '09:00',    # default_start_time
                    '17:00',    # default_end_time
                    'صباحي',    # shift_type
                    user_info.get('password', ''), # password
                    user_info.get('group_id', ''), # group_id
                    user_info.get('card', 0),      # card_number
                    user_info.get('privilege', 0)  # privilege
                ))
                
                # إضافة السجلات في fingerprint_users لجميع الأجهزة
                for device_id in device_ids:
                    cursor.execute('''
                        INSERT OR IGNORE INTO fingerprint_users (
                            user_id, device_id, name, privilege, password, group_id, card_number, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ''', (int(user_id), device_id, user_name, user_info['privilege'], user_info.get('password'), user_info.get('group_id'), user_info.get('card')))
                
                existing_employee_numbers.add(user_id)
                total_employees_added += 1
                
                devices_str = ', '.join(user_devices)
                self.log_message("INFO", "SYSTEM", f"تم إضافة الموظف {user_name} ({user_id}) - موجود في الأجهزة: {devices_str}")
                
            except Exception as e:
                self.log_message("ERROR", "SYSTEM", f"خطأ في إضافة الموظف {user_name} ({user_id})", str(e))
        
        # حفظ جميع التغييرات في قاعدة البيانات
        conn.commit()
        pass # conn.close() removed to prevent leak in Flask g
        duration = time.time() - start_time
        
        # النتيجة النهائية
        result = {
            'success': total_employees_added > 0 or successful_devices > 0,
            'message': f'تم معالجة {total_users_processed} مستخدم من {successful_devices} جهاز',
            'total_devices': len(devices),
            'successful_devices': successful_devices,
            'users_processed': total_users_processed,
            'employees_added': total_employees_added,
            'employees_skipped': total_employees_skipped,
            'duration': round(duration, 2),
            'logs': self.sync_logs
        }
        
        self.log_message("INFO", "SYSTEM", 
                        f"انتهت مزامنة المستخدمين: {total_employees_added} موظف جديد، "
                        f"{total_employees_skipped} موجود مسبقاً من {len(devices)} جهاز في {duration:.2f} ثانية")
        
        return result
    
    def cleanup_existing_constraints(self):
        """تنظيف القيود الفريدة الموجودة في جدول attendance_records"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # التحقق من وجود جدول attendance_records
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='attendance_records'")
            if cursor.fetchone():
                # فحص وجود قيود فريدة في الجدول
                cursor.execute("PRAGMA table_info(attendance_records)")
                table_info = cursor.fetchall()
                
                # البحث عن القيود الفريدة
                cursor.execute("""
                    SELECT sql FROM sqlite_master 
                    WHERE type='table' AND name='attendance_records'
                """)
                table_sql = cursor.fetchone()
                
                if table_sql and 'UNIQUE' in table_sql[0]:
                    self.log_message("WARNING", "SYSTEM", "تم العثور على قيود فريدة في جدول attendance_records")
                    
                    # إنشاء جدول جديد بدون قيود فريدة
                    cursor.execute('''
                        CREATE TABLE IF NOT EXISTS attendance_records_new (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            employee_id INTEGER NOT NULL,
                            device_id INTEGER NOT NULL,
                            check_time DATETIME NOT NULL,
                            check_type INTEGER NOT NULL,
                            verify_code INTEGER DEFAULT 0,
                            sensor_id INTEGER DEFAULT 1,
                            work_code INTEGER DEFAULT 0,
                            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                            FOREIGN KEY (employee_id) REFERENCES employees (id),
                            FOREIGN KEY (device_id) REFERENCES fingerprint_devices (id)
                        )
                    ''')
                    
                    # نسخ البيانات للجدول الجديد
                    cursor.execute('''
                        INSERT INTO attendance_records_new 
                        SELECT * FROM attendance_records
                    ''')
                    
                    # حذف الجدول القديم
                    cursor.execute('DROP TABLE attendance_records')
                    
                    # إعادة تسمية الجدول الجديد
                    cursor.execute('ALTER TABLE attendance_records_new RENAME TO attendance_records')
                    
                    self.log_message("INFO", "SYSTEM", "تم إزالة القيود الفريدة من جدول attendance_records")
                
                conn.commit()
            
            pass # conn.close() removed to prevent leak in Flask g
            
        except Exception as e:
            self.log_message("ERROR", "SYSTEM", "خطأ في تنظيف القيود الفريدة", str(e))
    
    def optimize_attendance_indexes(self):
        """تحسين فهارس جدول الحضور لمنع التكرار وتحسين الأداء"""
        try:
            # أولاً تنظيف أي قيود فريدة موجودة
            self.cleanup_existing_constraints()
            
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # التحقق من وجود جدول attendance_records
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='attendance_records'")
            if cursor.fetchone():
                # إزالة أي فهارس قديمة
                try:
                    cursor.execute('DROP INDEX IF EXISTS idx_attendance_unique')
                    cursor.execute('DROP INDEX IF EXISTS idx_attendance_employee_date')
                    cursor.execute('DROP INDEX IF EXISTS idx_attendance_device_date')
                    cursor.execute('DROP INDEX IF EXISTS idx_attendance_employee_time')
                    cursor.execute('DROP INDEX IF EXISTS idx_attendance_device_time')
                    cursor.execute('DROP INDEX IF EXISTS idx_attendance_time_type')
                    cursor.execute('DROP INDEX IF EXISTS idx_attendance_duplicate_check')
                    self.log_message("INFO", "SYSTEM", "تم إزالة الفهارس القديمة")
                except Exception:
                    pass
                
                # إنشاء فهرس محسن لفحص التكرار بسرعة
                cursor.execute('''
                    CREATE INDEX IF NOT EXISTS idx_attendance_duplicate_check 
                    ON attendance_records (employee_id, device_id, check_time, check_type)
                ''')
                
                # إنشاء فهارس للأداء العام
                cursor.execute('''
                    CREATE INDEX IF NOT EXISTS idx_attendance_employee_time 
                    ON attendance_records (employee_id, check_time)
                ''')
                
                cursor.execute('''
                    CREATE INDEX IF NOT EXISTS idx_attendance_device_time 
                    ON attendance_records (device_id, check_time)
                ''')
                
                cursor.execute('''
                    CREATE INDEX IF NOT EXISTS idx_attendance_time_type 
                    ON attendance_records (check_time, check_type)
                ''')
                
                conn.commit()
                self.log_message("INFO", "SYSTEM", "تم إنشاء فهارس محسنة لمنع التكرار وتحسين الأداء")
            
            pass # conn.close() removed to prevent leak in Flask g
            
        except Exception as e:
            self.log_message("ERROR", "SYSTEM", "خطأ في إنشاء فهارس الحضور", str(e))
    
    def remove_unique_attendance_indexes(self):
        """إزالة الفهارس الفريدة التي قد تمنع دخول وخروج في نفس الوقت"""
        self.optimize_attendance_indexes()
    
    def ensure_attendance_indexes(self):
        """التأكد من وجود فهارس الأداء فقط (بدون منع التكرار)"""
        self.remove_unique_attendance_indexes()
    
    def create_employees_table(self, cursor):
        """إنشاء جدول الموظفين إذا لم يكن موجود"""
        try:
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS employees (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    employee_number TEXT UNIQUE NOT NULL,
                    name TEXT NOT NULL,
                    department TEXT NOT NULL,
                    position TEXT NOT NULL,
                    hire_date DATE NOT NULL,
                    salary REAL NOT NULL,
                    phone TEXT,
                    email TEXT,
                    address TEXT,
                    default_start_time TEXT,
                    default_end_time TEXT,
                    shift_type TEXT DEFAULT "صباحي",
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # إنشاء جدول أنواع الشفتات إذا لم يكن موجود
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS shift_types (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    start_time TEXT NOT NULL,
                    end_time TEXT NOT NULL,
                    hours_per_day REAL DEFAULT 8.0,
                    description TEXT,
                    is_active BOOLEAN DEFAULT 1,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # إدخال أنواع الشفتات الافتراضية
            cursor.execute('''
                INSERT OR IGNORE INTO shift_types (name, start_time, end_time, hours_per_day, description) VALUES 
                ('صباحي', '08:00', '16:00', 8.0, 'شفت صباحي من 8 صباحاً إلى 4 عصراً'),
                ('مسائي', '16:00', '00:00', 8.0, 'شفت مسائي من 4 عصراً إلى 12 منتصف الليل'),
                ('ليلي', '00:00', '08:00', 8.0, 'شفت ليلي من 12 منتصف الليل إلى 8 صباحاً'),
                ('مرن', '09:00', '17:00', 8.0, 'شفت مرن حسب طبيعة العمل')
            ''')
            
            # إنشاء جدول أنواع الإجازات إذا لم يكن موجود
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS leave_types (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    max_days INTEGER NOT NULL,
                    is_paid BOOLEAN DEFAULT 1,
                    description TEXT
                )
            ''')
            
            # إدخال أنواع الإجازات الافتراضية
            cursor.execute('''
                INSERT OR IGNORE INTO leave_types (name, max_days, is_paid, description) VALUES 
                ('إجازة سنوية', 30, 1, 'الإجازة السنوية المدفوعة'),
                ('إجازة مرضية', 15, 1, 'إجازة مرضية بشهادة طبية'),
                ('إجازة طارئة', 5, 0, 'إجازة طارئة غير مدفوعة'),
                ('إجازة أمومة', 90, 1, 'إجازة الوضع للموظفات'),
                ('إجازة وفاة', 3, 1, 'إجازة وفاة أحد الأقارب')
            ''')
            
            # إنشاء جدول طلبات الإجازات إذا لم يكن موجود
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS leave_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    employee_id INTEGER NOT NULL,
                    leave_type_id INTEGER NOT NULL,
                    start_date DATE NOT NULL,
                    end_date DATE NOT NULL,
                    days_count INTEGER DEFAULT 1,
                    reason TEXT,
                    status TEXT DEFAULT 'pending',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (employee_id) REFERENCES employees (id),
                    FOREIGN KEY (leave_type_id) REFERENCES leave_types (id)
                )
            ''')
            
            # إنشاء جدول الرواتب إذا لم يكن موجود
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS salaries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    employee_id INTEGER NOT NULL,
                    month TEXT NOT NULL,
                    year INTEGER NOT NULL,
                    basic_salary REAL NOT NULL,
                    allowances REAL DEFAULT 0,
                    deductions REAL DEFAULT 0,
                    total_salary REAL NOT NULL,
                    FOREIGN KEY (employee_id) REFERENCES employees (id)
                )
            ''')
            
            # إنشاء جدول المستندات إذا لم يكن موجود
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    employee_id INTEGER NOT NULL,
                    document_type TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    original_filename TEXT NOT NULL,
                    upload_date DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (employee_id) REFERENCES employees (id)
                )
            ''')
            
            # إنشاء فهارس لتحسين الأداء (بدون منع التكرار)
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_attendance_employee_date 
                ON attendance_records (employee_id, check_time)
            ''')
            
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_attendance_device_date 
                ON attendance_records (device_id, check_time)
            ''')
            
            self.log_message("INFO", "SYSTEM", "تم إنشاء جدول الموظفين والجداول المرتبطة مع الفهارس بنجاح")
            
        except Exception as e:
            self.log_message("ERROR", "SYSTEM", "خطأ في إنشاء جدول الموظفين", str(e))
            raise e

    def test_duplicate_prevention(self) -> Dict:
        """اختبار فعالية منع التكرار في جدول attendance_records"""
        try:
            conn = self.get_db_connection()
            
            # فحص وجود أي سجلات مكررة في قاعدة البيانات
            duplicates_query = '''
                SELECT employee_id, device_id, check_time, check_type, COUNT(*) as count
                FROM attendance_records
                GROUP BY employee_id, device_id, check_time, check_type
                HAVING COUNT(*) > 1
                ORDER BY count DESC
            '''
            
            duplicates = conn.execute(duplicates_query).fetchall()
            
            # فحص الفهارس المحسنة
            indexes_query = '''
                SELECT name, sql FROM sqlite_master 
                WHERE type='index' AND tbl_name='attendance_records'
                ORDER BY name
            '''
            
            indexes = conn.execute(indexes_query).fetchall()
            
            # فحص بنية الجدول للتأكد من عدم وجود قيود فريدة
            table_structure = '''
                SELECT sql FROM sqlite_master 
                WHERE type='table' AND name='attendance_records'
            '''
            
            table_sql = conn.execute(table_structure).fetchone()
            
            # إحصائيات عامة
            total_records = conn.execute('SELECT COUNT(*) FROM attendance_records').fetchone()[0]
            
            pass # conn.close() removed to prevent leak in Flask g
            
            # تحليل النتائج
            has_unique_constraint = table_sql and 'UNIQUE' in table_sql[0] if table_sql else False
            has_duplicate_check_index = any('duplicate_check' in index[0] for index in indexes)
            duplicate_count = len(duplicates)
            
            result = {
                'success': True,
                'duplicate_prevention_active': not has_unique_constraint and has_duplicate_check_index,
                'has_unique_constraint': has_unique_constraint,
                'has_duplicate_check_index': has_duplicate_check_index,
                'total_records': total_records,
                'duplicate_count': duplicate_count,
                'duplicates': [dict(d) for d in duplicates],
                'indexes': [dict(i) for i in indexes],
                'table_structure': table_sql[0] if table_sql else None
            }
            
            # تسجيل النتائج
            if duplicate_count > 0:
                self.log_message("WARNING", "SYSTEM", f"تم العثور على {duplicate_count} مجموعة سجلات مكررة")
                for dup in duplicates:
                    self.log_message("WARNING", "SYSTEM", 
                                   f"سجل مكرر: موظف {dup['employee_id']}, جهاز {dup['device_id']}, "
                                   f"وقت {dup['check_time']}, نوع {dup['check_type']}, عدد {dup['count']}")
            else:
                self.log_message("INFO", "SYSTEM", "لا توجد سجلات مكررة في قاعدة البيانات")
            
            if has_unique_constraint:
                self.log_message("WARNING", "SYSTEM", "يوجد قيد فريد في جدول attendance_records")
            else:
                self.log_message("INFO", "SYSTEM", "لا يوجد قيد فريد في جدول attendance_records")
            
            if has_duplicate_check_index:
                self.log_message("INFO", "SYSTEM", "فهرس فحص التكرار نشط")
            else:
                self.log_message("WARNING", "SYSTEM", "فهرس فحص التكرار غير موجود")
            
            return result
            
        except Exception as e:
            error_msg = f"خطأ في اختبار منع التكرار: {str(e)}"
            self.log_message("ERROR", "SYSTEM", error_msg)
            return {
                'success': False,
                'message': error_msg
            }
    
    def test_device_connection(self, device_info: Dict) -> Dict:
        """اختبار الاتصال بجهاز محدد"""
        device_ip = device_info['device_ip']
        
        conn_dev, error = self.connect_to_device(device_info, timeout=10)
        
        if error:
            return {
                'success': False,
                'message': error,
                'device_info': None
            }
        
        try:
            # جلب معلومات الجهاز
            info = {
                'serial_number': getattr(conn_dev, 'get_serialnumber', lambda: 'غير متوفر')(),
                'platform': getattr(conn_dev, 'get_platform', lambda: 'غير متوفر')(),
                'firmware_version': getattr(conn_dev, 'get_firmware_version', lambda: 'غير متوفر')(),
                'device_name': getattr(conn_dev, 'get_device_name', lambda: device_info.get('device_name', 'غير محدد'))(),
                'users_count': len(getattr(conn_dev, 'get_users', lambda: [])()),
                'attendance_count': len(getattr(conn_dev, 'get_attendance', lambda: [])())
            }
            
            self.log_message("INFO", device_ip, "تم اختبار الاتصال بنجاح")
            
            return {
                'success': True,
                'message': 'تم الاتصال بنجاح',
                'device_info': info
            }
            
        except Exception as e:
            error_msg = f"خطأ في جلب معلومات الجهاز: {str(e)}"
            self.log_message("ERROR", device_ip, error_msg)
            return {
                'success': False,
                'message': error_msg,
                'device_info': None
            }
        finally:
            try:
                if conn_dev:
                    conn_dev.enable_device()
                    conn_dev.disconnect()
            except:
                pass

# إنشاء مثيل عام
fingerprint_manager = FingerprintSyncManager()

def sync_all_fingerprint_devices():
    """دالة مساعدة للمزامنة الكاملة"""
    return fingerprint_manager.sync_all_devices()

def sync_users_to_employees():
    """دالة مساعدة لمزامنة المستخدمين إلى جدول الموظفين"""
    return fingerprint_manager.sync_users_to_employees()

def test_fingerprint_device(device_info):
    """دالة مساعدة لاختبار جهاز محدد"""
    return fingerprint_manager.test_device_connection(device_info)

def get_sync_logs(limit=100):
    """دالة مساعدة لجلب السجلات"""
    try:
        if fingerprint_manager is None:
            return []
        return fingerprint_manager.get_sync_logs(limit)
    except Exception as e:
        print(f"خطأ في get_sync_logs: {e}")
        import traceback
        traceback.print_exc()
        return []

def test_duplicate_prevention():
    """دالة مساعدة لاختبار منع التكرار"""
    return fingerprint_manager.test_duplicate_prevention()
