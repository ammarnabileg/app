from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_babel import gettext
import threading
import time
import json
from utils.db import get_db_connection
from utils.db import get_db_connection
from utils.auth import login_required
from utils.rbac import require_permission
from utils.fingerprint_utils import test_fingerprint_device, test_duplicate_prevention, get_sync_logs, upload_user_to_device, clear_fingerprint_device_users, get_device_user_count, reboot_fingerprint_device, factory_reset_fingerprint_device
from fingerprint_sync import sync_all_fingerprint_devices, sync_users_to_employees
from utils.license import get_effective_max_devices
from utils.oracle_db import get_sync_queue_stats

try:
    from zk import ZK, const
    FINGERPRINT_AVAILABLE = True
except ImportError:
    FINGERPRINT_AVAILABLE = False

attendance_bp = Blueprint('attendance', __name__)

@attendance_bp.route('/fingerprint')
@login_required
@require_permission('page.attendance')
def fingerprint():
    """صفحة إدارة نظام البصمة"""
    try:
        if not FINGERPRINT_AVAILABLE:
            flash(gettext('x.f_fp_unavailable_pyzk'), 'error')
            return redirect(url_for('main.index'))
        
        conn = get_db_connection()
        try:
            devices = conn.execute('SELECT * FROM fingerprint_devices ORDER BY device_name').fetchall()
        except Exception as e:
            print(f"خطأ في جلب الأجهزة: {e}")
            devices = []
        finally:
            pass # conn.close() removed to prevent leak in Flask g
        
        try:
            sync_logs = get_sync_logs(50)
        except Exception as e:
            print(f"خطأ في جلب سجلات المزامنة: {e}")
            sync_logs = []
        
        from utils import device_autosync
        return render_template(
            'fingerprint_dashboard.html',
            devices=[dict(d) for d in devices], 
            sync_logs=sync_logs,
            fingerprint_available=FINGERPRINT_AVAILABLE,
            autosync=device_autosync.status()
        )
    except Exception as e:
        print(f"خطأ في route /fingerprint: {e}")
        flash(gettext('x.f_error_occurred') % {'p0': f'{str(e)}'}, 'error')
        return redirect(url_for('main.index'))

@attendance_bp.route('/fingerprint/devices')
@login_required
@require_permission('attendance.devices')
def devices():
    conn = get_db_connection()
    devices = conn.execute('SELECT * FROM fingerprint_devices').fetchall()
    current_active = conn.execute('SELECT COUNT(*) FROM fingerprint_devices WHERE is_active = 1').fetchone()[0]
    
    # Query active branches
    try:
        branches = conn.execute('SELECT * FROM branches WHERE is_active = 1 ORDER BY name ASC').fetchall()
    except Exception:
        branches = []
        
    pass # conn.close() removed to prevent leak in Flask g
    
    try:
        max_devices = get_effective_max_devices()
    except:
        max_devices = 3
        
    # محرّك الأجهزة الموحّد: الماركة وقدراتُها لكلّ جهاز، والماركاتُ المدعومة للشركة.
    from utils.devices import registry as _reg
    _cat = {c['key']: c for c in _reg.catalog()}
    dev_drivers = {}
    for d in devices:
        k = _reg.driver_key(d)
        c = _cat.get(k, {})
        dev_drivers[d['id']] = {'key': k, 'label': c.get('label', k), 'legacy': _reg.is_legacy(d),
                                'experimental': c.get('experimental', False), 'caps': c.get('capabilities', [])}
    return render_template('fingerprint_devices.html', devices=devices, max_devices=max_devices, current_active=current_active,
                           branches=branches, driver_catalog=list(_cat.values()), dev_drivers=dev_drivers)

@attendance_bp.route('/fingerprint/branches/add', methods=['POST'])
@login_required
@require_permission('attendance.devices')
def add_branch_quick():
    conn = get_db_connection()
    name = (request.form.get('name') or (request.get_json(silent=True) or {}).get('name') or '').strip()
    if not name:
        return jsonify({'success': False, 'message': 'اسم الفرع مطلوب'}), 400
    try:
        conn.execute('INSERT OR IGNORE INTO branches (name) VALUES (?)', (name,))
        conn.commit()
        branch = conn.execute('SELECT * FROM branches WHERE name = ?', (name,)).fetchone()
        return jsonify({'success': True, 'branch': dict(branch) if branch else {'id': None, 'name': name}})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

def _form_driver():
    """السوّاق المختار في نموذج الجهاز: '' (لم يُرسَل — ZKTeco كما كان)، أو مفتاحه،
    أو False (غير مفعّل عند هذه الشركة — مع رسالة)."""
    key = (request.form.get('driver') or '').strip()
    if not key:
        return ''
    from utils.devices import registry
    if key not in registry.enabled_keys():
        flash('الماركة دي مش مفعّلة — فعّلها الأول من «الماركات المدعومة» في صفحة الأجهزة', 'error')
        return False
    return key


def _save_driver_fields(conn, device_id, drv):
    """الماركة وبيانات الدخول — لجهازٍ اختير له سوّاق. كلمةُ المرور تُحفظ مشفّرة، ولا
    تُمسح إن تُركت الخانة فارغة عند التعديل."""
    if not drv or not device_id:
        return
    import json as _json
    from utils.devices import secrets as dsec, registry, engine
    row = conn.execute('SELECT driver_options FROM fingerprint_devices WHERE id = ?', (device_id,)).fetchone()
    try:
        opts = _json.loads((row[0] if row else '') or '{}') or {}
    except Exception:
        opts = {}
    opts['insecure_tls'] = bool(request.form.get('insecure_tls'))
    conn.execute('UPDATE fingerprint_devices SET driver = ?, auth_user = ?, use_https = ?, driver_options = ? WHERE id = ?',
                 (drv, (request.form.get('auth_user') or '').strip(), 1 if request.form.get('use_https') else 0,
                  _json.dumps(opts), device_id))
    pw = request.form.get('auth_password') or ''
    if pw:
        conn.execute('UPDATE fingerprint_devices SET auth_secret = ? WHERE id = ?', (dsec.seal(pw), device_id))
    conn.commit()
    if not registry.is_legacy({'driver': drv}):
        engine.ensure_push_token(conn, device_id)


