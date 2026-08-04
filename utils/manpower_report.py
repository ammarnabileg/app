# -*- coding: utf-8 -*-
"""كشف قوى عاملة المقاولات — تقرير وزارة الكهرباء والماء.

## مصدر القواعد

القواعد أدناه مستخرَجة من كشوف العميل الفعلية ومُتحقَّق منها عليها، لا
مفترضة:

    اليومية      = الراتب الشهري ÷ 26
    أجر الساعة   = اليومية ÷ 8
    الأساسي      = الشهري × (أيام الحضور ÷ 26)
    إضافي عادي   = ساعات × أجر الساعة × 1.25
    إضافي جمعة   = ساعات × أجر الساعة × 1.50
    إضافي عطلة   = ساعات × أجر الساعة × 2.00
    الإجمالي     = الأساسي + الإضافيات الثلاثة

ومعاملات الإضافي مطابقة لقانون العمل الكويتي.

## لماذا لا يُعاد استعمال محرّك الرواتب

محرّك الرواتب يحسب صافيًا بعد الاستقطاعات والسلف والجزاءات. وهذا الكشف
مستحقٌّ تعاقديّ يُقدَّم للوزارة: يعرض ما تدفعه الوزارة للمقاول عن كل عامل،
لا ما يقبضه العامل. فخلطهما يجعل استقطاعًا على موظف يخفض مطالبة المقاول.

## المعاملات لكل عقد لا عامة

كل عقد له معاملاته وأساسه (26 يومًا، 8 ساعات). تُقرأ من صفّ العقد لا من
إعدادات النظام العامة، لأن النظام يخدم عملاء لا يعملون جميعًا بهذا الكشف.
"""
from datetime import date


def get_contract(conn, contract_id):
    row = conn.execute(
        'SELECT * FROM manpower_contracts WHERE id = ?', (contract_id,)).fetchone()
    return dict(row) if row else None


def list_contracts(conn, active_only=True):
    q = 'SELECT * FROM manpower_contracts'
    if active_only:
        q += ' WHERE is_active = 1'
    q += ' ORDER BY company_name'
    return [dict(r) for r in conn.execute(q).fetchall()]


def _f(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def build_report(conn, contract_id, month, year, department=None):
    """بناء الكشف لشهر وسنة.

    يُرجع dict فيه العقد والصفوف والإجماليات — جاهزًا للعرض أو التصدير.
    """
    contract = get_contract(conn, contract_id)
    if not contract:
        return None

    basis = int(contract.get('days_basis') or 26)
    hpd = int(contract.get('hours_per_day') or 8)
    p_normal = _f(contract.get('ot_normal'), 1.25)
    p_friday = _f(contract.get('ot_friday'), 1.5)
    p_holiday = _f(contract.get('ot_holiday'), 2.0)

    # الحضور والعمل الإضافي من جدول اعتماد الساعات: هو المصدر الذي
    # يعتمده كشف الرواتب نفسه، فلا يختلف الكشفان في يوم أو ساعة. وقراءة
    # الأرقام من مكان آخر تعني كشفين متعارضين عن الشهر ذاته.
    by_emp = {}
    try:
        for r in conn.execute(
                '''SELECT employee_id, actual_days, ot_weekday_mins,
                          ot_weekend_mins, ot_holiday_mins
                   FROM payroll_hours_approvals
                   WHERE month = ? AND year = ?''', (month, year)).fetchall():
            by_emp[r['employee_id']] = dict(r)
    except Exception:
        pass

    q = """SELECT id, employee_number, name, arabic_name, position,
                  department, salary
           FROM employees
           WHERE COALESCE(is_active, 1) = 1"""
    params = []
    if department:
        q += ' AND department = ?'
        params.append(department)
    q += ' ORDER BY CAST(employee_number AS INTEGER)'

    rows = []
    for i, e in enumerate(conn.execute(q, params).fetchall(), start=1):
        emp = dict(e)
        pr = by_emp.get(emp['id'], {})

        monthly = _f(emp.get('salary'))
        daily = round(monthly / basis, 6) if basis else 0.0
        hourly = round(daily / hpd, 6) if hpd else 0.0

        days_present = _f(pr.get('actual_days'), 0.0)
        # الأساسي يتناسب مع أيام الحضور: عاملٌ حضر نصف الشهر يستحقّ نصفه،
        # والوزارة تدفع بالحضور لا بالتعاقد.
        basic = round(monthly * (days_present / basis), 3) if basis else 0.0

        # الدقائق تُحوّل إلى ساعات: الجدول يخزّنها دقائق لدقّة التقريب،
        # والكشف يعرضها ساعات كما في نموذج الوزارة.
        ot_n = round(_f(pr.get('ot_weekday_mins')) / 60.0, 2)
        ot_f = round(_f(pr.get('ot_weekend_mins')) / 60.0, 2)
        ot_h = round(_f(pr.get('ot_holiday_mins')) / 60.0, 2)

        t_n = round(ot_n * hourly * p_normal, 3)
        t_f = round(ot_f * hourly * p_friday, 3)
        t_h = round(ot_h * hourly * p_holiday, 3)

        rows.append({
            'item': i,
            'sl_no': emp.get('employee_number'),
            'name': emp.get('name') or emp.get('arabic_name'),
            'arabic_name': emp.get('arabic_name'),
            'designation': emp.get('position'),
            'monthly_salary': round(monthly, 3),
            'daily_salary': daily,
            'days_present': days_present,
            'basic_total': basic,
            'ot_normal_hours': ot_n, 'ot_normal_rate': hourly,
            'ot_normal_pct': p_normal, 'ot_normal_total': t_n,
            'ot_friday_hours': ot_f, 'ot_friday_rate': hourly,
            'ot_friday_pct': p_friday, 'ot_friday_total': t_f,
            'ot_holiday_hours': ot_h, 'ot_holiday_rate': hourly,
            'ot_holiday_pct': p_holiday, 'ot_holiday_total': t_h,
            'ot_total': round(t_n + t_f + t_h, 3),
            'grand_total': round(basic + t_n + t_f + t_h, 3),
        })

    totals = {
        'count': len(rows),
        'basic': round(sum(r['basic_total'] for r in rows), 3),
        'ot_normal': round(sum(r['ot_normal_total'] for r in rows), 3),
        'ot_friday': round(sum(r['ot_friday_total'] for r in rows), 3),
        'ot_holiday': round(sum(r['ot_holiday_total'] for r in rows), 3),
        'ot_total': round(sum(r['ot_total'] for r in rows), 3),
        'grand_total': round(sum(r['grand_total'] for r in rows), 3),
    }

    MONTHS_EN = ['', 'JANUARY', 'FEBRUARY', 'MARCH', 'APRIL', 'MAY', 'JUNE',
                 'JULY', 'AUGUST', 'SEPTEMBER', 'OCTOBER', 'NOVEMBER', 'DECEMBER']
    MONTHS_AR = ['', 'يناير', 'فبراير', 'مارس', 'أبريل', 'مايو', 'يونيو',
                 'يوليو', 'أغسطس', 'سبتمبر', 'أكتوبر', 'نوفمبر', 'ديسمبر']

    return {
        'contract': contract,
        'rows': rows,
        'totals': totals,
        'month': month,
        'year': year,
        'month_en': MONTHS_EN[month] if 1 <= month <= 12 else '',
        'month_ar': MONTHS_AR[month] if 1 <= month <= 12 else '',
        'generated_at': date.today().isoformat(),
    }
