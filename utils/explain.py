# -*- coding: utf-8 -*-
"""«كيف اتحسبت؟» ليوم واحد لموظف: التصنيف وسببه، والبصمات، والتأخير والإضافي — والسياسات اللي اتطبقت
ومصدرها وتاريخ سريانها. من نفس حساب كشف الرواتب (compute_employee_days + utils/policies)، مش حساب تاني.
"""
from datetime import datetime


_STATUS = {'present': 'حاضر', 'partial': 'بصمة ناقصة', 'absent': 'غياب', 'weekly_off': 'راحة أسبوعية',
           'holiday': 'عطلة رسمية', 'leave_sick': 'إجازة مرضية', 'leave_paid': 'إجازة مدفوعة',
           'leave_unpaid': 'إجازة بدون راتب', 'excused': 'عذر', 'not_hired': 'قبل التعيين', 'ended': 'بعد نهاية الخدمة'}


def explain_day(conn, employee_id, day):
    from utils import payroll_engine as pe
    from utils.policies import load
    d = datetime.strptime(str(day)[:10], '%Y-%m-%d').date()
    month, year = d.month, d.year
    ps, pend = pe.resolve_period(conn, month, year)
    if not (ps <= d <= pend):
        month, year = (d.month % 12) + 1, d.year + (1 if d.month == 12 else 0)
    data = pe.compute_employee_days(conn, int(employee_id), month, year)
    row = next((x for x in data.get('days', []) if x['date'] == d.isoformat()), None)
    if row is None:
        return None
    emp = pe.fetch_payroll_employees(conn, employee_id=int(employee_id), active_only=False)[0]
    P = load(conn)
    src = lambda k: pe.policy_source(P, k, emp, d)
    lines = []
    st = row['status']
    reason = _STATUS.get(st, st)
    if st == 'weekly_off':
        reason += ' — من أيام الراحة السارية في اليوم ده (تاريخ أيام الراحة أو ملف الموظف)'
        if row.get('in_leave'):
            reason += f" — داخل فترة إجازة «{row.get('leave_type') or ''}» ومابتتحسبش منها (سياسة النوع)"
    elif st == 'holiday':
        reason += f" — {row.get('holiday_name') or ''}"
        if row.get('in_leave'):
            reason += f" — داخل فترة إجازة «{row.get('leave_type') or ''}»"
    elif st.startswith('leave'):
        reason += f" — «{row.get('leave_type') or ''}»"
    lines.append(('التصنيف', reason))
    if st.startswith('leave') or row.get('in_leave'):
        k = row.get('leave_kind') or 'other'
        for key in ('leave_day_basis', 'leave_excludes_rest', 'leave_excludes_holidays', 'leave_punch_policy'):
            v, s_, r = P.resolve(key, emp['id'], d, leave_kind=k)
            lines.append(('سياسة الإجازة', pe.policy_source(P, key, emp, d) if s_ != 'leave_kind' else
                          f"{key}: {v} — سياسة نوع الإجازة من {r['effective_from']}"))
    lines.append(('الشفت', f"{row.get('shift_name') or emp['shift_name'] or '—'} — الشفت الساري في اليوم ده (تاريخ الشفتات)"))
    if row.get('punch_count'):
        fmt = lambda t: t.strftime('%H:%M') if hasattr(t, 'strftime') else str(t)
        lines.append(('البصمات', f"دخول {fmt(row.get('check_in'))} · خروج {fmt(row.get('check_out'))} · "
                                 f"{float(row.get('hours') or 0):g} ساعة · {row['punch_count']} بصمة"))
    if row.get('late_mins'):
        lines.append(('التأخير', f"{row['late_mins']} دقيقة محسوبة" +
                      (f" (الفعلي {row['late_actual']} دقيقة)" if row.get('late_actual') and row['late_actual'] != row['late_mins'] else '')))
        lines += [('سياسة', src('grace_late_minutes')), ('سياسة', src('late_arrival_policy')),
                  ('سياسة', src('late_count_from'))]
    if row.get('early_mins'):
        lines.append(('الانصراف بدري', f"{row['early_mins']} دقيقة محسوبة"))
        lines += [('سياسة', src('early_departure_grace_minutes')), ('سياسة', src('early_departure_policy'))]
    if row.get('ot_mins'):
        lines.append(('الإضافي', f"{row['ot_mins']} دقيقة ({ {'weekday': 'يوم عادي', 'weekend': 'راحة', 'holiday': 'عطلة'}.get(row.get('ot_kind'), '') })"))
        lines += [('سياسة', src('overtime_round_to_minutes')), ('سياسة', src('overtime_min_minutes')),
                  ('سياسة', src('overtime_requires_approval')), ('سياسة', src('overtime_net_of_late'))]
    if row.get('presence_missing'):
        lines.append(('التواجد', 'بصمة التواجد ناقصة — ' + src('presence_missing_policy')))
    return {'employee_id': int(employee_id), 'date': d.isoformat(), 'status': st, 'lines': lines}
