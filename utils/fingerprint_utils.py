import os
import io
import json
from utils.db import get_db_connection
from zk import ZK, const

def get_sync_logs(limit=100, offset=0, device_ip=None, level=None, date_filter=None):
    """
    قراءة سجلات المزامنة مع دعم الفلترة والترقيم
    """
    log_file = 'fingerprint_sync.log'
    if not os.path.exists(log_file):
        return []
    
    logs = []
    try:
        with open(log_file, 'r', encoding='utf-8') as f:
            all_lines = f.readlines()
            
            # 1. Parsing and Filtering
            for line in reversed(all_lines): # Read latest first
                log_entry = {}
                if ' - ' in line:
                    parts = line.split(' - ', 2)
                    if len(parts) >= 3:
                        log_entry = {
                            'timestamp': parts[0],
                            'level': parts[1],
                            'message': parts[2].strip()
                        }
                        
                        # Extract device IP from message if possible (format: IP: message)
                        if ': ' in log_entry['message']:
                            msg_parts = log_entry['message'].split(': ', 1)
                            # Simple check if first part looks like IP or is SYSTEM
                            potential_ip = msg_parts[0]
                            if potential_ip == 'SYSTEM' or potential_ip.count('.') == 3:
                                log_entry['device_ip'] = potential_ip
                                log_entry['message'] = msg_parts[1]
                                log_entry['details'] = '' # details usually not in main line unless parsed further
                            else:
                                log_entry['device_ip'] = 'UNKNOWN'
                        else:
                             log_entry['device_ip'] = 'SYSTEM'

                    else:
                        log_entry = {'message': line.strip(), 'level': 'INFO', 'timestamp': '', 'device_ip': 'UNKNOWN'}
                else:
                     log_entry = {'message': line.strip(), 'level': 'INFO', 'timestamp': '', 'device_ip': 'UNKNOWN'}
                
                # Filter check
                if device_ip and log_entry.get('device_ip') != device_ip:
                    continue
                if level and log_entry.get('level') != level:
                    continue
                if date_filter and date_filter not in log_entry.get('timestamp', ''):
                    continue
                    
                logs.append(log_entry)
            
            # 2. Pagination
            return logs[offset : offset + limit]
            
    except Exception as e:
        return [{'level': 'ERROR', 'message': f'خطأ في قراءة السجلات: {str(e)}', 'timestamp': '', 'device_ip': 'SYSTEM'}]

def test_fingerprint_device(device_id):
    """اختبار الاتصال بجهاز البصمة"""
    conn = get_db_connection()
    device = conn.execute('SELECT * FROM fingerprint_devices WHERE id = ?', (device_id,)).fetchone()
    pass # conn.close() removed to prevent leak in Flask g
    
    if not device:
        return False, "الجهاز غير موجود"

    # ADMS Check
    # sqlite3.Row access
    is_adms = False
    if 'is_adms' in device.keys():
         is_adms = device['is_adms']
         
    if is_adms:
        last_sync = device['last_sync_time'] if 'last_sync_time' in device.keys() else None
        
        if not last_sync:
            return False, "لم يتم الاتصال بالجهاز بعد (No Heartbeat)"
        
        device_info = {
            'serial_number': device['device_name'] if 'device_name' in device.keys() else 'Unknown',
            'firmware_version': 'ADMS Push',
            'platform': 'ZKTeco Push',
            'device_name': device['device_name'] if 'device_name' in device.keys() else 'Unknown',
            'users_count': 'N/A', 
            'attendance_count': 'See Database',
            'last_heartbeat': last_sync
        }
        return True, device_info
        
    try:
        ip_address = device['device_ip']
        port = int(device['device_port'])
        
        zk = ZK(ip_address, port=port, timeout=5)
        conn = zk.connect()
        # محاولة قراءة المعلومات الأساسية
        try:
            firmware = conn.get_firmware_version()
        except:
            firmware = 'Unknown'
            
        try:
            serial = conn.get_serialnumber()
        except:
            serial = 'Unknown'
            
        try:
            platform = conn.get_platform()
        except:
            platform = 'ZKTeco'
            
        try:
            device_name = conn.get_device_name()
        except:
            device_name = device['device_name']
            
        # Get users and attendance counts if possible
        try:
            users_count = conn.get_users()
            users_count = len(users_count) if users_count else 0
        except:
            users_count = 0
            
        try:
            attendance_count = conn.get_attendance()
            attendance_count = len(attendance_count) if attendance_count else 0
        except:
            attendance_count = 0
        
        conn.disconnect()
        
        # Update last_activity in the database
        try:
            import datetime
            db_conn = get_db_connection()
            db_conn.execute('UPDATE fingerprint_devices SET last_activity = ? WHERE id = ?',
                            (datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), device_id))
            db_conn.commit()
            pass # conn.close() removed to prevent leak in Flask g
        except Exception as e:
            print(f"Error updating last_activity: {e}")
        
        device_info = {
            'serial_number': serial,
            'firmware_version': firmware,
            'platform': platform,
            'device_name': device_name,
            'users_count': users_count,
            'attendance_count': attendance_count
        }
        
        return True, device_info
    except Exception as e:
        return False, f"فشل الاتصال: {str(e)}"

