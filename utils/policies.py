# -*- coding: utf-8 -*-
"""سياسات الحضور والرواتب بتاريخ سريان (الخطوة ٢ — docs/PAYROLL_POLICY_INVENTORY.md).

المعادلات في الكود؛ **القيم والاختيارات** هنا. كل قيمة ليها تاريخ سريان، ونطاق:

| الأولوية | النطاق | `scope` |
|---|---|---|
| ١ | استثناء معتمد خاص بالموظف | `employee` (scope_id = رقم الموظف) |
| ٢ | مجموعة / فرع (لو مفعّلة) | `group` (scope_id = اسم المجموعة، من `employees.branch_location`) |
| ٣ | سياسة الشركة | `company` |
| ٤ | الإعداد القديم في صفحة الإعدادات (`salary_settings_v2`) — ساري «من البداية» | انتقالي |
| ٥ | الافتراضي في الكود (`REGISTRY`) | `system` |

يعني من غير ولا سطر هنا كل حاجة زي ما هي بالظبط (٤ و٥). وأول ما الشركة تسجّل قيمة بتاريخ،
الأيام من التاريخ ده بتاخدها، واللي قبله على القديم.

## القواعد
- **السجل مابيتعدلش:** القيمة الجديدة سطر جديد. «الإلغاء» = `revoked_at` مع السبب (السطر يفضل).
- **يوم بيوم:** `resolve(key, employee, day)` — المحرك بيسأل عن كل يوم بقيمته (التأخير، السماح، …).
- **نسخة مع الكشف المعتمد:** `snapshot()` بيتحفظ في `payroll_runs.policy_snapshot` وقت الاعتماد.
- **أثر رجعي:** `impact()` بيطلع الأشهر المفتوحة اللي محتاجة إعادة حساب، والمقفولة اللي مش هتتغيّر.
- **القيم القانونية** (`legal=True`) بتتعرض «افتراضي — يحتاج مراجعة واعتماد».
"""
import json
from datetime import date, datetime

BASE_DATE = '0001-01-01'
SCOPES = ('employee', 'group', 'company')

SECTIONS = [
    ('wage', 'الأجر والراتب'),
    ('attendance', 'الشفتات والحضور'),
    ('late', 'التأخير والانصراف بدري'),
    ('overtime', 'الإضافي'),
    ('leave', 'الإجازات'),
    ('deductions', 'الخصومات والسلف'),
    ('insurance', 'التأمينات'),
]

