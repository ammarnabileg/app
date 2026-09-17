from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for, flash
from flask_babel import gettext as _
from datetime import datetime, date, timedelta
import os

from utils.db import get_db_connection
from utils.auth import login_required, get_current_user
from utils.leave_utils import calculate_leave_balance, save_leave_balance, calculate_actual_leave_days

portal_bp = Blueprint('portal', __name__, url_prefix='/portal')

def get_portal_employee_id(for_write=False):
    """رقم الموظف صاحب الجلسة الحالية.

    `for_write=True` لكل مسار يكتب باسم الموظف — طلب إجازة أو استئذان أو
    بصمة. والفرق كله في سقوط المسؤول:

    مسؤول النظام لا سجلّ موظف له عادةً، فكان يسقط إلى «أول موظف نشط»
    ليستعرض شكل البوابة. هذا مقبول للقراءة، وخطأ في الكتابة: مسؤول يفتح
    البوابة ويضغط «تسجيل حضور» كان يُسجّل حضورًا باسم موظفٍ لم يحضر —
    ولا شيء في الشاشة يقول إنه يتصرّف نيابةً عن أحد. وسجلّ الحضور مصدرُ
    الأجر.
    """
    emp_id = session.get('employee_id')
    if emp_id:
        return emp_id
    user_id = session.get('user_id')
    if not user_id:
        return None
    conn = get_db_connection()
    row = conn.execute("SELECT employee_id, role FROM users WHERE id = ?", (user_id,)).fetchone()
    if row and row['employee_id']:
        session['employee_id'] = row['employee_id']
        return row['employee_id']

    # استعراض المسؤول: للقراءة فقط.
    if row and row['role'] == 'admin' and not for_write:
        first_emp = conn.execute("SELECT id FROM employees WHERE is_active = 1 LIMIT 1").fetchone()
        if first_emp:
            return first_emp['id']
    return None


def has_own_employee_record():
    """هل لصاحب الجلسة سجلّ موظف يخصّه هو؟

    غير get_portal_employee_id: تلك تُرجع «أول موظف نشط» للمسؤول
    ليستعرض شكل البوابة. فكان الشرط `is_admin and not emp_id` في شاشتَي
    الفريق لا يتحقّق أبدًا — المسؤول يحصل على رقم موظف فيسقط في فرع
    «المرؤوسون المباشرون» ويرى فريق ذلك الموظف لا فريق الشركة، وفرعُ
    المسؤول شيفرةٌ ميتة.
    """
    if session.get('employee_id'):
        return True
    user_id = session.get('user_id')
    if not user_id:
        return False
    row = get_db_connection().execute(
        "SELECT employee_id FROM users WHERE id = ?", (user_id,)).fetchone()
    return bool(row and row['employee_id'])

@portal_bp.route('/')
@portal_bp.route('/dashboard')
@login_required
def dashboard():
    emp_id = get_portal_employee_id()
    conn = get_db_connection()
    
    employee = None
    if emp_id:
        employee = conn.execute('''
            SELECT e.*, e.department as department_name, e.position as position_name,
                   m.name as manager_name
            FROM employees e
            LEFT JOIN employees m ON e.manager_id = m.id
            WHERE e.id = ?
        ''', (emp_id,)).fetchone()
    
    # Check if this employee is a direct manager to others
    is_manager = False
    subordinates_count = 0
    if emp_id:
        cnt_row = conn.execute("SELECT COUNT(*) FROM employees WHERE manager_id = ? AND is_active = 1", (emp_id,)).fetchone()
        subordinates_count = cnt_row[0] if cnt_row else 0
        is_manager = (subordinates_count > 0)
    
    # If global admin without employee, also grant manager view for testing
    user = get_current_user()
    if user and user['role'] == 'admin':
        is_manager = True
    
    leave_types = conn.execute("SELECT id, name, is_hourly_permission FROM leave_types ORDER BY id ASC").fetchall()
    
    from utils.settings_utils import get_portal_attendance_settings
    portal_att = get_portal_attendance_settings(conn)

    # «مندوب» حقلٌ صريح في ملف الموظف (work_mode)، لا استنتاجٌ من كونه
    # أُسنِدت له منطقة: المندوب الذي عُيّن اليوم ولم تُرسم منطقته بعد
    # مندوبٌ أيضًا. ووحدة المناديب اختيارية، فإن كانت مطفأة فلا أحد
    # مندوب مهما قال الحقل.
    is_field_rep = False
    office_punch = True
    try:
        from utils import field as _field
        if emp_id:
            office_punch = _field.punches_at_office(conn, emp_id)
            if _field.module_enabled(conn):
                is_field_rep = _field.is_field_rep(conn, emp_id)
    except Exception:
        is_field_rep = False
        office_punch = True

    # المسح الشامل هنا لا في `bootstrap` وحده.
    #
    # `bootstrap` مسارٌ بُني للتطبيق، والبوابة لا تناديه إطلاقًا —
    # فوضعُ المسح فيه وحده جعله لا يعمل عند أحد: التطبيق لم يُبنَ
    # بعد، والويب لا يمرّ من هناك. وهذه الصفحة هي ما يفتحه الناس
    # فعلًا، فهي موضع المسح.
    try:
        from utils import notifications as _notif
        if emp_id:
            _notif.check_presence_for(conn, emp_id)
        _notif.sweep_presence(conn)
    except Exception:
        pass      # صفحة الموظف لا تسقط لأن تذكيرًا تعثّر

    return render_template(
        'portal/index.html',
        employee=employee,
        is_field_rep=is_field_rep,
        office_punch=office_punch,
        is_manager=is_manager,
        subordinates_count=subordinates_count,
        leave_types=leave_types,
        portal_att=portal_att,
        today_date=date.today().strftime('%Y-%m-%d'),
        today_display=date.today().strftime('%A, %d %B %Y')
    )