@attendance_bp.route('/fingerprint/devices/add', methods=['POST'])
@login_required
@require_permission('attendance.devices')
def add_device():
    conn = get_db_connection()
    
    # Enforce device limit on active devices
    try:
        max_devices = get_effective_max_devices()
        current_active = conn.execute('SELECT COUNT(*) FROM fingerprint_devices WHERE is_active = 1').fetchone()[0]
        
        if current_active >= max_devices:
            pass # conn.close() removed to prevent leak in Flask g
            flash(gettext('x.f_max_active_devices') % {'p0': f'{max_devices}'}, 'error')
            return redirect(url_for('attendance.devices'))
    except Exception as e:
        print(f"Error checking device limit: {e}")
        
    device_name = request.form.get('device_name')
    device_ip = request.form.get('device_ip')
    device_port = int(request.form.get('device_port', 4370))
    is_adms = 1 if 'is_adms' in request.form else 0
    branch_name = (request.form.get('branch_name') or '').strip()
    # الماركة/البروتوكول (utils/devices). بلا اختيار = ZKTeco كما كان دائمًا.
    drv = _form_driver()
    if drv is False:
        return redirect(url_for('attendance.devices'))
    if drv:
        is_adms = 1 if drv == 'zk_push' else 0
    
    branch_id = None
    if branch_name:
        try:
            conn.execute('INSERT OR IGNORE INTO branches (name) VALUES (?)', (branch_name,))
            conn.commit()
            b_row = conn.execute('SELECT id FROM branches WHERE name = ?', (branch_name,)).fetchone()
            if b_row:
                branch_id = b_row['id']
        except Exception as be:
            print(f"Error registering branch: {be}")
    
    try:
        cur = conn.execute('''
            INSERT INTO fingerprint_devices (device_name, device_ip, device_port, is_active, is_adms, branch_name, branch_id)
            VALUES (?, ?, ?, 1, ?, ?, ?)
        ''', (device_name, device_ip, device_port, is_adms, branch_name or 'الفرع الرئيسي', branch_id))
        conn.commit()
        _save_driver_fields(conn, cur.lastrowid, drv)
        
        # If ADMS, remove from pending list if exists
        if is_adms:
            # Assuming we match by IP if name is used as SN??
            # Or try to delete by IP
             conn.execute('DELETE FROM pending_adms_devices WHERE ip_address = ?', (device_ip,))
             conn.commit()
             
        flash(gettext('x.f_device_added'), 'success')
    except Exception as e:
        flash(gettext('x.f_error_colon') % {'p0': f'{e}'}, 'error')
    finally:
        pass # conn.close() removed to prevent leak in Flask g
        
    return redirect(url_for('attendance.devices'))

@attendance_bp.route('/fingerprint/devices/edit/<int:id>', methods=['GET', 'POST'])
@login_required
@require_permission('attendance.devices')
def edit_device(id):
    conn = get_db_connection()
    
    if request.method == 'POST':
        device_name = request.form.get('device_name')
        device_ip = request.form.get('device_ip')
        device_port = int(request.form.get('device_port', 4370))
        is_active = 1 if 'is_active' in request.form else 0
        is_adms = 1 if 'is_adms' in request.form else 0
        branch_name = (request.form.get('branch_name') or '').strip()
        drv = _form_driver()
        if drv is False:
            return redirect(url_for('attendance.devices'))
        if drv:
            is_adms = 1 if drv == 'zk_push' else 0
        
        branch_id = None
        if branch_name:
            try:
                conn.execute('INSERT OR IGNORE INTO branches (name) VALUES (?)', (branch_name,))
                conn.commit()
                b_row = conn.execute('SELECT id FROM branches WHERE name = ?', (branch_name,)).fetchone()
                if b_row:
                    branch_id = b_row['id']
            except Exception as be:
                print(f"Error registering branch: {be}")
        
        # Enforce limit if activating
        if is_active == 1:
            try:
                from utils.db import get_setting
                max_devices = get_effective_max_devices()
                current_active = conn.execute('SELECT COUNT(*) FROM fingerprint_devices WHERE is_active = 1 AND id != ?', (id,)).fetchone()[0]
                if current_active >= max_devices:
                    is_active = 0
                    flash(gettext('x.f_device_saved_inactive') % {'p0': f'{max_devices}'}, 'warning')
            except Exception as e:
                print(e)
                
        try:
            conn.execute('''
                UPDATE fingerprint_devices 
                SET device_name = ?, device_ip = ?, device_port = ?, is_active = ?, is_adms = ?, branch_name = ?, branch_id = ?
                WHERE id = ?
            ''', (device_name, device_ip, device_port, is_active, is_adms, branch_name or 'الفرع الرئيسي', branch_id, id))
            conn.commit()
            _save_driver_fields(conn, id, drv)
            flash(gettext('x.f_device_updated'), 'success')
        except Exception as e:
            flash(gettext('x.f_error_colon') % {'p0': f'{e}'}, 'error')
        finally:
            pass # conn.close() removed to prevent leak in Flask g
        return redirect(url_for('attendance.devices'))
        
    device = conn.execute('SELECT * FROM fingerprint_devices WHERE id = ?', (id,)).fetchone()
    pass # conn.close() removed to prevent leak in Flask g
    
    if not device:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or 'application/json' in request.headers.get('Accept', '') or True:
            return jsonify({'success': False, 'message': 'الجهاز غير موجود'}), 404
            
    dev_dict = dict(device)
    # كلمةُ مرور الجهاز لا تخرج للمتصفّح — يُقال فقط إن كانت محفوظة.
    dev_dict['has_password'] = bool(dev_dict.pop('auth_secret', None))
    from utils.devices import registry as _reg
    dev_dict['driver'] = _reg.driver_key(dev_dict)
    for k, v in dev_dict.items():
        if hasattr(v, 'isoformat'):
            dev_dict[k] = v.isoformat()
        elif v is None:
            dev_dict[k] = '' if k == 'branch_name' else None
            
    # Always return JSON since device editing is modal-based
    return jsonify(dev_dict)

@attendance_bp.route('/fingerprint/devices/toggle/<int:id>')
@login_required
@require_permission('attendance.devices')
def toggle_device(id):
    conn = get_db_connection()
    device = conn.execute('SELECT is_active FROM fingerprint_devices WHERE id = ?', (id,)).fetchone()
    
    if device and not device['is_active']:
        try:
            from utils.db import get_setting
            max_devices = get_effective_max_devices()
            current_active = conn.execute('SELECT COUNT(*) FROM fingerprint_devices WHERE is_active = 1').fetchone()[0]
            if current_active >= max_devices:
                pass # conn.close() removed to prevent leak in Flask g
                flash(gettext('x.f_cannot_activate_device') % {'p0': f'{max_devices}'}, 'error')
                return redirect(url_for('attendance.devices'))
        except Exception as e:
            print(e)
            
    conn.execute('UPDATE fingerprint_devices SET is_active = NOT is_active WHERE id = ?', (id,))
    conn.commit()
    pass # conn.close() removed to prevent leak in Flask g
    flash(gettext('x.f_device_status_changed'), 'success')
    return redirect(url_for('attendance.devices'))