# key: (القسم، الاسم، النوع، الاختيارات، الافتراضي في الكود، قانوني؟، يوم بيوم؟)
# «يوم بيوم» = المحرك بيقراها لكل يوم (فتغييرها في نص الشهر بيتطبق من يومه).
_R = [
    # --- الأجر
    ('daily_rate_basis', 'wage', 'قاسم الأجر اليومي', 'choice',
     [('26', '26 يوم'), ('30', '30 يوم'), ('working', 'أيام الجدول'), ('custom', 'رقم تحدده الشركة')], '26', False, False),
    ('daily_rate_custom_divisor', 'wage', 'القاسم المخصص (لو «رقم تحدده الشركة»)', 'number', None, '26', False, False),
    ('hourly_rate_basis', 'wage', 'أساس أجر الساعة', 'choice',
     [('shift', 'ساعات شفت اليوم'), ('fixed', 'عدد ساعات ثابت')], 'shift', False, False),
    ('hourly_rate_fixed_hours', 'wage', 'ساعات اليوم لأجر الساعة (لو «ثابت»)', 'number', None, '8', False, False),
    ('salary_day_basis', 'wage', 'الراتب المستخدم في خصم/إضافي اليوم', 'choice',
     [('period_average', 'متوسط راتب الدورة'), ('day', 'الراتب الساري في اليوم نفسه')], 'period_average', False, False),
    ('late_wage_base', 'wage', 'أساس خصم التأخير والانصراف', 'choice',
     [('basic', 'الأساسي بس'), ('wage', 'الأساسي + البدلات الثابتة')], 'basic', False, False),
    ('overtime_wage_base', 'wage', 'أساس أجر الإضافي', 'choice',
     [('basic', 'الأساسي بس'), ('wage', 'الأساسي + البدلات الثابتة')], 'basic', False, False),
    ('loan_cap_wage_base', 'wage', 'الأجر لحد أقساط السلف', 'choice',
     [('wage', 'الأساسي + البدلات الثابتة'), ('basic', 'الأساسي بس')], 'wage', False, False),
    ('pifss_wage_base', 'wage', 'الأجر الخاضع للتأمينات', 'choice',
     [('wage', 'الأساسي + البدلات الثابتة'), ('basic', 'الأساسي بس')], 'wage', True, False),
    ('absence_deduction_base', 'wage', 'أساس خصم الغياب', 'choice',
     [('wage', 'الأساسي + البدلات الثابتة'), ('basic', 'الأساسي بس')], 'wage', False, False),
    ('rounding_display_decimals', 'wage', 'علامات عشرية للمبالغ', 'number', None, '2', False, False),
    # --- الحضور
    ('default_hours_per_day', 'attendance', 'ساعات اليوم لو الموظف مالوش شفت', 'number', None, '8', False, False),
    ('default_weekly_off', 'attendance', 'أيام الراحة لو مش متسجلة للموظف', 'choice',
     [('5,6', 'الجمعة والسبت'), ('5', 'الجمعة بس'), ('6,0', 'السبت والأحد'), ('0', 'الأحد بس'), ('4,5', 'الخميس والجمعة')],
     '5,6', False, False),
    ('missing_punch_policy', 'attendance', 'سياسة البصمة الناقصة', 'choice',
     [('invalid', 'اليوم غير صالح (يوم كامل)'), ('penalty_tiered', 'عقوبة متدرجة')], 'penalty_tiered', False, False),
    ('missing_punch_penalty_1', 'attendance', 'البصمة الناقصة — أول مرة (يوم)', 'number', None, '0', False, False),
    ('missing_punch_penalty_2', 'attendance', 'البصمة الناقصة — تاني مرة (يوم)', 'number', None, '0.25', False, False),
    ('missing_punch_penalty_3', 'attendance', 'البصمة الناقصة — من التالتة (يوم لكل مرة)', 'number', None, '1.0', False, False),
    ('presence_missing_policy', 'attendance', 'سياسة بصمة التواجد', 'choice',
     [('warning', 'إنذار'), ('deduct_fraction', 'جزء من يوم'), ('penalty_tiered', 'متدرج')], 'warning', False, False),
    ('presence_penalty_monthly_cap_days', 'attendance', 'حد جزاء التواجد في الشهر (يوم)', 'number', None, '5', False, False),
    # --- التأخير (يوم بيوم)
    ('grace_late_minutes', 'late', 'سماح التأخير (دقيقة)', 'number', None, '10', False, True),
    ('late_arrival_policy', 'late', 'سياسة التأخير', 'choice',
     [('actual_time', 'بالدقيقة'), ('quarter_day', 'ربع يوم'), ('half_day', 'نص يوم'),
      ('full_day', 'يوم كامل'), ('warning', 'إنذار')], 'actual_time', False, True),
    ('early_departure_grace_minutes', 'late', 'سماح الانصراف بدري (دقيقة)', 'number', None, '5', False, True),
    ('early_departure_policy', 'late', 'سياسة الانصراف بدري', 'choice',
     [('actual_time', 'بالدقيقة'), ('quarter_day', 'ربع يوم'), ('half_day', 'نص يوم'),
      ('full_day', 'يوم كامل'), ('warning', 'إنذار')], 'actual_time', False, True),
    ('late_count_from', 'late', 'التأخير بعد السماح بيتحسب من', 'choice',
     [('shift_start', 'بداية الشفت (كله)'), ('grace_end', 'نهاية السماح (الزيادة بس)')], 'shift_start', False, True),
    ('early_count_from', 'late', 'الانصراف بدري بعد السماح بيتحسب من', 'choice',
     [('shift_end', 'نهاية الشفت (كله)'), ('grace_end', 'نهاية السماح (الزيادة بس)')], 'shift_end', False, True),
    ('hourly_perm_effect', 'late', 'الاستئذان عند بداية/نهاية الشفت', 'choice',
     [('cancel', 'يلغي التأخير/الانصراف كله'), ('reduce', 'يقلله بمدة الاستئذان بس')], 'cancel', False, True),
    ('penalty_monthly_cap_days', 'late', 'سقف الجزاءات في الشهر (أيام أجر)', 'number', None, '5', True, False),
    # --- الإضافي
    ('overtime_net_of_late', 'overtime', 'الإضافي يتخصم منه التأخير؟', 'choice',
     [('0', 'لأ'), ('1', 'أيوه')], '0', False, True),
    ('overtime_round_to_minutes', 'overtime', 'تقريب الإضافي (دقيقة)', 'number', None, '0', False, True),
    ('overtime_min_minutes', 'overtime', 'أقل إضافي في اليوم بيتحسب (دقيقة)', 'number', None, '0', False, True),
    ('overtime_requires_approval', 'overtime', 'الإضافي محتاج موافقة؟', 'choice',
     [('0', 'لأ — من البصمة'), ('1', 'أيوه — بحد الموافقة على اليوم')], '0', False, True),
    ('overtime_cap_action', 'overtime', 'لو الإضافي عدّى حد الشهر', 'choice',
     [('warn', 'تنبيه (الساعات بتتصرف)'), ('block', 'يمنع حفظ الكشف لحد المراجعة')], 'warn', False, False),
    ('comp_rest_policy', 'overtime', 'الراحة البديلة', 'choice',
     [('none', 'مش متابعة'), ('track', 'متابعة رصيد (يوم عن كل يوم راحة/عطلة اشتغله)')], 'none', True, False),
    ('comp_rest_min_hours', 'overtime', 'أقل ساعات في يوم الراحة/العطلة تستحق راحة بديلة', 'number', None, '4', True, False),
    ('weekday_ot_multiplier', 'overtime', 'معامل الإضافي — يوم عادي', 'number', None, '1.25', True, False),
    ('weekend_ot_multiplier', 'overtime', 'معامل الإضافي — راحة', 'number', None, '1.5', True, False),
    ('holiday_ot_multiplier', 'overtime', 'معامل الإضافي — عطلة رسمية', 'number', None, '2.0', True, False),
    ('overtime_cap_monthly_hours', 'overtime', 'حد الإضافي في الشهر (ساعة — تنبيه)', 'number', None, '60', True, False),
    # --- الإجازات
    ('sick_tier_year_basis', 'leave', 'سنة شرائح المرضية', 'choice',
     [('calendar', 'السنة الميلادية'), ('service', 'سنة الخدمة')], 'calendar', True, False),
    # --- الخصومات
    ('loan_deduction_cap_pct', 'deductions', 'حد أقساط السلف (% من الأجر)', 'number', None, '10', True, False),
    # --- التأمينات
    ('pifss_enabled', 'insurance', 'تفعيل التأمينات', 'choice', [('0', 'مقفول'), ('1', 'مفعّل')], '0', True, False),
    ('pifss_employee_pct', 'insurance', 'نسبة الموظف %', 'number', None, '10.5', True, False),
    ('pifss_employer_pct', 'insurance', 'نسبة الشركة %', 'number', None, '11.5', True, False),
    ('pifss_salary_cap', 'insurance', 'سقف الأجر الخاضع', 'number', None, '2750', True, False),
]
REGISTRY = {k: {'key': k, 'section': sec, 'label': lab, 'type': typ, 'choices': ch, 'default': d,
                'legal': legal, 'per_day': per_day}
            for k, sec, lab, typ, ch, d, legal, per_day in _R}