@portal_bp.route('/api/bootstrap')
@login_required
def api_bootstrap():
    """ما تعرفه الشاشة عند فتحها: من أنت، وماذا يَظهر لك.

    كل هذا كان يُحقن في `portal/index.html` عند التصيير فحسب — أنواع
    الإجازات، وهل أنت مدير، وهل أنت مندوب، وهل تبصم في المكتب. فما
    يقرأ JSON لا يعرف شيئًا منه، وكان التطبيق سيضطرّ إلى تخمينها أو
    إلى كشط HTML.

    ولا يُعاد هنا شيءٌ جديد: المصادر نفسها التي يقرؤها `dashboard`،
    ليبقى ما يراه التطبيق هو ما تراه الصفحة لا نسخةً تفترق عنها.
    """
    conn = get_db_connection()
    emp_id = get_portal_employee_id()
    user = get_current_user()

    employee = None
    if emp_id:
        row = conn.execute("""
            SELECT e.id, e.name, e.arabic_name, e.employee_number, e.department,
                   e.position, e.hire_date, e.phone, m.name AS manager_name
            FROM employees e
            LEFT JOIN employees m ON e.manager_id = m.id
            WHERE e.id = ?
        """, (emp_id,)).fetchone()
        employee = dict(row) if row else None

    subordinates = 0
    if emp_id:
        subordinates = conn.execute(
            'SELECT COUNT(*) FROM employees WHERE manager_id = ? AND is_active = 1',
            (emp_id,)).fetchone()[0]
    is_manager = subordinates > 0 or bool(user and user['role'] == 'admin')

    leave_types = [dict(r) for r in conn.execute(
        'SELECT id, name, is_hourly_permission FROM leave_types ORDER BY id ASC')]

    from utils.settings_utils import get_portal_attendance_settings
    att = get_portal_attendance_settings(conn)

    try:
        from utils import notifications as _notif
        # الفحص قبل العدّ: لو استحقّ تذكيرٌ الآن، ظهر في العدّاد
        # نفسه بدل أن ينتظر فتحةً ثانية.
        if emp_id:
            _notif.check_presence_for(conn, emp_id)
        # ومسحٌ شامل مخنوق: من لم يفتح شيئًا لا يُذكَّر لو اكتُفي
        # بالفحص الفرديّ — وهو من يحتاج التذكير. فأيّ زميلٍ يفتح
        # البوابة يُنشئ التذكير للجميع.
        _notif.sweep_presence(conn)
        _unread = _notif.unread_count(conn, session.get('user_id'))
    except Exception:
        _unread = 0

    try:
        from utils import presence as _pres
        _presence = _pres.status(conn, emp_id) if emp_id else None
    except Exception:
        _presence = None

    is_field_rep = False
    office_punch = True
    try:
        from utils import field as _field
        if emp_id:
            office_punch = _field.punches_at_office(conn, emp_id)
            if _field.module_enabled(conn):
                is_field_rep = _field.is_field_rep(conn, emp_id)
    except Exception:
        is_field_rep, office_punch = False, True

    return jsonify({
        'success': True,
        'employee': employee,
        # حسابٌ بلا سجلّ موظف يقرأ ولا يكتب. تُقال للتطبيق صراحةً
        # ليُخفي أزرار الكتابة بدل أن يعرضها ثم يردّها الخادم.
        'has_employee_record': employee is not None,
        'is_manager': is_manager,
        'subordinates_count': subordinates,
        'is_field_rep': is_field_rep,
        'office_punch': office_punch,
        'leave_types': leave_types,
        'unread_notifications': _unread,
        'presence': _presence,
        'attendance': {
            'enabled': att['enabled'],
            'geofence_enabled': att['geofence_enabled'],
            'radius': att['radius'],
            'cooldown_minutes': att['cooldown_minutes'],
        },
        'today': date.today().strftime('%Y-%m-%d'),
    })


@portal_bp.route('/api/notifications')
@login_required
def api_notifications():
    """صندوق الإشعارات لهذا الحساب.

    الهدف حسابٌ لا موظف: من يقرأ هو من سجّل دخوله. ولا يُمرَّر رقم
    حسابٍ في الطلب — يُؤخذ من الجلسة، فلا يقرأ أحدٌ صندوق غيره.
    """
    from utils import notifications as notif

    conn = get_db_connection()
    user_id = session.get('user_id')
    unread_only = request.args.get('unread') in ('1', 'true')

    return jsonify({
        'success': True,
        'items': notif.listing(conn, user_id, unread_only=unread_only),
        'unread': notif.unread_count(conn, user_id),
    })


@portal_bp.route('/api/notifications/read', methods=['POST'])
@login_required
def api_notifications_read():
    """يعلّم المقروء: أرقامًا بعينها، أو الكلّ حين لا تُذكر أرقام."""
    from utils import notifications as notif

    conn = get_db_connection()
    user_id = session.get('user_id')
    data = request.get_json(silent=True) or {}
    ids = data.get('ids')

    changed = notif.mark_read(conn, user_id, ids if isinstance(ids, list) else None)
    return jsonify({'success': True, 'changed': changed,
                    'unread': notif.unread_count(conn, user_id)})