@attendance_bp.route('/fingerprint/devices/delete/<int:id>')
@login_required
@require_permission('attendance.devices')
def delete_device(id):
    # إلى سلّة المحذوفات بسجلّ — يُسترجع من صفحة «العمليات الحساسة».
    from utils import sensitive_ops
    conn = get_db_connection()
    sensitive_ops.delete_device(conn, id, note='من صفحة الأجهزة')
    pass # conn.close() removed to prevent leak in Flask g
    flash(gettext('x.f_device_deleted'), 'success')
    return redirect(url_for('attendance.devices'))

def _new_device(device_id):
    """صفُّ الجهاز إن كان من الماركات الجديدة (Hikvision…)، وإلّا None — ZKTeco بمساره القديم."""
    from utils.devices import registry
    row = get_db_connection().execute('SELECT * FROM fingerprint_devices WHERE id = ?', (device_id,)).fetchone()
    if row is None or registry.is_legacy(row):
        return None
    return row


def _driver_test(device):
    from utils.devices import registry, base as dbase
    try:
        r = registry.get_driver(device).test()
        return {'success': bool(r.get('ok')), 'message': r.get('message', ''), 'device_info': r.get('info', {})}
    except dbase.DriverError as e:
        return {'success': False, 'message': str(e)}
    except Exception as e:
        return {'success': False, 'message': f'خطأ: {str(e)[:150]}'}


@attendance_bp.route('/fingerprint/test/<int:device_id>')
@login_required
@require_permission('attendance.devices')
def test_device_api(device_id):
    _new = _new_device(device_id)
    if _new is not None:
        return jsonify(_driver_test(_new))
    success, result = test_fingerprint_device(device_id)
    
    if success:
        return jsonify({
            'success': True,
            'message': 'تم الاتصال بالجهاز بنجاح',
            'device_info': result
        })
    else:
        return jsonify({
            'success': False,
            'message': result
        })

@attendance_bp.route('/fingerprint/devices/<int:device_id>/test')
@login_required
@require_permission('attendance.devices')
def test_device_route(device_id):
    _new = _new_device(device_id)
    if _new is not None:
        r = _driver_test(_new)
        flash(r['message'], 'success' if r['success'] else 'error')
        return redirect(url_for('attendance.devices'))
    success, message = test_fingerprint_device(device_id)
    if success:
        flash(message, 'success')
    else:
        flash(message, 'error')
    return redirect(url_for('attendance.devices'))

@attendance_bp.route('/fingerprint/sync', methods=['POST'])
@login_required
@require_permission('attendance.devices')
def sync_fingerprint():
    # يمكن تحديد جهاز معين أو المزامنة للكل
    device_id = request.form.get('device_id')
    
    # تشغيل المزامنة
    # ملاحظة: هذا قد يأخذ وقتاً طويلاً، يفضل نقله لـ thread منفصل وإرجاع ID للمتابعة
    # ولكن للتبسيط سنبقيه كما هو حالياً مع استدعاء دالة المزامنة
    try:
        # إذا تم تحديد جهاز نمرره للدالة (تحتاج تعديل لاستقباله)
        # حالياً الدالة sync_all_fingerprint_devices تزامن الكل
        # القفلُ نفسُه الذي تمرّ به المزامنةُ التلقائيّة (كلَّ ساعة): مزامنتان
        # معًا تتداخل سجلّاتهما والجهازُ مُعطَّلٌ أثناء الأولى.
        from utils import device_autosync
        started = time.strftime('%Y-%m-%d %H:%M:%S')
        result = device_autosync.run_punches()
        if result is None:
            return jsonify({'success': False, 'busy': True,
                            'message': 'مزامنةٌ جارية الآن (تلقائيّة أو من مستخدمٍ آخر) — حاول بعد دقيقة.'})
        device_autosync.log_run('manual', started, result, device_autosync.SKIP)
        from utils import remote_commands
        remote_commands.poll_soon()             # وطلباتُ البوّابة مع المزامنة
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'message': f'خطأ: {str(e)}'})

@attendance_bp.route('/fingerprint/sync_users', methods=['POST'])
@login_required
@require_permission('attendance.devices')
def sync_users():
    # تشغيل في thread منفصل لتجنب تجميد الواجهة — وبالقفل المشترك مع التلقائيّة.
    from utils import device_autosync
    if device_autosync.busy():
        return jsonify({'success': False, 'busy': True,
                        'message': 'مزامنةٌ جارية الآن (تلقائيّة أو من مستخدمٍ آخر) — حاول بعد دقيقة.'})
    def _users_logged():
        started = time.strftime('%Y-%m-%d %H:%M:%S')
        res = device_autosync.run_users()
        if res is not None:
            device_autosync.log_run('manual', started, device_autosync.SKIP, res)
        from utils import remote_commands
        remote_commands.poll_soon()
    thread = threading.Thread(target=_users_logged, daemon=True)
    thread.start()
    
    return jsonify({
        'success': True, 
        'message': 'بدأت عملية مزامنة المستخدمين في الخلفية'
    })


@attendance_bp.route('/fingerprint/autosync', methods=['POST'])
@login_required
@require_permission('attendance.devices')
def toggle_device_autosync():
    """تشغيلُ المزامنة التلقائيّة (كلَّ ساعة) أو إيقافُها."""
    from utils import device_autosync
    from utils.db import set_setting
    on = request.form.get('enabled') in ('1', 'on', 'true')
    set_setting(device_autosync.SETTING_ENABLED, '1' if on else '0')
    flash('المزامنة التلقائيّة كلَّ ساعة: ' + ('مفعّلة' if on else 'متوقّفة'), 'success')
    return redirect(url_for('attendance.fingerprint'))

@attendance_bp.route('/fingerprint/autosync/templates', methods=['POST'])
@login_required
@require_permission('attendance.devices')
def toggle_device_autosync_templates():
    """سحبُ البصمات نفسِها (صوابع ووجه) من الأجهزة مرّةً في اليوم — تشغيل أو إيقاف."""
    from utils import device_autosync
    from utils.db import set_setting
    on = request.form.get('enabled') in ('1', 'on', 'true')
    set_setting(device_autosync.SETTING_TEMPLATES, '1' if on else '0')
    flash('سحب البصمات نفسها من الأجهزة مرة في اليوم: ' + ('مفعّل' if on else 'متوقّف'), 'success')
    return redirect(url_for('attendance.fingerprint'))