PER_DAY_KEYS = tuple(k for k, v in REGISTRY.items() if v['per_day'])


# ------------------------------------------------------------ المخطّط

def ensure_schema(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS payroll_policies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        key TEXT NOT NULL,
        value TEXT NOT NULL,
        scope TEXT NOT NULL DEFAULT 'company',
        scope_id TEXT,
        effective_from TEXT NOT NULL,
        note TEXT,
        created_by INTEGER,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        revoked_by INTEGER,
        revoked_at DATETIME,
        revoke_reason TEXT)''')
    # موافقات الإضافي على اليوم (سياسة «الإضافي محتاج موافقة؟»).
    conn.execute('''CREATE TABLE IF NOT EXISTS overtime_approvals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        employee_id INTEGER NOT NULL,
        work_date TEXT NOT NULL,
        minutes INTEGER NOT NULL,
        note TEXT,
        approved_by INTEGER,
        approved_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(employee_id, work_date))''')
    conn.execute('CREATE INDEX IF NOT EXISTS ix_policies_key ON payroll_policies (key, scope, effective_from)')


# ------------------------------------------------------------ القراءة

class Policies:
    """كل السياسات محمّلة مرة واحدة للحساب (الكشف بيسأل آلاف المرات)."""

    def __init__(self, conn, legacy=None):
        ensure_schema(conn)
        if legacy is None:
            from utils.settings_utils import get_salary_settings_v2
            legacy = get_salary_settings_v2(conn) or {}
        self.legacy = dict(legacy)
        self.rows = {}
        for r in conn.execute('''SELECT key, value, scope, scope_id, effective_from, id FROM payroll_policies
                                 WHERE revoked_at IS NULL ORDER BY effective_from, id''').fetchall():
            self.rows.setdefault(r['key'], []).append(dict(r))
        self._groups = {}

    def has_rows(self):
        return bool(self.rows)

    def base(self, key):
        """القيمة من غير أي سطر بتاريخ: القديم في الإعدادات، وإلا الافتراضي."""
        if key in self.legacy and self.legacy[key] not in (None, ''):
            return str(self.legacy[key]), 'legacy'
        return (REGISTRY[key]['default'] if key in REGISTRY else None), 'system'

    def resolve(self, key, employee_id=None, day=None, group=None):
        """(القيمة، المصدر، السطر أو None) السارية يوم `day` للموظف."""
        day = (day.isoformat() if isinstance(day, (date, datetime)) else str(day or date.today().isoformat()))[:10]
        rows = self.rows.get(key)
        if rows:
            for scope, sid in (('employee', str(employee_id) if employee_id is not None else None),
                               ('group', group), ('company', None)):
                if scope != 'company' and not sid:
                    continue
                hit = None
                for r in rows:
                    if r['scope'] == scope and (scope == 'company' or str(r['scope_id']) == sid) \
                            and r['effective_from'] <= day:
                        hit = r                      # مترتبين بالتاريخ: الأخير هو الساري
                if hit:
                    return hit['value'], scope, hit
        v, src = self.base(key)
        return v, src, None

    def get(self, key, employee_id=None, day=None, group=None):
        return self.resolve(key, employee_id, day, group)[0]

    def day_view(self, base_sal, employee_id, day, group=None):
        """`sal` لليوم ده: الإعدادات + أي سياسة سارية يومها (للمفاتيح اللي ليها سطور بس — سريع)."""
        if not self.rows:
            return base_sal
        out = None
        for k in self.rows:
            v, src, _ = self.resolve(k, employee_id, day, group)
            if src in ('employee', 'group', 'company'):
                if out is None:
                    out = dict(base_sal)
                out[k] = v
        return out if out is not None else base_sal

    def snapshot(self, start_iso, end_iso):
        """اللي اتبنى عليه الكشف: القيم الأساسية + كل سطر كان ساري في أي يوم من الدورة."""
        used = []
        for k, rows in self.rows.items():
            for r in rows:
                if r['effective_from'] <= end_iso:
                    used.append({'id': r['id'], 'key': k, 'value': r['value'], 'scope': r['scope'],
                                 'scope_id': r['scope_id'], 'effective_from': r['effective_from']})
        base = {k: self.base(k)[0] for k in REGISTRY}
        return {'period': [start_iso, end_iso], 'base': base, 'rows': used,
                'taken_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')}


def load(conn, legacy=None):
    return Policies(conn, legacy)


# ------------------------------------------------------------ الكتابة

def validate(key, value):
    meta = REGISTRY.get(key)
    if not meta:
        raise ValueError('سياسة غير معروفة')
    v = str(value if value is not None else '').strip()
    if meta['type'] == 'choice':
        if v not in {c for c, _ in meta['choices']}:
            raise ValueError(f"قيمة غير مسموحة لـ «{meta['label']}»")
    else:
        try:
            n = float(v)
        except ValueError:
            raise ValueError(f"«{meta['label']}» لازم رقم")
        if n < 0:
            raise ValueError(f"«{meta['label']}» مايبقاش سالب")
        v = ('%g' % n)
    return v


def add(conn, key, value, effective_from, user_id, scope='company', scope_id=None, note=''):
    """سطر جديد (مابيعدّلش القديم). يُرجع رقمه."""
    ensure_schema(conn)
    v = validate(key, value)
    if scope not in SCOPES:
        raise ValueError('نطاق غير معروف')
    if scope != 'company' and not str(scope_id or '').strip():
        raise ValueError('حدد الموظف أو المجموعة')
    try:
        eff = datetime.strptime(str(effective_from)[:10], '%Y-%m-%d').date().isoformat()
    except ValueError:
        raise ValueError('تاريخ السريان غلط')
    if len((note or '').strip()) < 3:
        raise ValueError('اكتب سبب التغيير')
    cur = conn.execute('''INSERT INTO payroll_policies (key, value, scope, scope_id, effective_from, note, created_by)
                          VALUES (?, ?, ?, ?, ?, ?, ?)''',
                       (key, v, scope, str(scope_id).strip() if scope_id not in (None, '') else None, eff,
                        note.strip(), user_id))
    return cur.lastrowid


def revoke(conn, policy_id, user_id, reason):
    ensure_schema(conn)
    if len((reason or '').strip()) < 3:
        raise ValueError('اكتب سبب الإلغاء')
    n = conn.execute('''UPDATE payroll_policies SET revoked_by = ?, revoked_at = CURRENT_TIMESTAMP, revoke_reason = ?
                        WHERE id = ? AND revoked_at IS NULL''', (user_id, reason.strip(), int(policy_id))).rowcount
    if not n:
        raise ValueError('السياسة دي مش موجودة أو اتلغت')


def history(conn, key=None):
    ensure_schema(conn)
    q = '''SELECT p.*, u.username AS created_by_name FROM payroll_policies p
           LEFT JOIN users u ON u.id = p.created_by'''
    args = []
    if key:
        q += ' WHERE p.key = ?'
        args.append(key)
    q += ' ORDER BY p.id DESC LIMIT 300'
    return [dict(r) for r in conn.execute(q, args).fetchall()]


def impact(conn, effective_from):
    """تعديل ساري من تاريخ: الأشهر من الشهر ده لحد النهارده — المفتوحة (محتاجة إعادة حساب واعتماد)
    والمقفولة (مش هتتغيّر — نسختها محفوظة)."""
    from utils.payroll_engine import resolve_period
    try:
        d = datetime.strptime(str(effective_from)[:10], '%Y-%m-%d').date()
    except ValueError:
        return {'open': [], 'locked': []}
    today = date.today()
    m, y = d.month, d.year
    out = {'open': [], 'locked': []}
    for _ in range(36):
        ps, pe = resolve_period(conn, m, y)
        if ps > today:
            break
        if pe >= d:
            r = conn.execute('SELECT status FROM payroll_runs WHERE month = ? AND year = ?', (m, y)).fetchone()
            (out['locked'] if r and r['status'] == 'approved' else out['open']).append(
                {'month': m, 'year': y, 'saved': bool(r)})
        m, y = (1, y + 1) if m == 12 else (m + 1, y)
    return out


def snapshot_json(conn, start_iso, end_iso):
    return json.dumps(load(conn).snapshot(start_iso, end_iso), ensure_ascii=False)


# ------------------------------------------------------------ أيام الراحة بتاريخ سريان

def ensure_weekly_off_schema(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS employee_weekly_off_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        employee_id INTEGER NOT NULL,
        days TEXT NOT NULL,
        effective_from TEXT NOT NULL,
        note TEXT,
        created_by INTEGER,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP)''')
    conn.execute('CREATE INDEX IF NOT EXISTS ix_weekly_off_emp ON employee_weekly_off_history (employee_id, effective_from)')