@portal_bp.route('/api/my-data')
@login_required
def api_my_data():
    emp_id = get_portal_employee_id()
    if not emp_id:
        return jsonify({'success': False, 'message': 'لا يوجد حساب موظف مرتبط'}), 404
    
    conn = get_db_connection()
    today_str = date.today().strftime('%Y-%m-%d')
    now = datetime.now()
    cur_month = now.month
    cur_year = now.year
    
    # 1. Employee Info
    emp_row = conn.execute('''
        SELECT e.id, e.name, e.arabic_name, e.employee_number, e.department, e.position,
               e.hire_date, e.salary, e.phone, e.national_id as civil_id
        FROM employees e WHERE e.id = ?
    ''', (emp_id,)).fetchone()
    if not emp_row:
        return jsonify({'success': False, 'message': 'الموظف غير موجود'}), 404
    emp = dict(emp_row)
    
    # 2. Today's Punches
    punches = conn.execute('''
        SELECT check_time, check_type, note FROM attendance_records
        WHERE employee_id = ? AND DATE(check_time) = ?
        ORDER BY check_time ASC
    ''', (emp_id, today_str)).fetchall()
    
    punch_times = [p['check_time'][11:16] for p in punches] # 'HH:MM'
    check_in = None
    check_out = None
    presence = None
    
    for p in punches:
        t = p['check_time'][11:16]
        c = p['check_type']
        n = p['note'] or ''
        if c == 2 or 'تواجد' in n:
            if not presence:
                presence = t
        elif c == 1 or 'حضور' in n:
            if not check_in:
                check_in = t
        elif c == 0 or 'انصراف' in n:
            check_out = t
            
    if not check_in and punch_times:
        check_in = punch_times[0]
    if not check_out and len(punch_times) > 1 and punch_times[-1] != presence:
        check_out = punch_times[-1]
        
    is_present = bool(punches)
    
    # 3. Current Month Stats
    month_days_worked = conn.execute('''
        SELECT COUNT(DISTINCT DATE(check_time)) FROM attendance_records
        WHERE employee_id = ? AND strftime('%m', check_time) = ? AND strftime('%Y', check_time) = ?
    ''', (emp_id, f'{cur_month:02d}', str(cur_year))).fetchone()[0]
    
    # 4. Leave Balance
    try:
        bal = calculate_leave_balance(conn, emp_id, cur_month, cur_year)
        annual_balance = bal.get('closing_balance', 0) if bal else 30
        annual_accrued = bal.get('accrued_days', 0) if bal else 30
        annual_taken = bal.get('consumed_days', 0) if bal else 0
    except Exception:
        annual_balance = 30
        annual_accrued = 30
        annual_taken = 0
    
    # 5. Recent Leave Requests (Last 5)
    recent_leaves = conn.execute('''
        SELECT lr.id, lr.start_date, lr.end_date, lr.days_count, lr.status, lr.reason,
               lt.name as leave_type_name
        FROM leave_requests lr
        LEFT JOIN leave_types lt ON lr.leave_type_id = lt.id
        WHERE lr.employee_id = ?
        ORDER BY lr.created_at DESC LIMIT 5
    ''', (emp_id,)).fetchall()
    
    # 6. Active Loans
    from utils.payroll_engine import fetch_employee_loans_detail
    all_loans = fetch_employee_loans_detail(conn, emp_id)
    loans = [l for l in all_loans if l.get('status') == 'active' and (l.get('remaining') or 0) > 0]
    total_loan_remaining = sum(float(l['remaining']) for l in loans) if loans else 0
    monthly_installment = sum(float(l.get('installment') or 0) for l in loans) if loans else 0
    
    return jsonify({
        'success': True,
        'employee': emp,
        'today': {
            'date': today_str,
            'is_present': is_present,
            'check_in': check_in,
            'presence': presence,
            'check_out': check_out,
            'punches_count': len(punches),
            'punches': punch_times
        },
        'stats': {
            'days_worked': month_days_worked,
            'annual_balance': round(annual_balance, 1),
            'annual_accrued': round(annual_accrued, 1),
            'annual_taken': round(annual_taken, 1),
            'loan_remaining': round(total_loan_remaining, 2),
            'monthly_installment': round(monthly_installment, 2)
        },
        'recent_leaves': [dict(r) for r in recent_leaves]
    })

@portal_bp.route('/api/attendance')
@login_required
def api_attendance():
    emp_id = get_portal_employee_id()
    if not emp_id:
        return jsonify({'success': False, 'message': 'لا يوجد موظف'}), 404
    
    conn = get_db_connection()
    now = datetime.now()
    month = request.args.get('month', type=int) or now.month
    year = request.args.get('year', type=int) or now.year
    
    # Fetch punches for this month
    records = conn.execute('''
        SELECT DATE(check_time) as date, GROUP_CONCAT(TIME(check_time)) as punches
        FROM attendance_records
        WHERE employee_id = ? AND strftime('%m', check_time) = ? AND strftime('%Y', check_time) = ?
        GROUP BY DATE(check_time)
        ORDER BY date DESC
    ''', (emp_id, f'{month:02d}', str(year))).fetchall()
    
    data = []
    for r in records:
        all_p = r['punches'].split(',') if r['punches'] else []
        t_in = all_p[0][:5] if all_p else '-'
        t_out = all_p[-1][:5] if len(all_p) > 1 else '-'
        data.append({
            'date': r['date'],
            'check_in': t_in,
            'check_out': t_out,
            'punches_count': len(all_p),
            'status': 'حاضر' if len(all_p) >= 2 else ('بصمة واحدة' if len(all_p) == 1 else 'غياب')
        })
    
    return jsonify({'success': True, 'month': month, 'year': year, 'records': data})