@attendance_bp.route('/fingerprint/autosync/run', methods=['POST'])
@login_required
@require_permission('attendance.devices')
def run_device_autosync_now():
    """«مزامنة الآن» من بطاقة المزامنة التلقائيّة: البصماتُ والمستخدمون معًا، في الخلفيّة."""
    from utils import device_autosync
    if device_autosync.start_in_background('manual'):
        flash('بدأت المزامنة الآن (البصمات ثم المستخدمون) — حدّث الصفحة بعد دقيقة لترى نتيجتها في السجلّ.', 'success')
    else:
        flash('مزامنةٌ جارية الآن (تلقائيّة أو من مستخدمٍ آخر) — حدّث الصفحة بعد دقيقة.', 'warning')
    return redirect(url_for('attendance.fingerprint'))


@attendance_bp.route('/fingerprint/test_duplicate_prevention')
@login_required
@require_permission('attendance.devices')
def test_duplicate_route():
    result = test_duplicate_prevention()
    return jsonify(result)
@attendance_bp.route('/fingerprint/upload_users')
@login_required
@require_permission('attendance.devices')
def upload_users_page():
    conn = get_db_connection()
    # Fetch employees with their template counts
    employees = conn.execute('''
        SELECT e.*, 
        (SELECT COUNT(*) FROM fingerprint_templates WHERE pin = e.employee_number) as fp_count,
        (SELECT COUNT(*) FROM fingerprint_faces WHERE pin = e.employee_number) as face_count
        FROM employees e 
        WHERE e.is_active = 1 
        ORDER BY e.name
    ''').fetchall()
    devices = conn.execute('SELECT * FROM fingerprint_devices WHERE is_active = 1 ORDER BY device_name').fetchall()
    return render_template('fingerprint_device_users.html', employees=employees, devices=devices,
                           active_tab='upload', **_bio_context(conn, devices))


def _bio_context(conn, devices):
    """البصماتُ المحفوظة بإصداراتها لكلّ موظّف، وإصدارُ البصمة لكلّ جهاز ADMS معروف."""
    from utils import biometric_templates as bio
    return {'bio_summary': bio.summary(conn),
            'device_versions': {d['id']: bio.device_versions(d).get(1) for d in devices
                                if bio.device_versions(d).get(1)}}

@attendance_bp.route('/fingerprint/api/upload_users', methods=['POST'])
@login_required
@require_permission('attendance.devices')
def upload_users_api():
    data = request.json
    user_ids = data.get('user_ids', [])
    device_ids = data.get('device_ids', [])
    
    if not user_ids or not device_ids:
        return jsonify({'success': False, 'message': 'يجب اختيار مستخدم واحد وجهاز واحد على الأقل'})
        
    conn = get_db_connection()
    results = []
    
    # Pre-fetch employees to avoid repeated DB calls
    placeholders = ','.join('?' * len(user_ids))
    employees = conn.execute(f'SELECT * FROM employees WHERE id IN ({placeholders})', user_ids).fetchall()
    pass # conn.close() removed to prevent leak in Flask g
    
    from utils import device_access
    from utils import biometric_templates as bio
    from utils.fingerprint_utils import adms_template_commands
    for device_id in device_ids:
        device_results = {'device_id': device_id, 'success_count': 0, 'fail_count': 0, 'details': []}
        conn = get_db_connection()
        device = conn.execute('SELECT * FROM fingerprint_devices WHERE id = ?', (device_id,)).fetchone()
        if not device:
            device_results['fail_count'] = len(employees)
            device_results['details'].append({'user': '-', 'status': 'error', 'message': 'الجهاز غير موجود'})
            results.append(device_results)
            continue

        from utils.devices import registry as _reg, engine as _eng
        if not _reg.is_legacy(device):
            # ماركة جديدة (Hikvision…): المحرّك — الموظّف، ووجهُه من صورته إن قبلها الجهاز.
            try:
                details = _eng.push_employees(conn, device, employees)
            except Exception as e:
                details = [{'user': emp['name'], 'status': 'error', 'message': str(e)[:150]} for emp in employees]
            for d in details:
                device_results['fail_count' if d['status'] == 'error' else 'success_count'] += 1
            device_results['details'].extend(details)
        elif device['is_adms']:
            for emp in employees:
                try:
                    card_num = int(emp['card_number']) if emp['card_number'] and str(emp['card_number']).isdigit() else 0
                except Exception:
                    card_num = 0
                payload = {
                    'PIN': str(emp['employee_number']),
                    'Name': emp['name'],
                    'Pri': int(emp['privilege']) if emp['privilege'] is not None else 0,
                    'Passwd': str(emp['password']) if emp['password'] else '',
                    'Card': card_num,
                    'Grp': str(emp['group_id']) if emp['group_id'] else '1',
                    'Enabled': 1,
                }
                try:
                    conn.execute('''INSERT INTO adms_commands (device_id, command_type, payload, status)
                                    VALUES (?, 'DATA UPDATE USERINFO', ?, 'PENDING')''',
                                 (device_id, json.dumps(payload)))
                    # البصماتُ بنوعها وإصدارها كما حُفظت (من ADMS أو من جهازٍ مباشر).
                    st = {}
                    cmds = adms_template_commands(conn, emp['employee_number'], device, st)
                    for ctype, cpayload in cmds:
                        conn.execute('''INSERT INTO adms_commands (device_id, command_type, payload, status)
                                        VALUES (?, ?, ?, 'PENDING')''', (device_id, ctype, cpayload))
                    conn.commit()
                    device_results['success_count'] += 1
                    if cmds:
                        msg, status = f'في الطابور ومعه {len(cmds)} بصمة/وجه', 'success'
                    elif st.get('skipped'):
                        msg, status = 'في الطابور بدون بصمات', 'warning'
                    else:
                        msg, status = 'في الطابور بدون بصمات — لا بصمات محفوظة له', 'warning'
                    if st.get('skipped'):
                        status = 'warning'
                        msg += (f" — {st['skipped']} بصمة محفوظة بإصدار غير إصدار الجهاز"
                                f" ({bio.device_versions(device).get(1, '؟')}): يبصم عليه مرة واحدة")
                    device_results['details'].append({'user': emp['name'], 'status': status, 'message': msg})
                except Exception as e:
                    device_results['fail_count'] += 1
                    device_results['details'].append({'user': emp['name'], 'status': 'error',
                                                      'message': f'Queue Error: {str(e)}'})
        else:
            # جهازٌ مباشر (IP + بورت، مثل K40): يُرفع ومعه بصماتُه المحفوظة عندنا.
            try:
                details, _ver = device_access.push_direct(conn, device, employees)
            except Exception as e:
                details = [{'user': emp['name'], 'status': 'error',
                            'message': f'خطأ في الاتصال بالجهاز: {str(e)[:120]}'} for emp in employees]
            for d in details:
                if d['status'] == 'error':
                    device_results['fail_count'] += 1
                else:
                    device_results['success_count'] += 1
            device_results['details'].extend(details)
            
        results.append(device_results)
        
    return jsonify({'success': True, 'results': results})

