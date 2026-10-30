# -*- coding: utf-8 -*-
"""التصفية النهائية: كشف واحد للموظف اللي خدمته بتنتهي.

    آخر راتب (من أوّل الدورة لحد يوم الإنهاء، بنفس محرك الرواتب: الحضور والبدلات والجزاءات
    والبنود المسجّلة في الشهر)
  + مكافأة نهاية الخدمة + رصيد الإجازات + بدل الإنذار     (routes/eos_routes.compute_kuwait_eos)
  − باقي السلف

السلف مابتتخصمش مرتين: القسط اللي نازل في آخر راتب بيتطرح من باقي السلفة قبل ما يتخصم في التصفية.
ولو كشف شهر الإنهاء اتقفل (approved) قبل كده، آخر راتب = اتصرف خلاص، مابيتحسبش تاني.

الحساب على نسخة مؤقتة (SAVEPOINT): تاريخ الإنهاء بيتحط في ملف الموظف عشان المحرك يوقف عنده، وبعدها
بيترجع زي ما كان — المعاينة مابتغيّرش أي حاجة.
"""
from utils.payroll_engine import compute_monthly_payroll, is_period_locked, resolve_period, _parse_d


def _period_of(conn, d):
    """(شهر، سنة) لدورة الرواتب اللي فيها اليوم ده (الدورة ممكن تبدأ يوم 21 مثلًا)."""
    for m, y in ((d.month, d.year), ((d.month % 12) + 1, d.year + (1 if d.month == 12 else 0)),
                 (((d.month - 2) % 12) + 1, d.year - (1 if d.month == 1 else 0))):
        ps, pe = resolve_period(conn, m, y)
        if ps <= d <= pe:
            return m, y, ps, pe
    ps, pe = resolve_period(conn, d.month, d.year)
    return d.month, d.year, ps, pe


def last_salary(conn, employee_id, termination_date):
    d = _parse_d(termination_date)
    m, y, ps, pe = _period_of(conn, d)
    out = {'month': m, 'year': y, 'period_start': ps.isoformat(), 'period_end': termination_date[:10],
           'already_paid': False, 'row': None, 'net': 0.0, 'loan_installments': 0.0}
    if is_period_locked(conn, m, y):
        out['already_paid'] = True
        return out
    conn.execute('SAVEPOINT final_settlement')
    try:
        conn.execute('UPDATE employees SET end_of_service_date = ? WHERE id = ?', (termination_date[:10], employee_id))
        data = compute_monthly_payroll(conn, m, y, employee_id=employee_id)
    finally:
        conn.execute('ROLLBACK TO SAVEPOINT final_settlement')
        conn.execute('RELEASE SAVEPOINT final_settlement')
    row = next((r for r in data['rows'] if r['employee_id'] == employee_id), None)
    if row:
        out['row'] = row
        out['net'] = float(row['net'])
        out['loan_installments'] = round(sum(x['amount'] for x in row['deductions'] if x.get('source') == 'loan'), 3)
    return out


def settle(conn, employee_id, termination_date, reason, **eos_kw):
    """الكشف كله — أو None لو التواريخ غلط."""
    from routes.eos_routes import compute_kuwait_eos
    eos = compute_kuwait_eos(conn, employee_id, termination_date, reason, **eos_kw)
    if not eos:
        return None
    last = last_salary(conn, employee_id, termination_date)
    # القسط اللي نازل في آخر راتب اتحسب هناك: باقي السلف في التصفية = اللي بعده بس.
    loans_after = max(0.0, round(eos['loans_total'] - last['loan_installments'], 3))
    eos_net = round(eos['net'] + eos['loans_total'] - loans_after, 3)
    total = round(last['net'] + eos_net, 3)
    lines = []
    if not last['already_paid']:
        lines.append(('آخر راتب', f"{last['period_start']} ← {last['period_end']}", last['net']))
    else:
        lines.append(('آخر راتب', f"اتصرف في كشف {last['month']}/{last['year']} المقفول", 0.0))
    lines += [('مكافأة نهاية الخدمة', f"{eos['years']:.2f} سنة", eos['gratuity']),
              ('رصيد الإجازات', f"{eos['leave_days']} يوم × {eos['daily_rate']}", eos['leave_amount'])]
    if eos['notice_amount']:
        lines.append(('بدل الإنذار', '', eos['notice_amount']))
    if loans_after:
        lines.append(('باقي السلف', '', -loans_after))
    return {'employee_id': employee_id, 'termination_date': termination_date[:10], 'reason': reason,
            'last_salary': last, 'eos': eos, 'loans_after_last_salary': loans_after,
            'eos_net': eos_net, 'lines': lines, 'total': total}