@portal_bp.route('/api/request-leave', methods=['POST'])
@login_required
def api_request_leave():
    emp_id = get_portal_employee_id(for_write=True)
    if not emp_id:
        return jsonify({'success': False, 'message': 'حساب الموظف غير محدد'}), 400
    
    data = request.get_json(silent=True) or request.form
    leave_type_id = data.get('leave_type_id')
    start_date = data.get('start_date')
    end_date = data.get('end_date')
    reason = data.get('reason', '').strip()
    
    if not all([leave_type_id, start_date, end_date]):
        return jsonify({'success': False, 'message': 'يرجى تعبئة كافة الحقول الإلزامية'}), 400

    conn = get_db_connection()

    # ما كان يُفحص شيء من هذا. جرّبتُه على نظام يعمل فقُبلت ثلاثة طلبات
    # لا معنى لها: نهاية قبل بدايةٍ، ونوع إجازة رقمه 99999 لا وجود له،
    # وطلبٌ مكرّر حرفيًا. وكلّها تصل شاشة المدير على أنها طلبات حقيقية.
    try:
        start_dt = datetime.strptime(str(start_date), '%Y-%m-%d')
        end_dt = datetime.strptime(str(end_date), '%Y-%m-%d')
    except ValueError:
        return jsonify({'success': False, 'message': 'صيغة التاريخ غير صحيحة (المطلوب YYYY-MM-DD)'}), 400

    if end_dt < start_dt:
        return jsonify({'success': False,
                        'message': 'تاريخ النهاية قبل تاريخ البداية'}), 400

    if not conn.execute('SELECT 1 FROM leave_types WHERE id = ?',
                        (leave_type_id,)).fetchone():
        return jsonify({'success': False, 'message': 'نوع الإجازة غير موجود'}), 400

    # تداخل مع طلب قائم: الموظف لا يكون في إجازتين معًا، والطلبان
    # المتداخلان يُعتمدان كلاهما فيُخصم الرصيد مرتين عن الأيام نفسها.
    clash = conn.execute('''
        SELECT id, start_date, end_date FROM leave_requests
        WHERE employee_id = ? AND status IN ('pending', 'approved')
          AND DATE(start_date) <= DATE(?) AND DATE(end_date) >= DATE(?)
        LIMIT 1
    ''', (emp_id, end_date, start_date)).fetchone()
    if clash:
        return jsonify({
            'success': False,
            'message': f"يوجد طلب قائم يتداخل مع هذه المدة ({clash['start_date']} — {clash['end_date']})"
        }), 400

    try:
        days_count = calculate_actual_leave_days(conn, start_date, end_date)
        if days_count <= 0:
            days_count = 1
        
        # Check leave balance — start_dt مفكوك ومُتحقَّق منه أعلاه
        bal = calculate_leave_balance(conn, emp_id, start_dt.month, start_dt.year)
        available = bal.get('closing_balance', 0) if bal else 0
        is_paid = (available >= days_count)
        
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO leave_requests (
                employee_id, leave_type_id, start_date, end_date,
                days_count, reason, is_paid_leave, status, current_step
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', 1)
        ''', (emp_id, leave_type_id, start_date, end_date, days_count, reason, is_paid))
        req_id = cursor.lastrowid
        
        # Check if employee has a direct manager
        emp_info = conn.execute("SELECT manager_id FROM employees WHERE id = ?", (emp_id,)).fetchone()
        if emp_info and emp_info['manager_id']:
            cursor.execute('''
                INSERT INTO leave_approvals (leave_request_id, step_order, approver_user_id, status)
                VALUES (?, 1, ?, 'pending')
            ''', (req_id, emp_info['manager_id']))
        
        save_leave_balance(conn, emp_id, start_dt.month, start_dt.year)
        conn.commit()

        # الطلب حُفظ. والإخطار بعده لا قبله، ولا يُبطله إن تعثّر —
        # فالمدير لا يفتح «فريقي» كل ساعة، والطلب كان ينتظر يومين لا
        # لأنه مرفوض بل لأن أحدًا لم يره.
        try:
            from utils import notifications as _notif
            if emp_info and emp_info['manager_id']:
                who = conn.execute('SELECT name FROM employees WHERE id = ?',
                                   (emp_id,)).fetchone()
                lt = conn.execute('SELECT name FROM leave_types WHERE id = ?',
                                  (leave_type_id,)).fetchone()
                _notif.leave_requested(
                    conn, emp_info['manager_id'],
                    who['name'] if who else 'موظف',
                    lt['name'] if lt else 'إجازة',
                    start_date, end_date, days_count, req_id)
        except Exception:
            pass

        return jsonify({'success': True, 'message': 'تم تقديم طلب الإجازة بنجاح وهو بانتظار الاعتماد.'})
    except Exception as e:
        return jsonify({'success': False, 'message': f'حدث خطأ: {e}'}), 500

@portal_bp.route('/api/request-excuse', methods=['POST'])
@login_required
def api_request_excuse():
    emp_id = get_portal_employee_id(for_write=True)
    if not emp_id:
        return jsonify({'success': False, 'message': 'حساب الموظف غير محدد'}), 400
    
    data = request.get_json(silent=True) or request.form
    date_str = data.get('date') or date.today().strftime('%Y-%m-%d')
    type_ = data.get('type', 'mission')
    reason = data.get('reason', '').strip()
    
    conn = get_db_connection()
    try:
        conn.execute('''
            INSERT INTO attendance_excuses (employee_id, date, type, reason, created_by)
            VALUES (?, ?, ?, ?, ?)
        ''', (emp_id, date_str, type_, reason, session.get('user_id')))
        conn.commit()

        try:
            from utils import notifications as _notif
            row = conn.execute(
                'SELECT e.name, e.manager_id FROM employees e WHERE e.id = ?',
                (emp_id,)).fetchone()
            if row and row['manager_id']:
                labels = {'mission': 'مهمة عمل خارجية',
                          'personal': 'استئذان شخصي',
                          'manual_override': 'تصحيح بصمة'}
                _notif.excuse_requested(conn, row['manager_id'], row['name'],
                                        labels.get(type_, 'استئذان'), date_str)
        except Exception:
            pass

        return jsonify({'success': True, 'message': 'تم تسجيل طلب الاستئذان/المهمة بنجاح.'})
    except Exception as e:
        if 'UNIQUE' in str(e):
            return jsonify({'success': False, 'message': 'يوجد استئذان مسجل بالفعل لهذا التاريخ'}), 400
        return jsonify({'success': False, 'message': f'خطأ: {e}'}), 500

# =========================================================================
# DIRECT MANAGER (MY TEAM) ENDPOINTS
# =========================================================================

@portal_bp.route('/api/team-summary')
@login_required
def api_team_summary():
    emp_id = get_portal_employee_id()
    user = get_current_user()
    # لا `not emp_id`: المسؤول يحصل على رقم موظف مستعار من الدالة أعلاه.
    is_admin = (user and user['role'] == 'admin' and not has_own_employee_record())

    conn = get_db_connection()
    today_str = date.today().strftime('%Y-%m-%d')

    if is_admin:
        # Admin gets full active team
        members = conn.execute('''
            SELECT id, name, arabic_name, employee_number, position, department, phone
            FROM employees WHERE is_active = 1 ORDER BY name ASC LIMIT 30
        ''').fetchall()
    else:
        # Direct subordinates only
        members = conn.execute('''
            SELECT id, name, arabic_name, employee_number, position, department, phone
            FROM employees WHERE manager_id = ? AND is_active = 1 ORDER BY name ASC
        ''', (emp_id,)).fetchall()
    
    team_data = []
    present_count = 0
    
    for m in members:
        mid = m['id']
        punches = conn.execute('''
            SELECT check_time FROM attendance_records
            WHERE employee_id = ? AND DATE(check_time) = ?
            ORDER BY check_time ASC
        ''', (mid, today_str)).fetchall()
        
        is_in = bool(punches)
        if is_in:
            present_count += 1
        
        first_punch = punches[0]['check_time'][11:16] if punches else None
        last_punch = punches[-1]['check_time'][11:16] if len(punches) > 1 else None
        
        team_data.append({
            'id': mid,
            'name': m['name'] or m['arabic_name'],
            'employee_number': m['employee_number'],
            'position': m['position'] or 'موظف',
            'department': m['department'] or 'عام',
            'is_present': is_in,
            'first_punch': first_punch,
            'last_punch': last_punch,
            'punches_count': len(punches)
        })
    
    # Count pending approvals
    if is_admin:
        pending_cnt = conn.execute("SELECT COUNT(*) FROM leave_requests WHERE status = 'pending'").fetchone()[0]
    else:
        pending_cnt = conn.execute('''
            SELECT COUNT(*) FROM leave_requests lr
            JOIN employees e ON lr.employee_id = e.id
            WHERE e.manager_id = ? AND lr.status = 'pending'
        ''', (emp_id,)).fetchone()[0]
    
    return jsonify({
        'success': True,
        'total_team': len(team_data),
        'present_today': present_count,
        'absent_today': len(team_data) - present_count,
        'pending_approvals_count': pending_cnt,
        'team_members': team_data
    })

@portal_bp.route('/api/team-approvals')
@login_required
def api_team_approvals():
    emp_id = get_portal_employee_id()
    user = get_current_user()
    is_admin = (user and user['role'] == 'admin' and not has_own_employee_record())

    conn = get_db_connection()

    if is_admin:
        reqs = conn.execute('''
            SELECT lr.id, lr.start_date, lr.end_date, lr.days_count, lr.reason, lr.created_at, lr.status,
                   e.name as employee_name, e.employee_number, lt.name as leave_type_name
            FROM leave_requests lr
            JOIN employees e ON lr.employee_id = e.id
            JOIN leave_types lt ON lr.leave_type_id = lt.id
            WHERE lr.status = 'pending'
            ORDER BY lr.created_at DESC
        ''').fetchall()
    else:
        reqs = conn.execute('''
            SELECT lr.id, lr.start_date, lr.end_date, lr.days_count, lr.reason, lr.created_at, lr.status,
                   e.name as employee_name, e.employee_number, lt.name as leave_type_name
            FROM leave_requests lr
            JOIN employees e ON lr.employee_id = e.id
            JOIN leave_types lt ON lr.leave_type_id = lt.id
            WHERE e.manager_id = ? AND lr.status = 'pending'
            ORDER BY lr.created_at DESC
        ''', (emp_id,)).fetchall()
    
    return jsonify({
        'success': True,
        'requests': [dict(r) for r in reqs]
    })

@portal_bp.route('/api/approve-request', methods=['POST'])
@login_required
def api_approve_request():
    emp_id = get_portal_employee_id()
    user = get_current_user()
    is_admin = (user and user['role'] == 'admin')
    
    data = request.get_json(silent=True) or request.form
    req_id = data.get('request_id')
    action = data.get('action') # 'approve' or 'reject'
    
    if not req_id or action not in ['approve', 'reject']:
        return jsonify({'success': False, 'message': 'بيانات الإجراء غير صحيحة'}), 400
    
    conn = get_db_connection()
    req = conn.execute('''
        SELECT lr.*, e.manager_id
        FROM leave_requests lr
        JOIN employees e ON lr.employee_id = e.id
        WHERE lr.id = ?
    ''', (req_id,)).fetchone()
    
    if not req:
        return jsonify({'success': False, 'message': 'طلب الإجازة غير موجود'}), 404
    
    # الصلاحية: مدير مباشر أو مسؤول نظام.
    #
    # الشرطان الأولان ليسا احتياطًا زائدًا، بل هما الثغرة نفسها: الفحص
    # كان `req['manager_id'] != emp_id` وحده، وفي بايثون `None != None`
    # تساوي False. فحسابٌ غير مرتبط بموظف (emp_id = None) كان يعتمد أي
    # طلب لموظف لا مدير مُسنَد له (manager_id = None) — وهذان حالان
    # شائعان لا نادران. جرّبتُه: الطلب انتقل من pending إلى approved.
    if not is_admin:
        if not emp_id:
            return jsonify({'success': False,
                            'message': 'حسابك غير مرتبط بموظف، فلا يمكن اعتماد الطلبات'}), 403
        if not req['manager_id']:
            return jsonify({'success': False,
                            'message': 'لا مدير مُسنَد لهذا الموظف — الاعتماد لمسؤول النظام'}), 403
        if req['manager_id'] != emp_id:
            return jsonify({'success': False, 'message': 'لا تملك صلاحية اعتماد هذا الطلب'}), 403
    
    new_status = 'approved' if action == 'approve' else 'rejected'
    conn.execute("UPDATE leave_requests SET status = ? WHERE id = ?", (new_status, req_id))
    
    # Update approvals table if exists
    try:
        conn.execute("UPDATE leave_approvals SET status = ? WHERE leave_request_id = ?", (new_status, req_id))
    except Exception:
        pass
    
    conn.commit()

    # صاحب الطلب يُخطَر بالقرار. ومن قرّر لا يُخطَر بقراره هو.
    try:
        from utils import notifications as _notif
        lt = conn.execute('SELECT name FROM leave_types WHERE id = ?',
                          (req['leave_type_id'],)).fetchone()
        _notif.leave_decided(
            conn, req['employee_id'], action == 'approve',
            lt['name'] if lt else 'إجازة',
            req['start_date'], req['end_date'],
            decided_by_user_id=session.get('user_id'), request_id=req_id)
    except Exception:
        pass

    msg = 'تمت الموافقة على الطلب بنجاح ✅' if action == 'approve' else 'تم رفض الطلب ❌'
    return jsonify({'success': True, 'message': msg, 'new_status': new_status})


# =========================================================================
# EMPLOYEE MOBILE GPS ATTENDANCE PUNCH ENDPOINTS
# =========================================================================

@portal_bp.route('/api/punch-status')
@login_required
def api_punch_status():
    emp_id = get_portal_employee_id()
    if not emp_id:
        return jsonify({'success': False, 'message': 'حساب الموظف غير محدد'}), 400
    
    from utils.settings_utils import get_portal_attendance_settings
    conn = get_db_connection()
    stg = get_portal_attendance_settings(conn)
    
    today_str = date.today().strftime('%Y-%m-%d')
    punches = conn.execute('''
        SELECT check_time, source, note, check_type
        FROM attendance_records
        WHERE employee_id = ? AND DATE(check_time) = ?
        ORDER BY check_time ASC
    ''', (emp_id, today_str)).fetchall()
    
    punch_list = []
    has_check_in = False
    has_presence = False
    has_check_out = False
    presence_time = None
    check_in_time = None
    check_out_time = None

    for idx, p in enumerate(punches):
        t = p['check_time'][11:16]
        c_type = p['check_type']
        note = p['note'] or ''

        if c_type == 2 or 'تواجد' in note:
            p_type = 'تواجد'
            has_presence = True
            presence_time = t
        elif c_type == 1 or 'حضور' in note:
            p_type = 'حضور'
            has_check_in = True
            if not check_in_time:
                check_in_time = t
        elif c_type == 0 or 'انصراف' in note:
            p_type = 'انصراف'
            has_check_out = True
            check_out_time = t
        else:
            if idx == 0:
                p_type = 'حضور'
                has_check_in = True
                check_in_time = t
            elif idx == len(punches) - 1:
                p_type = 'انصراف'
                has_check_out = True
                check_out_time = t
            else:
                p_type = 'تواجد'
                has_presence = True
                presence_time = t

        punch_list.append({
            'time': t,
            'full_time': p['check_time'],
            'type': p_type,
            'check_type': c_type,
            'source': p['source'] or 'device',
            'note': note
        })
        
    # الموظف هنا ليبصم. فإن كانت نافذة التواجد مفتوحة ولم يبصم
    # فيها، يُذكَّر الآن لا بعد أن يُخصم في آخر الشهر.
    presence_state = None
    try:
        from utils import notifications as _notif
        presence_state, _ = _notif.check_presence_for(conn, emp_id)
    except Exception:
        presence_state = None

    last_punch = punches[-1]['check_time'] if punches else None
    
    # Suggested next action
    if not has_check_in:
        next_action = 'حضور'
    elif not has_presence:
        next_action = 'تواجد'
    else:
        next_action = 'انصراف'
    
    return jsonify({
        'success': True,
        'enabled': stg['enabled'],
        'geofence_enabled': stg['geofence_enabled'],
        'company_lat': stg['latitude'],
        'company_latitude': stg['latitude'],
        'company_lon': stg['longitude'],
        'company_longitude': stg['longitude'],
        'radius': stg['radius'],
        'geofence_radius_meters': stg['radius'],
        'cooldown_minutes': stg['cooldown_minutes'],
        'today_punches': punch_list,
        'last_punch': last_punch,
        'next_action': next_action,
        'has_check_in': has_check_in,
        'has_presence': has_presence,
        'has_check_out': has_check_out,
        'presence_time': presence_time,
        'is_present': bool(punches),
        # نافذة التواجد وحالها: الشاشة تقول للموظف ماذا يُنتظر منه
        # الآن بدل أن يعرفه من قسيمة راتبه.
        'presence_window': presence_state,
    })

@portal_bp.route('/api/punch', methods=['POST'])
@login_required
def api_punch():
    import math
    emp_id = get_portal_employee_id(for_write=True)
    if not emp_id:
        return jsonify({'success': False, 'message': 'حساب الموظف غير محدد'}), 400
        
    from utils.settings_utils import get_portal_attendance_settings, calculate_haversine_distance
    conn = get_db_connection()
    stg = get_portal_attendance_settings(conn)
    
    # 1. Feature Enabled Check
    if not stg['enabled']:
        return jsonify({
            'success': False,
            'message': 'تسجيل البصمة الذاتية عبر الهاتف غير مفعّل وفق سياسة الشركة. يرجى استخدام جهاز البصمة المكتبي.'
        }), 403
        
    data = request.get_json(silent=True) or request.form
    emp_lat = data.get('latitude')
    emp_lon = data.get('longitude')
    accuracy = data.get('accuracy', 0)
    gps_timestamp = data.get('timestamp')
    device_uuid = (data.get('device_uuid') or '').strip()[:64]
    
    try:
        emp_lat = float(emp_lat) if emp_lat is not None else None
        emp_lon = float(emp_lon) if emp_lon is not None else None
        accuracy = float(accuracy) if accuracy is not None else 0
    except (ValueError, TypeError):
        emp_lat, emp_lon, accuracy = None, None, 0

    # العنوان من utils.net لا من الترويسة مباشرةً: كانت تُقرأ كما وصلت،
    # وهي شيء يكتبه المتصفح. انظر التعليق في ذلك الملف — جرّبتُه وسُجّلت
    # بصمة من خارج الشبكة بترويسة واحدة.
    from utils.net import client_ip as resolve_client_ip, ip_allowed
    client_ip = resolve_client_ip(request, conn)

    # 1. Fetch Employee Branch Info & Active Branches
    emp_row = conn.execute('SELECT branch_location FROM employees WHERE id = ?', (emp_id,)).fetchone()
    emp_branch_name = (emp_row['branch_location'] or '').strip() if emp_row else ''
    
    active_branches = conn.execute('''
        SELECT id, name, location, latitude, longitude, geofence_radius, allowed_ips, wifi_name
        FROM branches WHERE is_active = 1
    ''').fetchall()

    matched_branch_name = None
    distance = None

    # 1.1 Multi-Branch Wi-Fi / IP Restriction Check
    assigned_branch = next((b for b in active_branches if b['name'] and b['name'].strip().lower() == emp_branch_name.lower()), None)
    
    branch_ips = []
    if assigned_branch and assigned_branch['allowed_ips']:
        branch_ips = [ip.strip() for ip in assigned_branch['allowed_ips'].split(',') if ip.strip()]
    elif stg.get('allowed_ips'):
        branch_ips = [ip.strip() for ip in stg['allowed_ips'].split(',') if ip.strip()]
    else:
        all_branch_ips = []
        for b in active_branches:
            if b['allowed_ips']:
                all_branch_ips.extend([ip.strip() for ip in b['allowed_ips'].split(',') if ip.strip()])
        if all_branch_ips:
            branch_ips = all_branch_ips

    if branch_ips:
        # المطابقة على حدود النقاط: 'startswith' وحدها كانت تجعل
        # 10.0.0.1 تقبل 10.0.0.100 — عنوانًا على شبكة أخرى.
        if not ip_allowed(client_ip, branch_ips):
            return jsonify({
                'success': False,
                'message': f'يجب الاتصال بشبكة واي فاي الفرع المعتمدة لتسجيل البصمة (عنوان IP الحالي: {client_ip}).'
            }), 403

    # 2. Multi-Branch Geofence & GPS Verification
    has_any_gps = (stg['geofence_enabled'] and stg['latitude'] is not None and stg['longitude'] is not None) or \
                  any(b['latitude'] is not None and b['longitude'] is not None for b in active_branches)

    if has_any_gps:
        if emp_lat is None or emp_lon is None:
            return jsonify({
                'success': False,
                'message': 'يرجى السماح بصلاحية الوصول إلى موقعك الجغرافي (GPS) للتحقق من تواجدك بمقر العمل.'
            }), 400

        # Anti-Spoofing: Check GPS Timestamp Freshness (prevent stale/delayed replay)
        if gps_timestamp:
            try:
                import time as _time
                gps_ts_sec = float(gps_timestamp) / 1000.0
                server_ts_sec = _time.time()
                if abs(server_ts_sec - gps_ts_sec) > 35:
                    return jsonify({
                        'success': False,
                        'message': 'بيانات الموقع الجغرافي قديمة أو تم حفظها مسبقاً. يرجى الضغط للبصمة اللحظية الآن.'
                    }), 400
            except (ValueError, TypeError):
                pass

        gps_matched = False
        min_distance = float('inf')
        closest_branch_title = ''

        # A. Check assigned branch first
        if assigned_branch and assigned_branch['latitude'] is not None and assigned_branch['longitude'] is not None:
            d = calculate_haversine_distance(emp_lat, emp_lon, assigned_branch['latitude'], assigned_branch['longitude'])
            allowed_r = (assigned_branch['geofence_radius'] or 150) + max(0, min(accuracy, 30))
            if d <= allowed_r:
                gps_matched = True
                distance = d
                matched_branch_name = assigned_branch['name']
            else:
                min_distance = d
                closest_branch_title = assigned_branch['name']

        # B. Check other active branches
        if not gps_matched:
            for b in active_branches:
                if b['latitude'] is not None and b['longitude'] is not None:
                    d = calculate_haversine_distance(emp_lat, emp_lon, b['latitude'], b['longitude'])
                    allowed_r = (b['geofence_radius'] or 150) + max(0, min(accuracy, 30))
                    if d <= allowed_r:
                        gps_matched = True
                        distance = d
                        matched_branch_name = b['name']
                        break
                    elif d < min_distance:
                        min_distance = d
                        closest_branch_title = b['name']

        # C. Check global HQ if configured
        if not gps_matched and stg['latitude'] is not None and stg['longitude'] is not None:
            d = calculate_haversine_distance(emp_lat, emp_lon, stg['latitude'], stg['longitude'])
            allowed_r = stg['radius'] + max(0, min(accuracy, 30))
            if d <= allowed_r:
                gps_matched = True
                distance = d
                matched_branch_name = 'المقر الرئيسي'
            elif d < min_distance:
                min_distance = d
                closest_branch_title = 'المقر الرئيسي'

        if not gps_matched:
            target_desc = f" ({closest_branch_title})" if closest_branch_title else ""
            return jsonify({
                'success': False,
                'message': f'أنت خارج النطاق الجغرافي المسموح به لمقر العمل{target_desc} (المسافة: {int(min_distance)} متراً).'
            }), 400

    # 3. Cooldown Verification (Anti-duplication)
    now = datetime.now()
    cooldown_sec = stg['cooldown_minutes'] * 60
    last_rec = conn.execute('''
        SELECT check_time FROM attendance_records
        WHERE employee_id = ?
        ORDER BY check_time DESC LIMIT 1
    ''', (emp_id,)).fetchone()
    
    if last_rec:
        try:
            last_dt = datetime.strptime(last_rec['check_time'], '%Y-%m-%d %H:%M:%S')
            diff_sec = (now - last_dt).total_seconds()
            if diff_sec < cooldown_sec:
                rem_mins = int(math.ceil((cooldown_sec - diff_sec) / 60))
                return jsonify({
                    'success': False,
                    'message': f'تم تسجيل بصمة مسبقاً قبل قليل. يرجى الانتظار {rem_mins} دقيقة قبل تسجيل بصمة أخرى.'
                }), 400
        except Exception:
            pass

    # 4. Insert Attendance Record
    check_time_str = now.strftime('%Y-%m-%d %H:%M:%S')
    dist_str = f" | مسافة: {int(distance)}م" if distance is not None else ""
    acc_str = f" | دقة: {int(accuracy)}م" if accuracy else ""
    coord_str = f" ({emp_lat:.5f}, {emp_lon:.5f})" if emp_lat and emp_lon else ""
    ip_str = f" | IP: {client_ip}" if client_ip else ""
    dev_str = f" | جهاز: {device_uuid[:8]}" if device_uuid else ""
    
    req_type = (data.get('punch_type') or data.get('action') or '').strip().lower()

    today_str = now.strftime('%Y-%m-%d')
    todays_punches = conn.execute('''
        SELECT check_type, note FROM attendance_records
        WHERE employee_id = ? AND DATE(check_time) = ?
        ORDER BY check_time ASC
    ''', (emp_id, today_str)).fetchall()
    
    if req_type in ('presence', 'تواجد'):
        check_type_code = 2
        punch_label = 'بصمة التواجد'
        punch_action_name = 'تواجد'
    elif req_type in ('check_out', 'out', 'انصراف'):
        check_type_code = 0
        punch_label = 'الانصراف'
        punch_action_name = 'انصراف'
    elif req_type in ('check_in', 'in', 'حضور'):
        check_type_code = 1
        punch_label = 'الحضور'
        punch_action_name = 'حضور'
    else:
        # Automatic fallback based on today's count
        cnt = len(todays_punches)
        if cnt == 0:
            check_type_code = 1
            punch_label = 'الحضور'
            punch_action_name = 'حضور'
        elif cnt == 1:
            check_type_code = 2
            punch_label = 'بصمة التواجد'
            punch_action_name = 'تواجد'
        else:
            check_type_code = 0
            punch_label = 'الانصراف'
            punch_action_name = 'انصراف'

    branch_tag = f" | فرع: {matched_branch_name}" if matched_branch_name else (f" | فرع: {emp_branch_name}" if emp_branch_name else "")
    note_prefix = f"بصمة {punch_label} ذاتية (GPS){branch_tag}"
    note_str = f"{note_prefix}{dist_str}{acc_str}{coord_str}{ip_str}{dev_str}"

    conn.execute('''
        INSERT INTO attendance_records (employee_id, device_id, check_time, check_type, verify_code, source, note, created_at, created_by)
        VALUES (?, 0, ?, ?, 15, 'portal_mobile', ?, CURRENT_TIMESTAMP, ?)
    ''', (emp_id, check_time_str, check_type_code, note_str, session.get('user_id')))
    conn.commit()

    # Optional sync to Oracle if enabled
    try:
        from routes.adms_routes import ORACLE_ENABLED
        if ORACLE_ENABLED:
            from utils.oracle_db import add_to_sync_queue
            emp_row = conn.execute('SELECT employee_number FROM employees WHERE id = ?', (emp_id,)).fetchone()
            u_id = emp_row['employee_number'] if emp_row and emp_row['employee_number'] else str(emp_id)
            add_to_sync_queue(u_id, check_time_str, check_type_code, 15, client_ip, sqlite_conn=conn)
    except Exception:
        pass

    branch_display = f" ({matched_branch_name})" if matched_branch_name else ""
    return jsonify({
        'success': True,
        'message': f'تم تسجيل {punch_label} بنجاح الساعة {check_time_str[11:16]} 🎯{branch_display}',
        'punch_type': punch_action_name,
        'punch_label': punch_label,
        'check_time': check_time_str,
        'distance': round(distance, 1) if distance is not None else None,
        'branch_name': matched_branch_name or emp_branch_name
    })
