# -*- coding: utf-8 -*-
"""قانون العمل الكويتيّ 6/2010 — ما يطبّقه النظام، في موضعٍ واحد.

كلُّ رقمٍ قانونيّ هنا لا في الشاشات ولا في المحرّك: الحدُّ الأدنى للأجر،
ومهلةُ الإنذار، وحدودُ الإضافيّ، والإجازاتُ الخاصّة. وما يُكتب في المحرّك
يقرأ من هنا، فلا يفترق رقمان لقاعدةٍ واحدة.

## ما يُمنع وما يُنبَّه عليه

- **الأجرُ لا يسقط:** ساعةٌ اشتُغلت تُدفع. سقفُ الإضافيّ الشهريّ وحدودُ
  المادة 66 (ساعتان في اليوم، 3 أيام في الأسبوع، 180 ساعة في السنة) قيدٌ
  على صاحب العمل **ألّا يُشغِّل** — لا رخصةٌ ألّا يدفع. فتظهر تنبيهًا.
- **الجزاءاتُ مسقوفة:** ما يزيد على الوقت الفعليّ (ربعُ يومٍ عن تأخير 13
  دقيقة، ويومٌ كاملٌ عن بصمةٍ ناقصة) جزاءٌ لا أجرُ وقتٍ لم يُعمل، ومجموعُه
  في الشهر لا يتجاوز أجرَ `penalty_monthly_cap_days` يومًا (5 افتراضًا).
- **الإجازاتُ الخاصّة** (الحج، الوضع، الوفاة، العدّة، الراحة البديلة)
  مدفوعةٌ بنوعها لا برصيد السنويّة، ولها حدودٌ لا تُتجاوز.
"""
from datetime import date, datetime, timedelta

MIN_WAGE = 75.0                 # د.ك — القرار الوزاريّ للقطاع الأهليّ
NOTICE_DAYS = 90                # المادة 44: ثلاثة أشهر للأجر الشهريّ
PROBATION_MAX_WORKDAYS = 100    # المادة 32
ANNUAL_WAIT_MONTHS = 9          # المادة 70: لا إجازةَ عن السنة الأولى قبل 9 أشهر
DAY_MAX_HOURS = 8               # المادة 64
WEEK_MAX_HOURS = 48
BREAK_AFTER_HOURS = 5           # المادة 65: راحةٌ ساعةً بعد 5 ساعاتٍ متّصلة
BREAK_MIN_MINUTES = 60
OT_DAY_MAX_MINS = 120           # المادة 66
OT_WEEK_MAX_DAYS = 3
OT_YEAR_MAX_HOURS = 180
OT_YEAR_MAX_DAYS = 90
RAMADAN_DAY_HOURS = 6           # المادة 64: 36 ساعة في الأسبوع في رمضان
RAMADAN_WEEK_HOURS = 36
NURSING_MINUTES = 120           # المادة 25: ساعتا رضاعة في اليوم
LOAN_CAP_PCT = 10.0             # المادة 60: لا يُقتطع للقرض أكثر من 10% من الأجر
DAILY_EOS_DAYS = (10, 15)       # المادة 51 (أ): لأصحاب الأجر اليوميّ وما في حكمه
DAILY_EOS_CAP_DAYS = 312        # «أجرُ سنة»: 12 شهرًا × 26 يومًا
MIN_MULTIPLIERS = {'weekday_ot_multiplier': 1.25,   # المادة 66
                   'weekend_ot_multiplier': 1.5,    # المادة 67
                   'holiday_ot_multiplier': 2.0}    # المادة 68

# الإجازاتُ الخاصّة: مدفوعةٌ بنوعها، ولا تُخصم من رصيد السنويّة.
SPECIAL_LEAVES = {
    'hajj': {'max_days': 21, 'once': True, 'min_service_months': 24},
    'maternity': {'max_days': 70, 'female': True},
    'bereavement': {'max_days': 3},
    'iddah': {'max_days': 130, 'max_days_non_muslim': 21, 'female': True},
    'comp_rest': {'balance': True},
}

SEED_LEAVE_TYPES = (
    ('إجازة الحج', 'hajj', 21),
    ('إجازة وضع', 'maternity', 70),
    ('إجازة وفاة قريب', 'bereavement', 3),
    ('إجازة عدّة', 'iddah', 130),
    ('راحة بديلة', 'comp_rest', 0),
)

LEAVE_KINDS = ('annual', 'sick', 'emergency', 'unpaid', 'hourly', 'hajj',
               'maternity', 'bereavement', 'iddah', 'comp_rest', 'other')

