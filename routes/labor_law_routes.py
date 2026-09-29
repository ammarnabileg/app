# -*- coding: utf-8 -*-
"""فحصُ المطابقة لقانون العمل الكويتيّ — ما يخالف الآن، ومن أين يُصلَح.

الإعداداتُ القائمة عند العميل لا تُغيَّر من تلقاء التحديث: تغييرُ سياسةِ
خصمٍ يغيّر رواتبَ شهرٍ اعتُمدت ساعاتُه. فتُعرض هنا وفي لافتة الرواتب،
ويُصلحها صاحبُها بقرار.
"""
from flask import Blueprint, render_template

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
    return render_template('labor_law/check.html', checks=checks, ok=ok,
                           total=len(checks))