def record_weekly_off(conn, employee_id, days, effective_from, user_id=None, note=''):
    """أيام راحة الموظف من تاريخ (أرقام البرنامج: 0=الأحد … 6=السبت، مفصولة بفاصلة)."""
    ensure_weekly_off_schema(conn)
    vals = sorted({int(x) % 7 for x in str(days).split(',') if str(x).strip().isdigit()})
    eff = datetime.strptime(str(effective_from)[:10], '%Y-%m-%d').date().isoformat()
    conn.execute('DELETE FROM employee_weekly_off_history WHERE employee_id = ? AND effective_from = ?',
                 (int(employee_id), eff))
    conn.execute('INSERT INTO employee_weekly_off_history (employee_id, days, effective_from, note, created_by) '
                 'VALUES (?, ?, ?, ?, ?)', (int(employee_id), ','.join(map(str, vals)), eff, note or None, user_id))


def weekly_off_history(conn):
    ensure_weekly_off_schema(conn)
    out = {}
    for r in conn.execute('SELECT employee_id, days, effective_from FROM employee_weekly_off_history '
                          'ORDER BY employee_id, effective_from, id').fetchall():
        out.setdefault(r['employee_id'], []).append(
            (r['effective_from'], {int(x) for x in str(r['days']).split(',') if x.strip().isdigit()}))
    return out


