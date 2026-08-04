# -*- coding: utf-8 -*-
"""تقرير الأول والأخير — أول دخول وآخر خروج لكل يوم.

## ما يعرضه

لكل موظف ولكل يوم في المدى: أول بصمة، وآخر بصمة، والفارق بينهما، والجهاز
الذي سُجّلت عليه.

## لماذا لا يُعاد استعمال حساب الشفت المقسّم

الشفت المقسّم يجمع فترات العمل ويطرح الراحة. وهذا التقرير يعرض **المدى
الزمني** من أول بصمة إلى آخرها — وهو ما يُطلب في تقارير الحضور المقدَّمة
للجهات: متى حضر ومتى انصرف، لا كم عمل صافيًا.

فالتقريران يجيبان سؤالين مختلفين، وتوحيدهما يفقد أحدهما معناه.

## الحالات المميَّزة بالألوان

    أحمر   بصمة واحدة فقط — لا انصراف مسجَّل
    أصفر   بصم على أكثر من جهاز في اليوم نفسه
    رمادي  لا بصمة — غياب أو إجازة (التقرير لا يفرّق بينهما)

والتمييز الأخير مقصود: التقرير يعرض ما سجّلته الأجهزة لا ما تقرّره سياسة
الإجازات، فلا يُنسب غياب إلى موظف في إجازة معتمدة ولا العكس.
"""
from datetime import date, timedelta

DAY_AR = ['الإثنين', 'الثلاثاء', 'الأربعاء', 'الخميس',
          'الجمعة', 'السبت', 'الأحد']
DAY_EN = ['Monday', 'Tuesday', 'Wednesday', 'Thursday',
          'Friday', 'Saturday', 'Sunday']


def _parse(d):
    if isinstance(d, date):
        return d
    try:
        return date.fromisoformat(str(d)[:10])
    except ValueError:
        return None


def _hhmm(minutes):
    """تنسيق الدقائق ساعاتٍ ودقائق. يتجاوز 24 ساعة في الإجماليات."""
    if minutes is None:
        return '—'
    minutes = int(round(minutes))
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def build_first_last(conn, start, end, employee_ids=None,
                     department=None, include_absent=True):
    """بناء التقرير لمدى تواريخ ولموظف أو أكثر.

    employee_ids=None يعني كل الموظفين النشطين — وهو ما يتيح طباعة الجميع
    دفعةً واحدة بدل فتح تقرير لكل موظف.
    """
    p_start, p_end = _parse(start), _parse(end)
    if not p_start or not p_end or p_end < p_start:
        return None

    q = """SELECT id, employee_number, name, arabic_name, department, position
           FROM employees WHERE COALESCE(is_active, 1) = 1"""
    params = []
    if employee_ids:
        q += ' AND id IN (%s)' % ','.join('?' * len(employee_ids))
        params += list(employee_ids)
    if department:
        q += ' AND department = ?'
        params.append(department)
    q += ' ORDER BY CAST(employee_number AS INTEGER), employee_number'
    employees = [dict(r) for r in conn.execute(q, params).fetchall()]
    if not employees:
        return {'employees': [], 'start': p_start.isoformat(),
                'end': p_end.isoformat(), 'grand': {}}

    emp_ids = [e['id'] for e in employees]
    punches = {}
    rows = conn.execute(
        """SELECT a.employee_id, DATE(a.check_time) AS d, a.check_time,
                  COALESCE(d.device_name, '') AS device
           FROM attendance_records a
           LEFT JOIN fingerprint_devices d ON d.id = a.device_id
           WHERE DATE(a.check_time) BETWEEN ? AND ?
             AND a.employee_id IN (%s)
           ORDER BY a.check_time""" % ','.join('?' * len(emp_ids)),
        [p_start.isoformat(), p_end.isoformat()] + emp_ids).fetchall()
    for r in rows:
        punches.setdefault((r['employee_id'], r['d']), []).append(dict(r))

    out = []
    g_days = g_mins = g_missing = 0
    for emp in employees:
        days = []
        total_mins = 0
        present = missing_out = multi_dev = 0
        d = p_start
        while d <= p_end:
            ds = d.isoformat()
            recs = punches.get((emp['id'], ds), [])
            times = [r['check_time'][11:16] for r in recs]
            devices = sorted({r['device'] for r in recs if r['device']})

            first = times[0] if times else None
            last = times[-1] if len(times) >= 2 else None
            mins = None
            if first and last and last > first:
                fh, fm = map(int, first.split(':'))
                lh, lm = map(int, last.split(':'))
                mins = (lh * 60 + lm) - (fh * 60 + fm)
                total_mins += mins

            if times:
                present += 1
                if len(times) == 1:
                    missing_out += 1
                if len(devices) > 1:
                    multi_dev += 1
                flag = ('single' if len(times) == 1
                        else 'multi_device' if len(devices) > 1 else '')
            else:
                flag = 'absent'

            if times or include_absent:
                days.append({
                    'date': ds,
                    'day_ar': DAY_AR[d.weekday()],
                    'day_en': DAY_EN[d.weekday()],
                    'first_in': first,
                    'last_out': last,
                    'total': _hhmm(mins) if mins is not None else '—',
                    'total_mins': mins,
                    'devices': devices,
                    'device': ' / '.join(devices) if devices else '',
                    'punch_count': len(times),
                    'flag': flag,
                })
            d += timedelta(days=1)

        out.append({
            'employee': emp,
            'days': days,
            'summary': {
                'present_days': present,
                'missing_out_days': missing_out,
                'multi_device_days': multi_dev,
                'total': _hhmm(total_mins),
                'total_mins': total_mins,
            },
        })
        g_days += present
        g_mins += total_mins
        g_missing += missing_out

    return {
        'employees': out,
        'start': p_start.isoformat(),
        'end': p_end.isoformat(),
        'grand': {
            'employee_count': len(out),
            'present_days': g_days,
            'missing_out_days': g_missing,
            'total': _hhmm(g_mins),
            'total_mins': g_mins,
        },
        'generated_at': date.today().isoformat(),
    }