def test_duplicate_prevention(device_id=None):
    """اختبار آلية منع التكرار"""
    conn = get_db_connection()
    try:
        # 1. إدخال سجل تجريبي
        import datetime
        test_time = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        test_emp_id = 1 # افتراض وجود موظف رقم 1
        
        # التأكد من وجود موظف
        emp = conn.execute('SELECT id FROM employees LIMIT 1').fetchone()
        if not emp:
            return {'success': False, 'message': 'لا يوجد موظفين للاختبار'}
            
        test_emp_id = emp['id']
        
        # محاولة إدخال السجل مرتين
        # المرة الأولى
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR IGNORE INTO attendance_records (employee_id, check_time, check_type, source)
            VALUES (?, ?, ?, ?)
        ''', (test_emp_id, test_time, 'check_in', 'test'))
        first_insert = cursor.rowcount
        
        # المرة الثانية (يجب أن يتم تجاهلها)
        cursor.execute('''
            INSERT OR IGNORE INTO attendance_records (employee_id, check_time, check_type, source)
            VALUES (?, ?, ?, ?)
        ''', (test_emp_id, test_time, 'check_in', 'test'))
        second_insert = cursor.rowcount
        
        conn.commit()
        
        # تنظيف البيانات التجريبية
        conn.execute('DELETE FROM attendance_records WHERE source = ?', ('test',))
        conn.commit()
        
        return {
            'success': True,
            'message': f'تم الاختبار: الإدخال الأول={first_insert}, الإدخال الثاني={second_insert} (يجب أن يكون 0)',
            'duplicate_prevented': second_insert == 0
        }
        
    except Exception as e:
        return {'success': False, 'message': f'خطأ في الاختبار: {e}'}
    finally:
        pass # conn.close() removed to prevent leak in Flask g

def upload_user_to_device(device_id, user_data):
    """
    رفع مستخدم إلى جهاز البصمة
    user_data expects: {'uid': int, 'user_id': str, 'name': str, 'card': int, 'privilege': int, 'password': str}
    """
    conn = get_db_connection()
    device = conn.execute('SELECT * FROM fingerprint_devices WHERE id = ?', (device_id,)).fetchone()
    pass # conn.close() removed to prevent leak in Flask g
    
    if not device:
        return {'success': False, 'message': 'الجهاز غير موجود'}
        
    try:
        ip_address = device['device_ip']
        port = int(device['device_port'])
        
        zk = ZK(ip_address, port=port, timeout=10)
        conn = zk.connect()
        
        # Preparing data
        uid = int(user_data.get('uid', 0))
        user_id = str(user_data.get('user_id', ''))
        name = user_data.get('name', '')
        privilege = int(user_data.get('privilege', 0)) # 0: User, 14: Admin
        password = user_data.get('password', '')
        card = int(user_data.get('card', 0))
        group_id = str(user_data.get('group_id', ''))
        
        # Set User
        try:
            conn.set_user(uid=uid, name=name, privilege=privilege, password=password, group_id=group_id, user_id=user_id, card=card)
            result = {'success': True, 'message': f'تم رفع المستخدم {name} ({user_id}) بنجاح'}
        except Exception as e:
            result = {'success': False, 'message': f'فشل رفع المستخدم {name}: {str(e)}'}
            
        conn.disconnect()
        return result
        
    except Exception as e:
        return {'success': False, 'message': f'خطأ في الاتصال بالجهاز: {str(e)}'}

def queue_adms_user_update(employee_data):
    """
    Queue ADD_USER command for all active ADMS devices.
    Also syncs fingerprints and face templates if available.
    """
    import json
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        devices = cursor.execute('SELECT id FROM fingerprint_devices WHERE is_active = 1 AND is_adms = 1').fetchall()
        
        if not devices:
            return 0

        pin = str(employee_data.get('employee_number', ''))
        name = str(employee_data.get('name', ''))
        
        # 1. Prepare User Info Payload
        user_payload = {
            'PIN': pin,
            'Name': name,
            'Pri': int(employee_data.get('privilege', 0)),
            'Passwd': str(employee_data.get('password', '') or ''),
            'Card': str(employee_data.get('card_number', '') or '0'),
            'Grp': str(employee_data.get('group_id', '1') or '1')
        }
        user_json = json.dumps(user_payload)
        
        # 2. Get Biometric Templates
        # Fingerprints
        try:
            fingerprints = conn.execute('SELECT finger_id, size, template FROM user_fingerprints WHERE user_id = ?', (pin,)).fetchall()
        except:
            fingerprints = []
            
        # Faces
        try:
            faces = conn.execute('SELECT format, size, template FROM user_faces WHERE user_id = ?', (pin,)).fetchall()
        except:
            faces = []
        
        count = 0
        for device in devices:
            device_id = device['id']
            # Queue User Info — بلا تكرار.
            # كان كل حفظٍ للموظف يضيف أمرًا جديدًا ولو كان أمرٌ بالمحتوى
            # نفسه ما زال معلّقًا، فيتراكم الطابور ويُرسَل الأمر مرارًا.
            # وبروتوكول ZKTeco يخصّص للأمر المكرّر رمز خطأ (-7).
            dup = cursor.execute('''
                SELECT id FROM adms_commands
                WHERE device_id = ? AND command_type = ?
                  AND payload = ? AND status = 'PENDING'
            ''', (device_id, 'DATA UPDATE USERINFO', user_json)).fetchone()
            if not dup:
                cursor.execute('''
                    INSERT INTO adms_commands (device_id, command_type, payload, status)
                    VALUES (?, ?, ?, 'PENDING')
                ''', (device_id, 'DATA UPDATE USERINFO', user_json))
            count += 1
            
            # Queue Fingerprints
            for fp in fingerprints:
                fp_payload = {
                    'PIN': pin,
                    'FingerID': fp['finger_id'],
                    'Size': fp['size'],
                    'TMP': fp['template']
                }
                cursor.execute('''
                    INSERT INTO adms_commands (device_id, command_type, payload, status)
                    VALUES (?, ?, ?, 'PENDING')
                ''', (device_id, 'DATA UPDATE FINGERTMP', json.dumps(fp_payload)))
            
            # Queue Faces
            for face in faces:
                face_payload = {
                    'PIN': pin,
                    'Format': face['format'],
                    'Size': face['size'],
                    'TMP': face['template']
                }
                cursor.execute('''
                    INSERT INTO adms_commands (device_id, command_type, payload, status)
                    VALUES (?, ?, ?, 'PENDING')
                ''', (device_id, 'DATA UPDATE FACE', json.dumps(face_payload)))
            
        conn.commit()
        return count
    except Exception as e:
        print(f"Error queuing ADMS sync: {e}")
        return 0
    finally:
        pass # conn.close() removed to prevent leak in Flask g

def queue_adms_user_delete(employee_number):
    """
    Queue DELETE USER command for all active ADMS devices.
    """
    import json
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        devices = cursor.execute('SELECT id FROM fingerprint_devices WHERE is_active = 1 AND is_adms = 1').fetchall()
        
        if not devices:
            return 0

        payload = {'PIN': str(employee_number)}
        payload_json = json.dumps(payload)
        
        count = 0
        for device in devices:
            cursor.execute('''
                INSERT INTO adms_commands (device_id, command_type, payload, status)
                VALUES (?, ?, ?, 'PENDING')
            ''', (device['id'], 'DATA DELETE USERINFO', payload_json))
            count += 1
            
        conn.commit()
        return count
    except Exception as e:
        print(f"Error queuing ADMS delete: {e}")
        return 0
    finally:
        pass # conn.close() removed to prevent leak in Flask g


def queue_adms_set_time(device_id):
    """
    Queue SET TIME command for a specific ADMS device with current server time.
    """
    import datetime
    conn = get_db_connection()
    try:
        now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        # ZKTeco ADMS expects time in YYYY-MM-DD HH:MM:SS format
        payload = now 
        
        conn.execute('''
            INSERT INTO adms_commands (device_id, command_type, payload, status)
            VALUES (?, ?, ?, 'PENDING')
        ''', (device_id, 'SET TIME', json.dumps(payload)))
        conn.commit()
        return True
    except Exception as e:
        print(f"Error queuing ADMS set time: {e}")
        return False
    finally:
        pass # conn.close() removed to prevent leak in Flask g

def reboot_fingerprint_device(device_id):
    """
    إعادة تشغيل الجهاز (ADMS أو Standalone)
    """
    conn = get_db_connection()
    device = conn.execute('SELECT * FROM fingerprint_devices WHERE id = ?', (device_id,)).fetchone()
    pass # conn.close() removed to prevent leak in Flask g
    
    if not device:
        return False, "الجهاز غير موجود"

    is_adms = device['is_adms'] if 'is_adms' in device.keys() else False
    
    if is_adms:
        conn = get_db_connection()
        try:
            conn.execute('''
                INSERT INTO adms_commands (device_id, command_type, payload, status)
                VALUES (?, 'REBOOT', '{}', 'PENDING')
            ''', (device_id,))
            conn.commit()
            return True, "تمت جدولة إعادة تشغيل الجهاز. سيتم التنفيذ عند الاتصال القادم."
        except Exception as e:
            return False, f"خطأ في الجدولة: {str(e)}"
        finally:
            pass # conn.close() removed to prevent leak in Flask g
    else:
        # Standalone reboot
        try:
            from zk import ZK
            zk = ZK(device['device_ip'], port=int(device['device_port']), timeout=10)
            conn_dev = zk.connect()
            conn_dev.restart()
            conn_dev.disconnect()
            return True, "تم إعادة تشغيل الجهاز بنجاح."
        except Exception as e:
            return False, f"فشل إعادة التشغيل: {str(e)}"

def factory_reset_fingerprint_device(device_id):
    """
    ضبط مصنع الجهاز / مسح جميع البيانات (ADMS أو Standalone)
    """
    conn = get_db_connection()
    device = conn.execute('SELECT * FROM fingerprint_devices WHERE id = ?', (device_id,)).fetchone()
    pass # conn.close() removed to prevent leak in Flask g
    
    if not device:
        return False, "الجهاز غير موجود"

    is_adms = device['is_adms'] if 'is_adms' in device.keys() else False
    
    if is_adms:
        conn = get_db_connection()
        try:
            # Send multiple commands to ensure everything is cleared
            commands = [
                'CLEAR DATA USERINFO',
                'CLEAR DATA FINGERTMP',
                'CLEAR DATA FACE',
                'CLEAR DATA PHOTO',
                'CLEAR DATA USERPIC',
                'CLEAR DATA attlog',
                'CLEAR DATA admin',
                'CLEAR DATA manager',
                'CLEAR DATA ALL',
                'DELETE ADMIN',
                'REBOOT'
            ]
            for cmd in commands:
                conn.execute('''
                    INSERT INTO adms_commands (device_id, command_type, payload, status)
                    VALUES (?, ?, '{}', 'PENDING')
                ''', (device_id, cmd))
            
            conn.commit()
            return True, "تمت جدولة مسح الجهاز بالكامل وإعادة التشغيل. سيتم التنفيذ قريباً."
        except Exception as e:
            return False, f"خطأ في الجدولة: {str(e)}"
        finally:
            pass # conn.close() removed to prevent leak in Flask g
    else:
        # Standalone reset (Caution!)
        try:
            from zk import ZK
            zk = ZK(device['device_ip'], port=int(device['device_port']), timeout=10)
            conn_dev = zk.connect()
            conn_dev.clear_data(user=True, template=True, attendance=True)
            conn_dev.disconnect()
            return True, "تم ضبط مصنع الجهاز بنجاح."
        except Exception as e:
            return False, f"فشل ضبط المصنع: {str(e)}"

def clear_fingerprint_device_users(device_id):
    """
    مسح جميع المستخدمين من جهاز البصمة (ADMS أو Standalone)
    """
    conn = get_db_connection()
    device = conn.execute('SELECT * FROM fingerprint_devices WHERE id = ?', (device_id,)).fetchone()
    pass # conn.close() removed to prevent leak in Flask g
    
    if not device:
        return False, "الجهاز غير موجود"

    is_adms = device['is_adms'] if 'is_adms' in device.keys() else False
    
    if is_adms:
        # ADMS: Queue clear commands
        conn = get_db_connection()
        try:
            # We queue multiple clear commands to ensure broad compatibility
            # CLEAR DATA USERINFO is the most common for users
            # CLEAR DATA FINGERTMP for fingerprints
            # CLEAR DATA FACE for faces
            commands = [
                ('DATA CLEAR userinfo', '{}'),
                ('DATA CLEAR fingertmp', '{}'),
                ('DATA CLEAR face', '{}'),
                ('DATA CLEAR photo', '{}'),
                ('DATA CLEAR userpic', '{}'),
                ('CLEAR DATA USERINFO', '{}'), # Fallback uppercase
                ('CLEAR DATA FINGERTMP', '{}'),
                ('CLEAR DATA FACE', '{}')
            ]
            
            for cmd_type, payload in commands:
                conn.execute('''
                    INSERT INTO adms_commands (device_id, command_type, payload, status)
                    VALUES (?, ?, ?, 'PENDING')
                ''', (device_id, cmd_type, payload))
            
            conn.commit()
            return True, "تمت جدولة مسح جميع الموظفين من جهاز ADMS. سيتم التنفيذ عند الاتصال القادم."
        except Exception as e:
            return False, f"خطأ في الجدولة: {str(e)}"
        finally:
            pass # conn.close() removed to prevent leak in Flask g
    else:
        # Standalone: Connect via pyzk and clear
        try:
            ip_address = device['device_ip']
            port = int(device['device_port'])
            
            zk = ZK(ip_address, port=port, timeout=10)
            conn_dev = zk.connect()
            
            # Disable device to prevent interference
            conn_dev.disable_device()
            
            # Clear all users
            conn_dev.clear_data(user=True)
            
            # Re-enable and disconnect
            conn_dev.enable_device()
            conn_dev.disconnect()
            
            return True, "تم مسح جميع الموظفين من الجهاز بنجاح."
        except Exception as e:
            return False, f"فشل في مسح الموظفين: {str(e)}"

def get_device_user_count(device_id):
    """
    جلب عدد الموظفين المسجلين في جهاز البصمة
    """
    conn = get_db_connection()
    device = conn.execute('SELECT * FROM fingerprint_devices WHERE id = ?', (device_id,)).fetchone()
    
    if not device:
        pass # conn.close() removed to prevent leak in Flask g
        return False, "الجهاز غير موجود"

    is_adms = device['is_adms'] if 'is_adms' in device.keys() else False
    
    if is_adms:
        # ADMS: Get count from our synced users table
        count_row = conn.execute('SELECT COUNT(*) as count FROM fingerprint_users WHERE device_id = ?', (device_id,)).fetchone()
        count = count_row['count'] if count_row else 0
        pass # conn.close() removed to prevent leak in Flask g
        return True, {
            'count': count,
            'is_adms': True,
            'message': f"عدد الموظفين المسجلين (آخر مزامنة): {count}"
        }
    else:
        # Standalone: Connect and get current count
        pass # conn.close() removed to prevent leak in Flask g
        try:
            ip_address = device['device_ip']
            port = int(device['device_port'])
            
            zk = ZK(ip_address, port=port, timeout=5)
            conn_dev = zk.connect()
            
            # Get users
            users = conn_dev.get_users()
            count = len(users) if users else 0
            
            conn_dev.disconnect()
            return True, {
                'count': count,
                'is_adms': False,
                'message': f"عدد الموظفين الفعلي في الجهاز: {count}"
            }
        except Exception as e:
            return False, f"فشل في الاتصال بالجهاز: {str(e)}"
