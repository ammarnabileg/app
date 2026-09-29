# -*- coding: utf-8 -*-
"""فحصُ المطابقة لقانون العمل الكويتيّ — ما يخالف الآن، ومن أين يُصلَح.

الإعداداتُ القائمة عند العميل لا تُغيَّر من تلقاء التحديث: تغييرُ سياسةِ
خصمٍ يغيّر رواتبَ شهرٍ اعتُمدت ساعاتُه. فتُعرض هنا وفي لافتة الرواتب،
ويُصلحها صاحبُها بقرار.
"""
from datetime import datetime

from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_babel import gettext

from utils.auth import login_required
from utils.db import get_db_connection
from utils.rbac import require_permission

labor_law_bp = Blueprint('labor_law', __name__)

# أين يُصلَح كلُّ فحص.
FIX_ENDPOINTS = {
    'ot_rounding': 'main.settings', 'weekday_ot_multiplier': 'main.settings',
    'weekend_ot_multiplier': 'main.settings', 'holiday_ot_multiplier': 'main.settings',
    'penalty_cap': 'main.settings', 'missing_punch': 'main.settings',
    'pifss': 'main.settings', 'min_wage': 'employee.employees',
    'shift_hours': 'shift.index', 'shift_break': 'shift.index',
    'holidays': 'leave.official_holidays',
    'weekly_rest': 'employee.employees', 'week_48': 'shift.index',
    'ramadan': 'labor_law.check', 'loan_cap': 'main.settings',
}


@labor_law_bp.route('/settings/labor-law')
@login_required
@require_permission('page.settings')
def check():
    from flask import url_for
    from utils.labor_law import compliance_checks
    checks = compliance_checks(get_db_connection())
    for c in checks:
        try:
            c['fix_url'] = url_for(FIX_ENDPOINTS[c['code']])
        except Exception:
            c['fix_url'] = None
    ok = sum(1 for c in checks if c['ok'])
    ramadan = get_db_connection().execute(
        'SELECT * FROM ramadan_periods ORDER BY start_date DESC').fetchall()
    return render_template('labor_law/check.html', checks=checks, ok=ok,
                           total=len(checks), ramadan=ramadan)


@labor_law_bp.route('/settings/labor-law/ramadan', methods=['POST'])
@login_required
@require_permission('admin.settings')
def add_ramadan():
    """رمضانُ يُعلَن كلَّ سنة، فيُدخَل بيد: 6 ساعات في اليوم (المادة 64)."""
    try:
        a = datetime.strptime(request.form.get('start_date', ''), '%Y-%m-%d').date()
        b = datetime.strptime(request.form.get('end_date', ''), '%Y-%m-%d').date()
    except ValueError:
        flash(gettext('x.f_invalid_dates'), 'error')
        return redirect(url_for('labor_law.check'))
    if b < a or (b - a).days > 30:
        flash(gettext('x.f_ramadan_bad_range'), 'error')
        return redirect(url_for('labor_law.check'))
    conn = get_db_connection()
    if conn.execute('SELECT 1 FROM ramadan_periods WHERE NOT (end_date < ? OR start_date > ?)',
                    (a.isoformat(), b.isoformat())).fetchone():
        flash(gettext('x.f_ramadan_overlap'), 'error')
        return redirect(url_for('labor_law.check'))
    conn.execute('INSERT INTO ramadan_periods (start_date, end_date) VALUES (?, ?)',
                 (a.isoformat(), b.isoformat()))
    conn.commit()
    flash(gettext('x.f_ramadan_saved'), 'success')
    return redirect(url_for('labor_law.check'))


@labor_law_bp.route('/settings/labor-law/ramadan/<int:rid>/delete', methods=['POST'])
@login_required
@require_permission('admin.settings')
def delete_ramadan(rid):
    conn = get_db_connection()
    conn.execute('DELETE FROM ramadan_periods WHERE id = ?', (rid,))
    conn.commit()
    return redirect(url_for('labor_law.check'))
