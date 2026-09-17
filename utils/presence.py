"""بصمة التواجد: هل هي مطلوبة اليوم، وهل بُصمت؟

نسيانُها يُكلّف مالًا. `presence_penalty` في محرّك الرواتب يخصم
كسرًا من اليوم عن كل مرّة (ربعه افتراضًا)، فموظفٌ نسيها ثلاث مرات
في الشهر خسر ثلاثة أرباع يوم — ولا شيء نبّهه وهو في مكتبه على بعد
خطوات من الجهاز.

**والقاعدة هنا ليست جديدة.** هي قاعدة التقارير نفسها
(`smart_reports`) التي يُحاسَب عليها في الراتب: نافذةٌ في نوع
الشفت، وبصمةٌ داخلها. فلو كُتبت هنا قاعدةٌ ثانية لاختلفت عنها يومًا
— ويصير النظام يُذكّر بشيء ويخصم على غيره.

  * **مطلوبة** إن كان لشفت الموظف `presence_start_time` و
    `presence_end_time`.
  * **مستوفاة** إن وقعت أي بصمة له اليوم داخل النافذة.
  * **فائتة** إن كان حاضرًا اليوم — له بصمةٌ ما — ومضت النافذة بلا
    بصمةٍ فيها. ومن لم يحضر أصلًا غيابُه يُحاسَب غيابًا، ولا
    يُحاسَب مرّتين.
"""

from datetime import datetime

# ماذا يُنتظر من الموظف الآن
STATE_NOT_REQUIRED = 'not_required'   # لا نافذة في شفته
STATE_BEFORE = 'before'               # النافذة لم تبدأ بعد
STATE_DUE = 'due'                     # داخل النافذة ولم يبصم
STATE_DONE = 'done'                   # بُصمت
STATE_MISSED = 'missed'               # مضت النافذة وهو حاضر ولم يبصم
STATE_ABSENT = 'absent'               # لا بصمة له اليوم أصلًا


def window_for(conn, employee_id):
    """(بداية، نهاية) نافذة التواجد كنصّ HH:MM، أو (None, None).

    تُقرأ من نوع الشفت كما يقرؤها التقرير: `employees.shift_type`
    يطابق `shift_types.name`.
    """
    try:
        row = conn.execute('''
            SELECT st.presence_start_time AS ps, st.presence_end_time AS pe
            FROM employees e
            LEFT JOIN shift_types st ON e.shift_type = st.name
            WHERE e.id = ?
        ''', (employee_id,)).fetchone()
    except Exception:
        return None, None
    if not row:
        return None, None
    ps = row['ps'] if hasattr(row, 'keys') else row[0]
    pe = row['pe'] if hasattr(row, 'keys') else row[1]
    return (ps or None), (pe or None)


def _hhmm(text):
    try:
        return datetime.strptime(str(text)[:5], '%H:%M').time()
    except (TypeError, ValueError):
        return None


def status(conn, employee_id, now=None):
    """حال بصمة التواجد اليوم. يعيد قاموسًا لا يرمي.

    {state, start, end, punched_at}
    """
    now = now or datetime.now()
    out = {'state': STATE_NOT_REQUIRED, 'start': None, 'end': None,
           'punched_at': None}

    ps_txt, pe_txt = window_for(conn, employee_id)
    if not ps_txt or not pe_txt:
        return out

    ps, pe = _hhmm(ps_txt), _hhmm(pe_txt)
    if not ps or not pe:
        return out

    out['start'], out['end'] = ps_txt[:5], pe_txt[:5]

    try:
        rows = conn.execute(
            'SELECT check_time FROM attendance_records'
            ' WHERE employee_id = ? AND DATE(check_time) = ?'
            ' ORDER BY check_time ASC',
            (employee_id, now.strftime('%Y-%m-%d'))).fetchall()
    except Exception:
        return out

    times = []
    for r in rows:
        raw = r['check_time'] if hasattr(r, 'keys') else r[0]
        t = _hhmm(str(raw)[11:16])
        if t:
            times.append((t, str(raw)[11:16]))

    if not times:
        # لم يحضر بعدُ. قبل النافذة يبقى «قبلها»، وبعدها غيابٌ
        # يُحاسَب غيابًا لا تواجدًا ناقصًا.
        out['state'] = STATE_BEFORE if now.time() < pe else STATE_ABSENT
        return out

    for t, label in times:
        if ps <= t <= pe:
            out['state'] = STATE_DONE
            out['punched_at'] = label
            return out

    if now.time() < ps:
        out['state'] = STATE_BEFORE
    elif now.time() <= pe:
        out['state'] = STATE_DUE
    else:
        out['state'] = STATE_MISSED
    return out
