from functools import wraps
from flask import session, flash, redirect, url_for
from utils.license import get_current_license_info

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('يجب تسجيل الدخول أولاً', 'error')
            return redirect(url_for('auth.login'))

        # الحساب ما زال قائمًا ونشطًا؟
        #
        # كان الفحص على وجود user_id في الجلسة وحده. والدخول يشترط
        # is_active = 1، لكنه يُفحص مرةً واحدة عند الدخول فقط — فمن
        # أُوقف حسابه بعدها تبقى جلسته عاملة إلى أن تنتهي من نفسها.
        # جرّبتُه على نظام يعمل: أوقفتُ الحساب فبقي يقرأ بياناته،
        # وسجّل بصمة حضور بعد الإيقاف. والموظف المنتهية خدمته هو أوّل
        # من يُوقَف حسابه وآخر من يُنتبه إليه.
        if not _session_user_is_active():
            session.clear()
            flash('انتهت صلاحية الجلسة. يرجى تسجيل الدخول من جديد.', 'error')
            return redirect(url_for('auth.login'))

        # Optional: Check license here too as fallback
        license_info = get_current_license_info()
        if not license_info.get('ok', False):
            from flask import request
            if request.endpoint != 'main.license_page':
                return redirect(url_for('main.license_page'))
                
        return f(*args, **kwargs)
    return decorated_function

from utils.db import get_db_connection


def _session_user_is_active():
    """هل صاحب الجلسة حسابٌ قائم ونشط الآن؟

    الفشل مفتوح عن قصد عند تعذّر القراءة: خطأ في القاعدة لا ينبغي أن
    يُخرج كل من في النظام. والفحص نفسه يبقى على الحالة الطبيعية.
    """
    uid = session.get('user_id')
    if not uid:
        return False
    try:
        conn = get_db_connection()
        row = conn.execute('SELECT is_active FROM users WHERE id = ?', (uid,)).fetchone()
    except Exception:
        return True
    if row is None:
        return False          # حساب حُذف
    active = row[0] if not hasattr(row, 'keys') else row['is_active']
    return bool(active)


def get_current_user():
    """Fetch the current user from the database."""
    if 'user_id' not in session:
        return None
    conn = get_db_connection()
    try:
        user = conn.execute('SELECT * FROM users WHERE id = ?', (session['user_id'],)).fetchone()
        return user
    finally:
        pass # conn.close() removed to prevent leak in Flask g

def is_global_admin():
    """Returns True if the user is a global admin (no specific department)."""
    user = get_current_user()
    if not user:
        return False
    # If the user has a specific role like 'department_manager' and a managed_department_id, they are not global admin
    if user['role'] == 'department_manager' and user['managed_department_id']:
        return False
    return True

def get_allowed_department_name():
    """Returns the managed department name if user is a department manager, else None."""
    user = get_current_user()
    if not user:
        return None
    
    if user['role'] == 'department_manager' and user['managed_department_id']:
        conn = get_db_connection()
        try:
            dept = conn.execute('SELECT name FROM departments_master WHERE id = ?', (user['managed_department_id'],)).fetchone()
            if dept:
                return dept['name']
        finally:
            pass # conn.close() removed to prevent leak in Flask g
            
    return None