# ------------------------------------------------------------ موافقات الإضافي

def approve_overtime(conn, employee_id, work_date, minutes, user_id, note=''):
    """موافقة على إضافي يوم (بالدقايق). صفر = مرفوض. موافقة تانية لنفس اليوم بتستبدل الأولى."""
    ensure_schema(conn)
    d = datetime.strptime(str(work_date)[:10], '%Y-%m-%d').date().isoformat()
    mins = max(0, int(round(float(minutes or 0))))
    conn.execute('DELETE FROM overtime_approvals WHERE employee_id = ? AND work_date = ?', (int(employee_id), d))
    conn.execute('INSERT INTO overtime_approvals (employee_id, work_date, minutes, note, approved_by) VALUES (?, ?, ?, ?, ?)',
                 (int(employee_id), d, mins, note or None, user_id))


def approved_overtime(conn, start_iso, end_iso):
    """{(موظف، يوم): دقايق} — من شاشة الموافقات، ومن طلبات الأوفرتايم المعتمدة لو موجودة."""
    ensure_schema(conn)
    out = {}
    for r in conn.execute('SELECT employee_id, work_date, minutes FROM overtime_approvals WHERE work_date BETWEEN ? AND ?',
                          (start_iso, end_iso)).fetchall():
        out[(int(r['employee_id']), r['work_date'])] = int(r['minutes'])
    try:
        for r in conn.execute("SELECT employee_id, payload FROM hr_requests WHERE type = 'overtime' AND status = 'approved'"):
            p = json.loads(r['payload'] or '{}')
            d = str(p.get('work_date') or '')[:10]
            if start_iso <= d <= end_iso and (int(r['employee_id']), d) not in out:
                out[(int(r['employee_id']), d)] = int(round(float(p.get('hours') or 0) * 60))
    except Exception:
        pass
    return out