# أعيادٌ بتاريخٍ ثابت. والهجريّةُ يعلنها ديوانُ الخدمة كلَّ سنة فتُدخَل بيد.
FIXED_HOLIDAYS = (('01-01', 'رأس السنة الميلادية', 'رسمية'),
                  ('02-25', 'العيد الوطني', 'وطنية'),
                  ('02-26', 'عيد التحرير', 'وطنية'))
HOLIDAYS_PER_YEAR = 13          # ما يُعطَّل في الكويت عادةً، بالهجريّة

KUWAITI = {'kuwait', 'kuwaiti', 'kw', 'kwt', 'كويت', 'كويتي', 'كويتية',
           'الكويت'}
MUSLIM = {'muslim', 'islam', 'مسلم', 'مسلمة', 'الإسلام', 'اسلام', 'إسلام'}
MALE = {'male', 'm', 'ذكر'}
FEMALE = {'female', 'f', 'أنثى', 'انثى'}


def is_kuwaiti(nationality):
    return (nationality or '').strip().lower() in KUWAITI


def _norm(v):
    return (v or '').strip().lower()


def _d(v):
    if isinstance(v, date):
        return v
    try:
        return datetime.strptime(str(v)[:10], '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return None


def months_between(a, b):
    """أشهرُ الخدمة الكاملة من a إلى b."""
    if not a or not b or b < a:
        return 0
    m = (b.year - a.year) * 12 + (b.month - a.month)
    if b.day < a.day:
        m -= 1
    return max(0, m)


def _setting(conn, key, default=''):
    try:
        r = conn.execute('SELECT setting_value FROM salary_settings_v2 '
                         'WHERE setting_name = ?', (key,)).fetchone()
        return r[0] if r and r[0] is not None else default
    except Exception:
        return default


def _num(v, default):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


# ------------------------------------------------------------------ الترحيل

def infer_leave_kind(row):
    """نوعُ الإجازة القانونيّ من اسمها — لقواعدَ سبقت عمود `law_kind`."""
    keys = row.keys()
    if 'is_hourly_permission' in keys and row['is_hourly_permission']:
        return 'hourly'
    n = _norm(row['name'])
    for hints, kind in ((('مرض', 'sick'), 'sick'),
                        (('بدون راتب', 'بدون أجر', 'unpaid'), 'unpaid'),
                        (('حج', 'hajj'), 'hajj'),
                        (('وضع', 'أمومة', 'امومة', 'maternity'), 'maternity'),
                        (('عدة', 'عدّة', 'iddah'), 'iddah'),
                        (('وفاة', 'bereavement'), 'bereavement'),
                        (('راحة بديلة', 'بديلة', 'comp'), 'comp_rest'),
                        (('سنوي', 'annual'), 'annual'),
                        (('طارئ', 'emergency'), 'emergency')):
        if any(h in n for h in hints):
            return kind
    return 'other'


def deducts_annual(kind):
    """الخصمُ من رصيد السنويّة: السنويّةُ والطارئةُ وما عرّفه العميلُ بنفسه
    (كما كان)؛ لا المرضيّةُ ولا غيرُ المدفوعة ولا الخاصّةُ القانونيّة."""
    return 1 if kind in ('annual', 'emergency', 'other') else 0


def migrate(conn):
    """يُستدعى من init_db في كلّ إقلاع — وكلُّ خطوةٍ فيه لا تتكرّر."""
    cur = conn.cursor()

    def cols(t):
        return {r[1] for r in cur.execute(f'PRAGMA table_info({t})').fetchall()}

    for table, col, ddl in (('leave_types', 'law_kind', 'TEXT'),
                            ('leave_types', 'deducts_annual', 'INTEGER'),
                            ('shift_types', 'break_minutes', 'INTEGER DEFAULT 0'),
                            ('employees', 'probation_end_date', 'DATE'),
                            ('employees', 'nursing_until', 'DATE'),
                            ('employees', 'pay_type', "TEXT DEFAULT 'monthly'"),
                            ('payroll_hours_approvals', 'ot_days', 'INTEGER')):
        try:
            if col not in cols(table):
                cur.execute(f'ALTER TABLE {table} ADD COLUMN {col} {ddl}')
        except Exception as e:
            print(f'labor_law migrate {table}.{col}: {e}')

    cur.execute('''CREATE TABLE IF NOT EXISTS ramadan_periods (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        start_date DATE NOT NULL,
        end_date DATE NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP)''')

    # تصنيفُ أنواع الإجازات القائمة — مرّةً لكلّ نوع (law_kind فارغ).
    for r in cur.execute('SELECT * FROM leave_types WHERE law_kind IS NULL').fetchall():
        kind = infer_leave_kind(r)
        cur.execute('UPDATE leave_types SET law_kind = ?, deducts_annual = ? WHERE id = ?',
                    (kind, deducts_annual(kind), r['id']))
        if kind == 'unpaid':
            # كانت تُزرع «مدفوعة» فتُدفع حين يكفي الرصيد.
            cur.execute('UPDATE leave_types SET is_paid = 0 WHERE id = ?', (r['id'],))
        if kind == 'annual':
            cur.execute('UPDATE leave_types SET service_months_required = ? '
                        'WHERE id = ? AND COALESCE(service_months_required, 0) = 0',
                        (ANNUAL_WAIT_MONTHS, r['id']))

    have = {r[0] for r in cur.execute('SELECT law_kind FROM leave_types').fetchall()}
    for name, kind, days in SEED_LEAVE_TYPES:
        if kind in have:
            continue
        cur.execute('INSERT OR IGNORE INTO leave_types (name, days_per_year, is_paid, '
                    'requires_approval, law_kind, deducts_annual) VALUES (?, ?, 1, 1, ?, 0)',
                    (name, days, kind))
        cur.execute('UPDATE leave_types SET law_kind = ?, deducts_annual = 0, is_paid = 1 '
                    'WHERE name = ?', (kind, name))

    defaults = (
        ('penalty_monthly_cap_days', '5', 'سقف الجزاءات الشهري (أيام أجر)', 'attendance'),
        ('pifss_enabled', '0', 'خصم التأمينات الاجتماعية للكويتي', 'insurance'),
        ('pifss_employee_pct', '10.5', 'حصة الموظف في التأمينات (%)', 'insurance'),
        ('pifss_employer_pct', '11.5', 'حصة صاحب العمل في التأمينات (%)', 'insurance'),
        ('pifss_salary_cap', '2750', 'سقف الراتب الخاضع للتأمينات (د.ك)', 'insurance'),
        ('loan_deduction_cap_pct', '10', 'أقصى ما يُقتطع للسلف من الأجر (%)', 'payroll'),
        # الراحةُ البديلة تُحتسب من يوم التحديث لا بأثرٍ رجعيّ: سنواتٌ من
        # الأرشيف لا يُعرف ما عُوِّض منها بيدٍ خارج النظام.
        ('comp_rest_since', date.today().isoformat(), 'بداية احتساب الراحة البديلة', 'leave'),
    )
    for k, v, desc, cat in defaults:
        cur.execute('INSERT OR IGNORE INTO salary_settings_v2 (setting_name, setting_value, '
                    'description, category) VALUES (?, ?, ?, ?)', (k, v, desc, cat))

    seed_fixed_holidays(conn)


def seed_fixed_holidays(conn, today=None):
    """الأعيادُ الثابتة للسنة الجارية والقادمة — مرّةً لكلّ سنة. فمن حذف
    عيدًا (نُقل بقرار) لا يعود إليه في الإقلاع التالي.

    **ولا يُزرع ماضٍ:** عيدٌ يُضاف إلى شهرٍ مضى يغيّر أيّامَه المطلوبة فيُبطل
    اعتمادَ ساعاته — والشهرُ دُفع وانتهى."""
    today = today or date.today()
    cur = conn.cursor()
    for year in (today.year, today.year + 1):
        mark = f'law_holidays_seeded_{year}'
        if cur.execute('SELECT 1 FROM salary_settings_v2 WHERE setting_name = ?',
                       (mark,)).fetchone():
            continue
        for md, name, kind in FIXED_HOLIDAYS:
            ds = f'{year}-{md}'
            if ds < today.isoformat():
                continue
            if not cur.execute('SELECT 1 FROM official_holidays WHERE DATE(date) = ?',
                               (ds,)).fetchone():
                cur.execute('INSERT INTO official_holidays (name, date, type, is_paid) '
                            'VALUES (?, ?, ?, 1)', (name, ds, kind))
        cur.execute('INSERT INTO salary_settings_v2 (setting_name, setting_value, category) '
                    "VALUES (?, '1', 'system')", (mark,))


# ------------------------------------------------------------------ الإجازات

def leave_kind(lt):
    keys = lt.keys()
    k = lt['law_kind'] if 'law_kind' in keys else None
    return k if k in LEAVE_KINDS else infer_leave_kind(lt)


def comp_rest_balance(conn, employee_id, as_of=None, exclude_request_id=None):
    """أيامُ الراحة البديلة: كلُّ يوم راحةٍ أسبوعيّة أو عطلةٍ رسميّة اشتُغل
    (بصمتا دخولٍ وخروج) منذ `comp_rest_since`، ناقصًا ما أُخذ منها."""
    from utils.payroll_engine import _weekly_off_set
    emp = conn.execute('SELECT * FROM employees WHERE id = ?', (employee_id,)).fetchone()
    if not emp:
        return {'earned': 0, 'taken': 0.0, 'balance': 0.0}
    as_of = _d(as_of) or date.today()
    since = _d(_setting(conn, 'comp_rest_since')) or as_of
    hire = _d(emp['hire_date'])
    if hire and hire > since:
        since = hire
    offs = _weekly_off_set(emp)
    holidays = {str(r[0])[:10] for r in conn.execute(
        'SELECT date FROM official_holidays WHERE DATE(date) BETWEEN ? AND ?',
        (since.isoformat(), as_of.isoformat()))}
    earned = 0
    for r in conn.execute('''SELECT DATE(check_time) AS d, MIN(check_time) AS f,
                                    MAX(check_time) AS l
                             FROM attendance_records
                             WHERE employee_id = ? AND DATE(check_time) BETWEEN ? AND ?
                             GROUP BY DATE(check_time)''',
                          (employee_id, since.isoformat(), as_of.isoformat())):
        if not r['l'] or str(r['l'])[11:16] <= str(r['f'])[11:16]:
            continue
        d = _d(r['d'])
        if (d.isoweekday() % 7) in offs or r['d'] in holidays:
            earned += 1
    taken = conn.execute('''SELECT COALESCE(SUM(lr.days_count), 0)
                            FROM leave_requests lr JOIN leave_types lt ON lt.id = lr.leave_type_id
                            WHERE lr.employee_id = ? AND lt.law_kind = 'comp_rest'
                              AND lr.status IN ('approved', 'pending')
                              AND COALESCE(lr.is_deleted, 0) = 0
                              AND lr.id != ?''',
                         (employee_id, exclude_request_id or -1)).fetchone()[0]
    taken = round(float(taken or 0), 2)
    return {'earned': earned, 'taken': taken, 'balance': round(earned - taken, 2),
            'since': since.isoformat()}


def leave_decision(conn, employee_id, lt, start_date, end_date, days_count,
                   portal=False, exclude_request_id=None):
    """هل الإجازةُ مدفوعة؟ وهل تُرفض؟

    يعيد {'paid': bool, 'error': (مفتاح, معاملات) | None,
          'warning': (مفتاح, معاملات) | None}.

    - الخاصّةُ القانونيّة مدفوعةٌ بنوعها — والتجاوزُ يُرفض (موظّفًا أو مديرًا).
    - السنويّةُ قبل 9 أشهر: يُرفض طلبُ الموظّف؛ والمديرُ صاحبُ العمل يمنحها
      سلفةً إن شاء، فينبَّه ولا يُمنع.
    - ما يُخصم من رصيد السنويّة مدفوعٌ إن كفى الرصيد — كما كان.
    """
    out = {'paid': True, 'error': None, 'warning': None}
    kind = leave_kind(lt)
    keys = lt.keys()
    s, e = _d(start_date), _d(end_date)
    emp = conn.execute('SELECT * FROM employees WHERE id = ?', (employee_id,)).fetchone()
    if not emp or not s or not e:
        return out
    ek = emp.keys()
    gender = _norm(emp['gender'] if 'gender' in ek else '')
    cal_days = (e - s).days + 1

    if kind == 'hourly' or kind == 'sick':
        return out
    if kind == 'unpaid':
        out['paid'] = False
        return out

    rule = SPECIAL_LEAVES.get(kind)
    if rule:
        if rule.get('female') and gender in MALE:
            out['error'] = ('x.lv_err_female_only', {})
            return out
        max_days = rule.get('max_days')
        if kind == 'iddah':
            rel = _norm(emp['religion'] if 'religion' in ek else '')
            if rel and rel not in MUSLIM:
                max_days = rule['max_days_non_muslim']
        if max_days and cal_days > max_days:
            out['error'] = ('x.lv_err_max_days', {'n': max_days})
            return out
        if kind == 'hajj':
            hire = _d(emp['hire_date'])
            # تاريخُ تعيينٍ مجهول لا يُقرأ «صفرَ خدمة»: لا يُرفض بما لا يُعرف.
            if hire and months_between(hire, s) < rule['min_service_months']:
                out['error'] = ('x.lv_err_hajj_service', {})
                return out
            q = ('SELECT 1 FROM leave_requests lr JOIN leave_types lt ON lt.id = lr.leave_type_id '
                 "WHERE lr.employee_id = ? AND lt.law_kind = 'hajj' "
                 "AND lr.status IN ('approved', 'pending') AND COALESCE(lr.is_deleted, 0) = 0")
            args = [employee_id]
            if exclude_request_id:
                q += ' AND lr.id != ?'
                args.append(exclude_request_id)
            if conn.execute(q, args).fetchone():
                out['error'] = ('x.lv_err_hajj_once', {})
                return out
        if kind == 'comp_rest':
            bal = comp_rest_balance(conn, employee_id, s, exclude_request_id)['balance']
            if float(days_count or 0) > bal:
                out['error'] = ('x.lv_err_comp_balance', {'n': bal})
                return out
        return out

    if 'is_paid' in keys and lt['is_paid'] is not None and not lt['is_paid']:
        out['paid'] = False
        return out

    need = int((lt['service_months_required'] if 'service_months_required' in keys else 0) or 0)
    if kind == 'annual':
        need = max(need, ANNUAL_WAIT_MONTHS)
    hire = _d(emp['hire_date'])
    if need and hire and months_between(hire, s) < need:
        if portal:
            out['error'] = ('x.lv_err_service_months', {'n': need})
            return out
        out['warning'] = ('x.lv_warn_service_months', {'n': need})

    da = lt['deducts_annual'] if 'deducts_annual' in keys else None
    if da is None:
        da = deducts_annual(kind)
    if da:
        from utils.leave_balance import compute_leave_balance
        bal = compute_leave_balance(conn, employee_id, as_of=s)
        avail = bal['balance'] if bal else 0.0
        if exclude_request_id and bal:
            # الطلبُ المعدَّل نفسُه محسوبٌ في المأخوذ إن كان معتمدًا مدفوعًا.
            own = conn.execute('''SELECT days_count FROM leave_requests
                                  WHERE id = ? AND status = 'approved'
                                    AND COALESCE(is_paid_leave, 1) = 1
                                    AND COALESCE(is_deleted, 0) = 0
                                    AND COALESCE(leave_duration_type, 'full_day') != 'hourly'
                                    AND DATE(start_date) >= ?''',
                               (exclude_request_id, bal['effective_start'])).fetchone()
            if own:
                avail += float(own[0] or 0)
        if avail < float(days_count or 0):
            out['paid'] = False
            out['warning'] = out['warning'] or ('x.lv_warn_unpaid_balance', {'n': avail})
    return out


# ------------------------------------------------------------------ التأمينات

def pifss_shares(wage, sal):
    """حصّتا الموظّف وصاحب العمل على الأجر الخاضع (مسقوفًا)."""
    base = min(max(0.0, float(wage or 0)), _num(sal.get('pifss_salary_cap'), 2750.0) or 1e12)
    emp = base * _num(sal.get('pifss_employee_pct'), 10.5) / 100.0
    er = base * _num(sal.get('pifss_employer_pct'), 11.5) / 100.0
    return round(base, 3), round(emp, 3), round(er, 3)


# ------------------------------------------------------------------ الإنذار والتجربة

def notice_amount(reason, monthly_wage, served_days, waived=False, in_probation=False):
    """بدلُ الإنذار (المادة 44): من أنهى العقدَ دون مهلةٍ كاملة يدفع للآخر
    أجرَ ما نقص منها. صاحبُ العمل حين يفصل (موجب)، والمستقيلُ (سالب) إلّا
    أن يتنازل صاحبُ العمل. ولا إنذارَ في التجربة ولا في المادتين 41 و48
    ولا في انتهاء العقد أو الوفاة أو العجز أو التقاعد."""
    if in_probation or served_days is None:
        return 0.0, 0
    try:
        served = max(0, int(float(served_days)))
    except (TypeError, ValueError):
        return 0.0, 0
    short = max(0, NOTICE_DAYS - served)
    amount = short * float(monthly_wage or 0) / 30.0
    if reason == 'termination':
        return round(amount, 3), short
    if reason == 'resignation' and not waived:
        return round(-amount, 3), short
    return 0.0, 0


def probation_limit(conn, emp, hire=None):
    """آخرُ يومٍ تجوز فيه التجربة: 100 يوم عمل من التعيين (بلا أيّام
    الراحة الأسبوعيّة ولا العطل الرسميّة)."""
    from utils.payroll_engine import _weekly_off_set
    hire = _d(hire or emp['hire_date'])
    if not hire:
        return None
    offs = _weekly_off_set(emp)
    hol = {str(r[0])[:10] for r in conn.execute(
        'SELECT date FROM official_holidays WHERE DATE(date) >= ?', (hire.isoformat(),))}
    d, n = hire, 0
    while True:
        if (d.isoweekday() % 7) not in offs and d.isoformat() not in hol:
            n += 1
            if n >= PROBATION_MAX_WORKDAYS:
                return d
        d += timedelta(days=1)


def in_probation(emp, on_date):
    ek = emp.keys()
    pe = _d(emp['probation_end_date'] if 'probation_end_date' in ek else None)
    return bool(pe and _d(on_date) and _d(on_date) <= pe)


# ------------------------------------------------------------------ الإضافيّ

def ot_year_to_date_hours(conn, employee_id, month, year):
    """ساعاتُ إضافيّ أيّام العمل المعتمدة في الأشهر السابقة من السنة."""
    try:
        v = conn.execute('SELECT COALESCE(SUM(ot_weekday_mins), 0) FROM payroll_hours_approvals '
                         'WHERE employee_id = ? AND year = ? AND month < ?',
                         (employee_id, int(year), int(month))).fetchone()[0]
        return float(v or 0) / 60.0
    except Exception:
        return 0.0


# ------------------------------------------------------------------ الفحص

def compliance_checks(conn, sal=None):
    """ما يخالف القانونَ في الإعدادات والبيانات الآن — للشاشة واللافتة.

    كلُّ عنصر: {'code', 'ok', 'article', 'title', 'detail'}؛ النصوصُ
    مفاتيحُ ترجمة ومعاملاتها في 'params'."""
    from utils.settings_utils import get_salary_settings_v2
    sal = sal or get_salary_settings_v2(conn)
    checks = []

    def add(code, ok, article, params=None):
        checks.append({'code': code, 'ok': bool(ok), 'article': article,
                       'params': params or {}})

    step = int(_num(sal.get('overtime_round_to_minutes'), 0))
    add('ot_rounding', step <= 1, '66', {'n': step})
    for k, lo in MIN_MULTIPLIERS.items():
        add(k, _num(sal.get(k), lo) >= lo, {'weekday_ot_multiplier': '66',
                                            'weekend_ot_multiplier': '67',
                                            'holiday_ot_multiplier': '68'}[k],
            {'n': sal.get(k), 'min': lo})
    cap = _num(sal.get('penalty_monthly_cap_days'), 5)
    add('penalty_cap', 0 < cap <= 5, '—', {'n': cap})
    add('missing_punch', sal.get('missing_punch_policy') == 'penalty_tiered', '—',
        {'p': sal.get('missing_punch_policy')})

    kw = [r for r in conn.execute('SELECT nationality FROM employees WHERE is_active = 1')
          if is_kuwaiti(r[0])]
    add('pifss', not kw or str(sal.get('pifss_enabled')) == '1', 'PIFSS', {'n': len(kw)})

    low = conn.execute('SELECT COUNT(*) FROM employees WHERE is_active = 1 '
                       'AND COALESCE(salary, 0) > 0 AND salary < ?', (MIN_WAGE,)).fetchone()[0]
    add('min_wage', low == 0, '—', {'n': low, 'min': MIN_WAGE})

    long_shifts, no_break = [], []
    try:
        for r in conn.execute('SELECT name, start_time, end_time, hours_per_day, '
                              'COALESCE(break_minutes, 0) AS brk FROM shift_types '
                              'WHERE COALESCE(is_active, 1) = 1'):
            if _num(r['hours_per_day'], 8) > DAY_MAX_HOURS:
                long_shifts.append(r['name'])
            span = shift_span_minutes(r['start_time'], r['end_time'])
            if span > BREAK_AFTER_HOURS * 60 and int(r['brk'] or 0) < BREAK_MIN_MINUTES:
                no_break.append(r['name'])
    except Exception:
        pass
    add('shift_hours', not long_shifts, '64', {'names': '، '.join(long_shifts)})
    add('shift_break', not no_break, '65', {'names': '، '.join(no_break)})

    no_rest, over48 = [], []
    try:
        from utils.payroll_engine import _weekly_off_set
        for e in conn.execute('SELECT e.*, COALESCE(st.hours_per_day, 8) AS hpd FROM employees e '
                              'LEFT JOIN shift_types st ON st.name = e.shift_type '
                              'WHERE e.is_active = 1'):
            offs = _weekly_off_set(e)
            if not offs:
                no_rest.append(e['name'])
            if _num(e['hpd'], 8) * (7 - len(offs)) > WEEK_MAX_HOURS:
                over48.append(e['name'])
    except Exception:
        pass
    add('weekly_rest', not no_rest, '67', {'n': len(no_rest), 'names': '، '.join(no_rest[:5])})
    add('week_48', not over48, '64', {'n': len(over48), 'names': '، '.join(over48[:5])})

    today = date.today()
    try:
        ram = conn.execute('SELECT COUNT(*) FROM ramadan_periods WHERE end_date >= ? '
                           "OR strftime('%Y', start_date) = ?",
                           (today.isoformat(), str(today.year))).fetchone()[0]
    except Exception:
        ram = 0
    add('ramadan', ram > 0, '64', {'y': today.year})

    lc = _num(sal.get('loan_deduction_cap_pct'), LOAN_CAP_PCT)
    add('loan_cap', 0 < lc <= LOAN_CAP_PCT, '60', {'n': lc})

    y = date.today().year
    nh = conn.execute("SELECT COUNT(*) FROM official_holidays WHERE strftime('%Y', date) = ?",
                      (str(y),)).fetchone()[0]
    add('holidays', nh >= HOLIDAYS_PER_YEAR, '68', {'n': nh, 'y': y, 'min': HOLIDAYS_PER_YEAR})
    return checks


def shift_span_minutes(start, end):
    try:
        sh, sm = map(int, str(start)[:5].split(':'))
        eh, em = map(int, str(end)[:5].split(':'))
    except (TypeError, ValueError):
        return 0
    span = (eh * 60 + em) - (sh * 60 + sm)
    return span + 24 * 60 if span < 0 else span


def net_span_hours(span_h, break_minutes):
    """الساعاتُ الفعليّة بلا الاستراحة (المادة 65: لا تُحسب من ساعات العمل)
    — تُطرح حين يتجاوز اليومُ خمسَ ساعات، ولا تنزل تحت الخمس."""
    brk = max(0, int(break_minutes or 0)) / 60.0
    if not span_h or span_h <= BREAK_AFTER_HOURS or not brk:
        return span_h
    return max(float(BREAK_AFTER_HOURS), span_h - brk)


def decision_message(pair):
    """(مفتاح، معاملات) → نصٌّ مترجَم."""
    if not pair:
        return ''
    from flask_babel import gettext
    key, params = pair
    return gettext(key) % params if params else gettext(key)


def save_shift_break(conn, shift_id, raw):
    """يحفظ دقائقَ الاستراحة (إن أُرسلت) ويعيد تنبيهات المادتين 64 و65
    نصوصًا مترجمة. التنبيهُ لا يمنع الحفظ: الشفتُ قد يكون مُعتمدًا
    باستثناءٍ (رمضان، أو عملٌ متقطّع بطبيعته)."""
    if raw is not None and str(raw).strip() != '':
        try:
            brk = max(0, min(int(float(raw)), 600))
        except (TypeError, ValueError):
            brk = 0
        conn.execute('UPDATE shift_types SET break_minutes = ? WHERE id = ?', (brk, shift_id))
    r = conn.execute('SELECT start_time, end_time, hours_per_day, COALESCE(break_minutes, 0) AS brk, '
                     'COALESCE(is_split, 0) AS is_split FROM shift_types WHERE id = ?',
                     (shift_id,)).fetchone()
    out = []
    if not r:
        return out
    if _num(r['hours_per_day'], 8) > DAY_MAX_HOURS:
        out.append(('x.sw_over_8', {'n': DAY_MAX_HOURS}))
    if not r['is_split'] and shift_span_minutes(r['start_time'], r['end_time']) > BREAK_AFTER_HOURS * 60 \
            and int(r['brk'] or 0) < BREAK_MIN_MINUTES:
        out.append(('x.sw_no_break', {'n': BREAK_MIN_MINUTES}))
    return [decision_message(w) for w in out]


# ------------------------------------------------------------------ رمضان والرضاعة

def ramadan_dates(conn, start, end):
    """أيّامُ رمضان المُدخلة بين تاريخين (نصوص ISO)."""
    out = set()
    try:
        rows = conn.execute('SELECT start_date, end_date FROM ramadan_periods '
                            'WHERE NOT (end_date < ? OR start_date > ?)',
                            (start.isoformat(), end.isoformat())).fetchall()
    except Exception:
        return out
    for r in rows:
        a, b = _d(r[0]), _d(r[1])
        if not a or not b:
            continue
        d = max(a, start)
        while d <= min(b, end):
            out.add(d.isoformat())
            d += timedelta(days=1)
    return out


def nursing_on(emp, d):
    """ساعتا الرضاعة (المادة 25): للعاملة حتى `nursing_until`."""
    ek = emp.keys()
    until = _d(emp['nursing_until'] if 'nursing_until' in ek else None)
    if not until or _d(d) is None or _d(d) > until:
        return False
    return _norm(emp['gender'] if 'gender' in ek else '') not in MALE


def nursing_waive(late, early):
    """تُعفى ساعتا الرضاعة من التأخير أوّلًا ثم من الانصراف المبكر."""
    left = NURSING_MINUTES
    w = min(late, left)
    late, left = late - w, left - w
    w = min(early, left)
    return late, early - w


def ot_days_year_to_date(conn, employee_id, month, year):
    try:
        v = conn.execute('SELECT COALESCE(SUM(ot_days), 0) FROM payroll_hours_approvals '
                         'WHERE employee_id = ? AND year = ? AND month < ?',
                         (employee_id, int(year), int(month))).fetchone()[0]
        return int(v or 0)
    except Exception:
        return 0


# ------------------------------------------------------------------ السلف

def cap_loan_installments(items, wage, sal):
    """يُخفِّض أقساطَ السلف ليبقى مجموعها ضمن النسبة من الأجر (المادة 60).
    الباقي يبقى على السلفة للأشهر التالية. يعيد المقدارَ المؤجَّل."""
    pct = _num(sal.get('loan_deduction_cap_pct'), LOAN_CAP_PCT)
    if pct <= 0 or not items:
        return 0.0
    cap = max(0.0, float(wage or 0)) * pct / 100.0
    total = sum(i['amount'] for i in items)
    if total <= cap + 0.0005:
        return 0.0
    left = cap
    for i in items:
        take = round(min(i['amount'], left), 3)
        i['deferred'] = round(i['amount'] - take, 3)
        i['amount'] = take
        left -= take
    return round(total - cap, 3)


# ------------------------------------------------------------------ المرضيّة داخل السنويّة

SICK_ANNUAL_SQL = """SELECT lr.start_date, lr.end_date, lt.name,
                            COALESCE(lt.deducts_annual, 1) AS da,
                            COALESCE(lr.is_paid_leave, 1) AS paid
                     FROM leave_requests lr JOIN leave_types lt ON lt.id = lr.leave_type_id
                     WHERE lr.employee_id = ? AND lr.status = 'approved'
                       AND COALESCE(lr.is_deleted, 0) = 0
                       AND COALESCE(lr.leave_duration_type, 'full_day') != 'hourly'
                       AND DATE(lr.end_date) >= ? AND DATE(lr.start_date) <= ?"""


def sick_inside_annual_days(conn, employee_id, since, until):
    """أيّامُ عملٍ مرضيّة وقعت داخل إجازةٍ سنويّة (المادة 70): لا تُحسب من
    السنويّة، فتُعاد إلى الرصيد."""
    from utils.payroll_engine import _weekly_off_set, _is_sick_type
    emp = conn.execute('SELECT * FROM employees WHERE id = ?', (employee_id,)).fetchone()
    if not emp:
        return 0
    sick, annual = [], []
    for r in conn.execute(SICK_ANNUAL_SQL, (employee_id, since, until)).fetchall():
        a, b = _d(r['start_date']), _d(r['end_date'])
        if not a or not b:
            continue
        if _is_sick_type(r['name']):
            sick.append((a, b))
        elif r['da'] and r['paid'] and a >= _d(since):
            annual.append((a, b))
    if not sick or not annual:
        return 0
    offs = _weekly_off_set(emp)
    hol = {str(x[0])[:10] for x in conn.execute(
        'SELECT date FROM official_holidays WHERE DATE(date) BETWEEN ? AND ?', (since, until))}
    days = set()
    for sa, sb in sick:
        for aa, ab in annual:
            d = max(sa, aa)
            while d <= min(sb, ab):
                if (d.isoweekday() % 7) not in offs and d.isoformat() not in hol:
                    days.add(d)
                d += timedelta(days=1)
    return len(days)


def daily_divisor(conn, year, month):
    """قاسمُ الأجر اليوميّ من `daily_rate_basis`: 26 (أساسُ القانون) أو 30،
    و«أيّامُ العمل» تؤول هنا إلى أيّام الشهر."""
    import calendar
    basis = str(_setting(conn, 'daily_rate_basis', '26') or '26').strip()
    if basis == '30':
        return 30.0
    if basis == 'working':
        return float(calendar.monthrange(int(year), int(month))[1])
    return 26.0