@attendance_bp.route('/fingerprint/sync_from_device')
@login_required
@require_permission('attendance.edit')
def sync_from_device():
    """صفحة سحب البيانات من الأجهزة"""
    conn = get_db_connection()
    # كلُّ الأجهزة: ADMS بأوامر تُنفَّذ عند اتّصاله، والمباشرُ (IP + بورت) يُسحب منه فورًا.
    devices = conn.execute('SELECT * FROM fingerprint_devices WHERE is_active = 1 ORDER BY device_name').fetchall()
    
    # Fetch employees to allow specific selection
    employees = conn.execute('''
        SELECT e.*, 
               (SELECT COUNT(*) FROM fingerprint_templates WHERE pin = e.employee_number AND template_type = 1) as fp_count,
               (SELECT COUNT(*) FROM fingerprint_templates WHERE pin = e.employee_number AND template_type = 9) as face_count
        FROM employees e 
        WHERE is_active = 1 
        ORDER BY employee_number
    ''').fetchall()
    
    return render_template('fingerprint_device_users.html', devices=devices, employees=[dict(e) for e in employees],
                           active_tab='pull', **_bio_context(conn, devices))

@attendance_bp.route('/fingerprint/api/sync_from_device', methods=['POST'])
@login_required
@require_permission('attendance.edit')
def sync_from_device_api():
    """جدولة أوامر سحب البيانات من أجهزة ADMS"""
    data = request.get_json()
    device_ids = data.get('device_ids', [])
    user_pins = data.get('user_pins', []) # New: specific pins to sync
    
    if not device_ids:
        return jsonify({'success': False, 'message': 'لم يتم اختيار أي أجهزة'})
        
    conn = get_db_connection()
    results = []
    
    try:
        from utils import device_access
        for dev_id in device_ids:
            device = conn.execute('SELECT * FROM fingerprint_devices WHERE id = ?', (dev_id,)).fetchone()
            from utils.devices import registry as _reg, engine as _eng
            if device and not _reg.is_legacy(device):
                # ماركة جديدة: الموظّفون (ومن ليس موظّفًا يُضاف) — البصماتُ لا تنتقل بين الماركات.
                try:
                    r = _eng.sync_users(conn, device)
                    msg = (r.get('skipped') or f"اتقرى {r['users']} موظف من الجهاز، واتضاف {r['employees_added']} موظف جديد"
                           " — بصمات الصباع بتفضل على الجهاز (مبتتنقلش بين الماركات)")
                    results.append({'device_id': dev_id, 'success': 'skipped' not in r, 'message': msg})
                except Exception as e:
                    results.append({'device_id': dev_id, 'success': False, 'message': f'تعذّر: {str(e)[:150]}'})
                continue
            if device and not device['is_adms']:
                # مباشر: البصماتُ تُسحب الآن وتُحفظ — لتُرفع منه لأيّ جهاز.
                try:
                    r = device_access.pull_direct(conn, device, user_pins or None)
                    msg = f"سُحبت {r['templates']} بصمة لـ {r['employees']} موظف"
                    if r['fp_version']:
                        msg += f" (إصدار البصمة {r['fp_version']})"
                    if r['unknown']:
                        msg += f" — {len(r['unknown'])} مستخدم على الجهاز غير مسجّل في البرنامج"
                    if r['missing']:
                        msg += f" — غير موجود على الجهاز: {', '.join(r['missing'][:10])}"
                    results.append({'device_id': dev_id, 'success': True, 'message': msg})
                except Exception as e:
                    results.append({'device_id': dev_id, 'success': False,
                                    'message': f'تعذّر الاتصال بالجهاز: {str(e)[:120]}'})
                continue
            if user_pins:
                # Sync specific users
                for pin in user_pins:
                    # Query UserInfo for this pin
                    conn.execute('''
                        INSERT INTO adms_commands (device_id, command_type, payload, status)
                        VALUES (?, 'DATA QUERY USERINFO', ?, 'PENDING')
                    ''', (dev_id, json.dumps({"PIN": str(pin)})))
                    
                    # Query Templates for this pin
                    conn.execute('''
                        INSERT INTO adms_commands (device_id, command_type, payload, status)
                        VALUES (?, 'DATA QUERY FINGERTMP', ?, 'PENDING')
                    ''', (dev_id, json.dumps({"PIN": str(pin)})))
                    
                    # Query Face for this pin
                    conn.execute('''
                        INSERT INTO adms_commands (device_id, command_type, payload, status)
                        VALUES (?, 'DATA QUERY FACE', ?, 'PENDING')
                    ''', (dev_id, json.dumps({"PIN": str(pin)})))
                    # والأجهزةُ الأحدث (SpeedFace): الصوابعُ والوجهُ بالضوء المرئيّ من BIODATA.
                    for _bt in (1, 9):
                        conn.execute('''
                            INSERT INTO adms_commands (device_id, command_type, payload, status)
                            VALUES (?, 'DATA QUERY BIODATA', ?, 'PENDING')
                        ''', (dev_id, json.dumps({"Type": _bt, "PIN": str(pin)})))
                
                msg = f'تمت جدولة سحب لـ {len(user_pins)} موظف'
            else:
                # Bulk sync (All users)
                conn.execute('''
                    INSERT INTO adms_commands (device_id, command_type, payload, status)
                    VALUES (?, 'DATA QUERY USERINFO', '{"PIN": ""}', 'PENDING')
                ''', (dev_id,))
                conn.execute('''
                    INSERT INTO adms_commands (device_id, command_type, payload, status)
                    VALUES (?, 'DATA QUERY FINGERTMP', '{"PIN": ""}', 'PENDING')
                ''', (dev_id,))
                conn.execute('''
                    INSERT INTO adms_commands (device_id, command_type, payload, status)
                    VALUES (?, 'DATA QUERY FACE', '{"PIN": ""}', 'PENDING')
                ''', (dev_id,))
                for _bt in (1, 9):
                    conn.execute('''
                        INSERT INTO adms_commands (device_id, command_type, payload, status)
                        VALUES (?, 'DATA QUERY BIODATA', ?, 'PENDING')
                    ''', (dev_id, json.dumps({"Type": _bt})))
                msg = 'تمت جدولة سحب كافة الموظفين والبصمات'
            
            results.append({
                'device_id': dev_id,
                'success': True,
                'message': msg
            })
            
        conn.commit()
        return jsonify({'success': True, 'results': results})
    except Exception as e:
        return jsonify({'success': False, 'message': f'خطأ في الجدولة: {str(e)}'})
    finally:
        pass # conn.close() removed to prevent leak in Flask g