# ------------------------------------------------------------ الراحة البديلة

def comp_rest_balance(conn, year=None, employee_id=None, min_hours=None):
    """رصيد الراحة البديلة من الأشهر المعتمدة: يوم عن كل يوم راحة/عطلة اشتغل فيه ≥ الحد،
    ناقص إجازات «راحة بديلة» المعتمدة. يُرجع [{employee_id, earned, taken, balance, days: [...]}]."""
    if min_hours is None:
        try:
            min_hours = float(load(conn).get('comp_rest_min_hours') or 4)
        except (TypeError, ValueError):
            min_hours = 4.0
    year = int(year or date.today().year)
    q = '''SELECT d.employee_id, d.day, d.kind, d.actual_hours FROM payroll_run_days d
             JOIN payroll_runs r ON r.id = d.run_id
             WHERE r.status = 'approved' AND d.kind IN ('rest', 'holiday') AND d.actual_hours >= ?
               AND substr(d.day, 1, 4) = ?'''
    args = [min_hours, str(year)]
    if employee_id:
        q += ' AND d.employee_id = ?'
        args.append(int(employee_id))
    out = {}
    for r in conn.execute(q, args).fetchall():
        e = out.setdefault(r['employee_id'], {'employee_id': r['employee_id'], 'earned': 0, 'taken': 0.0, 'days': []})
        e['earned'] += 1
        e['days'].append({'day': r['day'], 'kind': r['kind'], 'hours': r['actual_hours']})
    try:
        for r in conn.execute('''SELECT lr.employee_id, COALESCE(SUM(lr.days_count), 0) AS n FROM leave_requests lr
                                 JOIN leave_types lt ON lt.id = lr.leave_type_id
                                 WHERE lr.status = 'approved' AND lt.law_kind = 'comp_rest'
                                   AND substr(lr.start_date, 1, 4) = ? GROUP BY lr.employee_id''', (str(year),)):
            if employee_id and int(r['employee_id']) != int(employee_id):
                continue
            e = out.setdefault(r['employee_id'], {'employee_id': r['employee_id'], 'earned': 0, 'taken': 0.0, 'days': []})
            e['taken'] = float(r['n'] or 0)
    except Exception:
        pass
    for e in out.values():
        e['balance'] = e['earned'] - e['taken']
    return sorted(out.values(), key=lambda x: x['employee_id'])