@attendance_bp.route('/fingerprint/api/adms/search')
@login_required
@require_permission('attendance.devices')
def search_adms_devices_api():
    """
    Return list of pending ADMS devices that contacted the server.
    """
    conn = get_db_connection()
    devices = conn.execute('SELECT * FROM pending_adms_devices ORDER BY last_activity DESC').fetchall()
    pass # conn.close() removed to prevent leak in Flask g
    
    return jsonify({
        'success': True,
        'devices': [dict(d) for d in devices]
    })

@attendance_bp.route('/fingerprint/api/scan_network')
@login_required
@require_permission('attendance.devices')
def scan_network_api():
    from utils.find_device_utils import NetworkScanner
    from flask import Response, stream_with_context
    import json
    
    mode = request.args.get('mode', 'local')
    scanner = NetworkScanner()
    
    subnets = None
    if mode == 'wide':
        subnets = ['192.168.0.0/16']
    
    def generate():
        # Generator for SSE
        for event in scanner.scan_yield(subnets=subnets):
            yield f"data: {json.dumps(event)}\n\n"
            
    return Response(stream_with_context(generate()), mimetype='text/event-stream')

@attendance_bp.route('/fingerprint/api/devices/bulk_add', methods=['POST'])
@login_required
@require_permission('attendance.devices')
def add_devices_bulk():
    data = request.json
    # Support both 'ips' (list of strings) and 'devices' (list of dicts {ip, name})
    devices = data.get('devices', [])
    if not devices and 'ips' in data:
        # Backwards compatibility or simple format
        devices = [{'ip': ip, 'name': f"Device {ip}"} for ip in data['ips']]
    
    if not devices:
        return jsonify({'success': False, 'message': 'لم يتم اختيار أي جهاز'})
        
    conn = get_db_connection()
    success_count = 0
    errors = []
    
    # Check limit before starting
    try:
        max_limit = get_effective_max_devices()
        current_active = conn.execute('SELECT COUNT(*) FROM fingerprint_devices WHERE is_active = 1').fetchone()[0]
        
        if current_active >= max_limit:
            pass # conn.close() removed to prevent leak in Flask g
            return jsonify({'success': False, 'message': f'عفواً، لقد وصلت للحد الأقصى المسموح به للأجهزة ({max_limit})'})
    except Exception as e:
        print(f"Error checking limit in bulk add: {e}")
        max_limit = 3 # Fallback

    for dev in devices:
        # Check limit inside loop too in case multiple were selected
        try:
             count_now = conn.execute('SELECT COUNT(*) FROM fingerprint_devices WHERE is_active = 1').fetchone()[0]
             if count_now >= max_limit:
                 errors.append(f"تم الوصول للحد الأقصى ({max_limit}). لم يتم إضافة باقي الأجهزة المختارة.")
                 break
        except: pass

        ip = dev.get('ip')
        name = dev.get('name') or f"Device {ip}"
        
        branch_name = (dev.get('branch_name') or 'الفرع الرئيسي').strip()
        
        try:
            # Check if exists
            existing = conn.execute('SELECT id FROM fingerprint_devices WHERE device_ip = ?', (ip,)).fetchone()
            if not existing:
                conn.execute('''
                    INSERT INTO fingerprint_devices (device_name, device_ip, device_port, is_active, branch_name)
                    VALUES (?, ?, ?, 1, ?)
                ''', (name, ip, 4370, branch_name))
                success_count += 1
            else:
                errors.append(f"{ip}: موجود مسبقاً")
        except Exception as e:
            errors.append(f"{ip}: {str(e)}")
            
    conn.commit()
    pass # conn.close() removed to prevent leak in Flask g
    return jsonify({
        'success': success_count > 0, 
        'count': success_count, 
        'message': f'تم إضافة {success_count} جهاز بنجاح',
        'errors': errors
    })

@attendance_bp.route('/api/devices/limit', methods=['GET', 'POST'])
@login_required
@require_permission('attendance.devices')
def manage_device_limit():
    """API endpoint to view and control the allowed number of fingerprint devices"""
    from utils.db import get_setting, set_setting
    
    if request.method == 'POST':
        data = request.get_json()
        if not data or 'max_devices' not in data:
            return jsonify({'success': False, 'message': 'بيانات غير مكتملة'}), 400
            
        try:
            new_limit = int(data['max_devices'])
            if new_limit < 1:
                return jsonify({'success': False, 'message': 'الحد الأدنى هو 1'}), 400
                
            set_setting('max_devices', str(new_limit))
            return jsonify({'success': True, 'message': f'تم تحديث الحد الأقصى للأجهزة إلى {new_limit}'})
        except ValueError:
            return jsonify({'success': False, 'message': 'قيمة غير صالحة'}), 400
            
    # GET Request
    conn = get_db_connection()
    total_devices = conn.execute('SELECT COUNT(*) FROM fingerprint_devices').fetchone()[0]
    active_devices = conn.execute('SELECT COUNT(*) FROM fingerprint_devices WHERE is_active = 1').fetchone()[0]
    pass # conn.close() removed to prevent leak in Flask g
    
    max_devices = get_effective_max_devices()
    
    return jsonify({
        'success': True,
        'total_devices': total_devices,
        'active_devices': active_devices,
        'max_devices': max_devices,
        'available_slots': max(0, max_devices - total_devices),
        'can_add_more': total_devices < max_devices
    })

@attendance_bp.route('/attendance/oracle')
@login_required
@require_permission('page.oracle_control')
def oracle_control():
    """مركز التحكم في تكامل أوراكل"""
    import os as _os
    from utils.db import get_setting
    from utils.oracle_db import get_sync_queue_stats
    enabled = str(get_setting('oracle_enabled', '1')).strip() == '1'
    stats = get_sync_queue_stats()

    # «مفعَّلة» و«مضبوطة» شيئان.
    #
    # المفتاح مفتوح افتراضيًّا، فالشاشة كانت تقول «مفعَّلة» على نظامٍ
    # لم يُضبط فيه مضيفٌ ولا مستخدم — لا يُزامَن فيه شيء ولا يُكتب
    # طابور. فتُقال الحال كما هي بدل شارةٍ خضراء تكذب.
    from utils.oracle_db import _oracle_conf, oracle_configured
    configured = oracle_configured()
    cfg = {
        'host': _oracle_conf('oracle_host', 'ORACLE_HOST', 'localhost'),
        'port': _oracle_conf('oracle_port', 'ORACLE_PORT', '1521'),
        'service': _oracle_conf('oracle_service', 'ORACLE_SERVICE', 'XEPDB1'),
        'user': _oracle_conf('oracle_user', 'ORACLE_USER', 'HR'),
        'table': _oracle_conf('oracle_table', 'ORACLE_TABLE_NAME', 'ATTENDANCE_LOGS'),
        'lib_dir': str(get_setting('oracle_lib_dir', '') or ''),
        'password_set': bool(str(get_setting('oracle_password', '') or '').strip()),
    }
    return render_template('oracle_control.html', enabled=enabled,
                           configured=configured, stats=stats, cfg=cfg)

@attendance_bp.route('/attendance/oracle/toggle', methods=['POST'])
@login_required
@require_permission('attendance.edit')
def oracle_toggle():
    from utils.db import set_setting
    from utils import oracle_db as _odb
    new_state = '1' if request.form.get('enable') == '1' else '0'
    set_setting('oracle_enabled', new_state)
    _odb._oracle_enabled_cache['ts'] = 0
    if new_state == '1':
        flash(gettext('x.f_oracle_enabled'), 'success')
    else:
        flash(gettext('x.f_oracle_disabled'), 'success')
    return redirect(url_for('attendance.oracle_control'))

@attendance_bp.route('/attendance/oracle/settings', methods=['POST'])
@login_required
@require_permission('attendance.edit')
def oracle_save_settings():
    from utils.db import set_setting
    for form_key, setting_key in [('host', 'oracle_host'), ('port', 'oracle_port'),
                                  ('service', 'oracle_service'), ('user', 'oracle_user'),
                                  ('table', 'oracle_table'), ('lib_dir', 'oracle_lib_dir')]:
        set_setting(setting_key, request.form.get(form_key, '').strip())
    pw = request.form.get('password', '')
    if pw.strip():
        set_setting('oracle_password', pw.strip())
    flash(gettext('x.f_oracle_settings_saved'), 'success')
    return redirect(url_for('attendance.oracle_control'))

@attendance_bp.route('/attendance/api/oracle/test')
@login_required
@require_permission('attendance.view')
def oracle_test():
    from utils.oracle_db import check_oracle_connection, is_oracle_enabled
    if not is_oracle_enabled():
        return jsonify({'success': True, 'enabled': False, 'alive': False})
    return jsonify({'success': True, 'enabled': True, 'alive': bool(check_oracle_connection())})

@attendance_bp.route('/attendance/oracle/history')
@login_required
@require_permission('attendance.view')
def oracle_history():
    """Oracle Sync History Page"""
    import os
    conn = get_db_connection()
    # Get last 100 records
    history = conn.execute('''
        SELECT id, user_id, check_time, check_type, verify_code, device_ip, retry_count, status, created_at 
        FROM oracle_sync_queue 
        ORDER BY created_at DESC 
        LIMIT 100
    ''').fetchall()
    pass # conn.close() removed to prevent leak in Flask g
    
    table_name = os.environ.get("ORACLE_TABLE_NAME", "ATTENDANCE_LOGS")
    # For sync interval, we'll use a hardcoded 5 for now as per current logic, 
    # but we could also get it from settings if we added one.
    sync_interval = "5 دقائق"
    
    return render_template('oracle_history.html', 
                          history=history, 
                          table_name=table_name,
                          sync_interval=sync_interval)

@attendance_bp.route('/attendance/api/oracle/stats')
@login_required
@require_permission('attendance.view')
def oracle_stats():
    """API for Oracle Sync Dashboard"""
    from utils.oracle_db import check_oracle_connection
    conn = get_db_connection()
    
    stats = conn.execute('''
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN status = 'PENDING' THEN 1 ELSE 0 END) as pending,
            SUM(CASE WHEN status = 'SYNCED' THEN 1 ELSE 0 END) as synced,
            SUM(CASE WHEN status = 'FAILED' THEN 1 ELSE 0 END) as failed
        FROM oracle_sync_queue
    ''').fetchone()
    
    pass # conn.close() removed to prevent leak in Flask g
    
    is_online = check_oracle_connection()
    
    return jsonify({
        'success': True,
        'is_online': is_online,
        'stats': {
            'total': stats['total'] or 0,
            'pending': stats['pending'] or 0,
            'synced': stats['synced'] or 0,
            'failed': stats['failed'] or 0
        }
    })

@attendance_bp.route('/attendance/api/oracle/sync_missing', methods=['POST'])
@login_required
@require_permission('attendance.edit')
def sync_missing_to_oracle():
    """Find records in attendance_records not yet in oracle_sync_queue and add them"""
    from utils.oracle_db import is_oracle_enabled
    if not is_oracle_enabled():
        return jsonify({'success': False, 'message': gettext('x.oracle_is_disabled')})
    from utils.oracle_db import process_sync_queue
    import threading
    
    conn = get_db_connection()
    try:
        # Find records in attendance_records that don't exist in oracle_sync_queue
        # We join on employee_number as user_id, check_time, check_type
        missing_records = conn.execute('''
            SELECT 
                e.employee_number,
                ar.check_time, 
                ar.check_type, 
                ar.verify_code, 
                fd.device_ip
            FROM attendance_records ar
            JOIN employees e ON ar.employee_id = e.id
            LEFT JOIN oracle_sync_queue osq ON 
                osq.user_id = e.employee_number AND 
                osq.check_time = ar.check_time AND 
                osq.check_type = ar.check_type
            WHERE osq.id IS NULL
        ''').fetchall()
        
        added_count = 0
        for row in missing_records:
            conn.execute('''
                INSERT INTO oracle_sync_queue (user_id, check_time, check_type, verify_code, device_id, status)
                VALUES (?, ?, ?, ?, ?, 'PENDING')
            ''', (row['employee_number'], row['check_time'], row['check_type'], row['verify_code'], row['device_id']))
            added_count += 1
        
        conn.commit()
        
        if added_count > 0:
            # Trigger sync process in background
            thread = threading.Thread(target=process_sync_queue)
            thread.daemon = True
            thread.start()
            
        return jsonify({
            'success': True, 
            'message': f'تم إضافة {added_count} حركة جديدة إلى طابور المزامنة',
            'added_count': added_count
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})
    finally:
        pass # conn.close() removed to prevent leak in Flask g

@attendance_bp.route('/attendance/api/oracle/retry', methods=['POST'])
@login_required
@require_permission('attendance.edit')
def oracle_retry():
    """Trigger manual retry of pending records"""

    from utils.oracle_db import is_oracle_enabled
    if not is_oracle_enabled():
        return jsonify({'success': False, 'message': gettext('x.oracle_is_disabled')})
    from utils.oracle_db import process_sync_queue
    
    # Run in a separate thread so it doesn't block the UI
    import threading
    thread = threading.Thread(target=process_sync_queue)
    thread.daemon = True
    thread.start()
    
    return jsonify({'success': True, 'message': 'تم بدء محاولة المزامنة في الخلفية'})

@attendance_bp.route('/api/adms/sync_users/<int:device_id>', methods=['POST'])
@login_required
@require_permission('attendance.edit')
def adms_sync_users(device_id):
    """Queue commands to sync users for an ADMS device"""
    from utils.db import get_db_connection
    conn = get_db_connection()
    try:
        device = conn.execute('SELECT * FROM fingerprint_devices WHERE id = ?', (device_id,)).fetchone()
        if not device:
            return jsonify({'success': False, 'message': 'Device not found'})
        
        if not device['is_adms']:
            return jsonify({'success': False, 'message': 'Device is not an ADMS device'})

        import json
        
        # Get all employees
        employees = conn.execute('SELECT employee_number FROM employees WHERE is_active = 1').fetchall()
        
        if not employees:
            return jsonify({'success': False, 'message': 'لا يوجد موظفين نشطين لعمل مزامنة لهم'})
            
        count = 0
        for emp in employees:
            pin = str(emp['employee_number'])
            payload = json.dumps({"PIN": pin})
            
            # Request User Info
            conn.execute('''
                INSERT INTO adms_commands (device_id, command_type, payload, status)
                VALUES (?, 'DATA QUERY USERINFO', ?, 'PENDING')
            ''', (device_id, payload))
            
            # Request Fingerprints
            conn.execute('''
                INSERT INTO adms_commands (device_id, command_type, payload, status)
                VALUES (?, 'DATA QUERY FINGERTMP', ?, 'PENDING')
            ''', (device_id, payload))
            
            count += 2
            
        # Optional: Ask the device to re-upload attendance logs just in case
        conn.execute('''
            INSERT INTO adms_commands (device_id, command_type, payload, status)
            VALUES (?, 'DATA QUERY ATTLOG', '{}', 'PENDING')
        ''', (device_id,))
        
        conn.commit()
        return jsonify({'success': True, 'message': f'تم إدراج {count} أمر مزامنة بنجاح لجميع الموظفين. سيتم التنفيذ تباعاً.'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})
    finally:
        pass # conn.close() removed to prevent leak in Flask g

@attendance_bp.route('/attendance/sync_queue')
@login_required
@require_permission('attendance.view')
def sync_queue():
    """View pending records in the Oracle sync queue"""
    stats = get_sync_queue_stats()
    return render_template('sync_queue.html', stats=stats)

@attendance_bp.route('/api/attendance/sync_queue/data')
@login_required
@require_permission('attendance.view')
def sync_queue_data_api():
    """API endpoint to get pending records for the UI"""
    try:
        conn = get_db_connection()
        records = conn.execute("SELECT user_id, check_time, check_type, status, retry_count FROM oracle_sync_queue WHERE status = 'PENDING' ORDER BY id DESC LIMIT 100").fetchall()
        pass # conn.close() removed to prevent leak in Flask g
        return jsonify([dict(r) for r in records])
    except Exception as e:
        return jsonify({'error': str(e)}), 500
@attendance_bp.route('/fingerprint/api/devices/clear_users/<int:device_id>', methods=['POST'])
@login_required
@require_permission('attendance.edit')
def clear_device_users_api(device_id):
    """API endpoint to clear all users from a device"""
    if _new_device(device_id) is not None:
        return jsonify({'success': False, 'message': 'العملية دي لأجهزة ZKTeco بس — اعملها من شاشة الجهاز نفسه'})
    success, message = clear_fingerprint_device_users(device_id)
    return jsonify({
        'success': success,
        'message': message
    })
@attendance_bp.route('/fingerprint/api/devices/user_count/<int:device_id>', methods=['GET'])
@login_required
@require_permission('attendance.view')
def get_device_user_count_api(device_id):
    """API endpoint to get the number of users on a device"""
    _new = _new_device(device_id)
    if _new is not None:
        from utils.devices import registry
        try:
            n = len(registry.get_driver(_new).list_users())
            return jsonify({'success': True, 'count': n, 'message': f'{n} موظف على الجهاز', 'is_adms': False})
        except Exception as e:
            return jsonify({'success': False, 'message': str(e)[:200]})
    success, result = get_device_user_count(device_id)
    if success:
        return jsonify({
            'success': True,
            'count': result['count'],
            'message': result['message'],
            'is_adms': result['is_adms']
        })
    else:
        return jsonify({
            'success': False,
            'message': result
        })

@attendance_bp.route('/fingerprint/api/devices/reboot/<int:device_id>', methods=['POST'])
@login_required
@require_permission('attendance.edit')
def reboot_device_api(device_id):
    """API endpoint to reboot a device"""
    if _new_device(device_id) is not None:
        return jsonify({'success': False, 'message': 'العملية دي لأجهزة ZKTeco بس — اعملها من شاشة الجهاز نفسه'})
    success, message = reboot_fingerprint_device(device_id)
    return jsonify({
        'success': success,
        'message': message
    })

@attendance_bp.route('/fingerprint/api/devices/factory_reset/<int:device_id>', methods=['POST'])
@login_required
@require_permission('attendance.edit')
def factory_reset_device_api(device_id):
    """API endpoint to factory reset a device"""
    if _new_device(device_id) is not None:
        return jsonify({'success': False, 'message': 'العملية دي لأجهزة ZKTeco بس — اعملها من شاشة الجهاز نفسه'})
    success, message = factory_reset_fingerprint_device(device_id)
    return jsonify({
        'success': success,
        'message': message
    })
