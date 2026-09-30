"""قانون العمل الكويتيّ 6/2010 — الفجوات الخمس التي سدّها 2.18.

1. **الإضافيّ:** سقفُ الشهر كان يقصّ الساعات المدفوعة؛ الآن تُدفع كلُّها
   والتجاوزُ تنبيه (سقف الشهر، ساعتان في اليوم، 3 أيام في الأسبوع، 180 في
   السنة). والتقريبُ الافتراضيّ 0.
2. **الجزاءات:** ما يزيد على الوقت الفعليّ مسقوفٌ شهريًّا (5 أيام أجر)،
   ولا يُسقف خصمُ الوقت الذي لم يُعمل.
3. **الإجازات:** الحج والوضع والوفاة والعدّة مدفوعةٌ بنوعها بحدودها، لا
   برصيد السنويّة؛ والسنويّةُ بعد 9 أشهر؛ ورصيدُ نهاية الخدمة لا يُنقصه
   إلّا ما يُخصم من السنويّة.
4. **الراحةُ البديلة** عن العمل يومَ الراحة أو العطلة، والأعيادُ الثابتة.
5. **الإنذار والتجربة والحدّ الأدنى والتأمينات والاستراحة.**

يُشغَّل:  python -m pytest tests/test_labor_law.py -v
"""
import json
import os
import sqlite3
import sys
from datetime import date, timedelta

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

MONTH, YEAR = 8, 2026


@pytest.fixture
def env(tmp_path, monkeypatch):
    import importlib
    monkeypatch.setenv('HR_DATA_DIR', str(tmp_path))
    import utils.db as db
    importlib.reload(db)
    db.init_db()
    conn = sqlite3.connect(os.path.join(str(tmp_path), 'hr_system.db'))
    conn.row_factory = sqlite3.Row
    conn.execute("INSERT INTO shift_types (name, start_time, end_time, hours_per_day, flex_mode, is_active)"
                 " VALUES ('S', '08:00', '16:00', 8, 'none', 1)")
    conn.execute("INSERT INTO shift_types (name, start_time, end_time, hours_per_day, flex_mode, is_active,"
                 " break_minutes) VALUES ('B', '08:00', '17:00', 8, 'none', 1, 60)")
    _emp(conn, 1, '2020-01-01', nationality='Egypt')
    conn.commit()
    import utils.payroll_engine as pe
    importlib.reload(pe)
    import utils.labor_law as L
    importlib.reload(L)
    return conn, pe, L


def _emp(conn, eid, hire, salary=260, shift='S', nationality='', gender='male', religion=''):
    conn.execute("INSERT INTO employees (id, employee_number, name, department, position, hire_date,"
                 " salary, is_active, shift_type, nationality, gender, religion)"
                 " VALUES (?, ?, ?, 'الإدارة', 'فنّي', ?, ?, 1, ?, ?, ?, ?)",
                 (eid, str(eid), f'م{eid}', hire, salary, shift, nationality, gender, religion))


def _set(conn, **kw):
    for k, v in kw.items():
        conn.execute('INSERT INTO salary_settings_v2 (setting_name, setting_value) VALUES (?, ?) '
                     'ON CONFLICT(setting_name) DO UPDATE SET setting_value = excluded.setting_value',
                     (k, str(v)))
    conn.commit()


def _punch(conn, eid, day, *times):
    for t in times:
        conn.execute("INSERT INTO attendance_records (employee_id, device_id, check_time, check_type)"
                     " VALUES (?, 'T1', ?, 'I')", (eid, f'{day} {t}'))
    conn.commit()


def _workdays(pe, conn, weeks=1):
    """أيامُ العمل (الأحد–الخميس) لأسابيعَ كاملةٍ داخل الدورة."""
    start, end = pe.resolve_period(conn, MONTH, YEAR)
    d = start + timedelta(days=(6 - start.isoweekday() % 7 + 1) % 7)   # أوّلُ أحد
    out = []
    for _ in range(weeks):
        out += [(d + timedelta(days=i)).isoformat() for i in range(5)]
        d += timedelta(days=7)
    assert all(x <= end.isoformat() for x in out)
    return out


def _row(pe, conn, eid=1):
    return next(r for r in pe.compute_monthly_payroll(conn, MONTH, YEAR)['rows']
                if r['employee_id'] == eid)


def _item(items, code):
    return next((i for i in items if i['code'] == code), None)


def _codes(row):
    return {w['code'] for w in row['law_warnings']}


# ================================================== 1. الإضافيّ

def test_new_installs_pay_overtime_to_the_minute(env):
    conn, pe, L = env
    from utils.settings_utils import get_salary_settings_v2
    assert get_salary_settings_v2(conn)['overtime_round_to_minutes'] == '0'
    day = _workdays(pe, conn)[0]
    _punch(conn, 1, day, '08:00', '16:07')
    assert pe.compute_month_metrics(conn, MONTH, YEAR)[1]['ot_weekday_mins'] == 7


def test_the_monthly_cap_no_longer_cuts_paid_overtime(env):
    conn, pe, L = env
    _set(conn, overtime_cap_monthly_hours=10)
    for day in _workdays(pe, conn):
        _punch(conn, 1, day, '08:00', '19:00')          # 3 ساعات إضافيّ × 5 أيام
    row = _row(pe, conn)
    ot = _item(row['allowances'], 'overtime')
    # 15 ساعة × (260 ÷ 26 ÷ 8 = 1.25) × 1.25 — لا 10 ساعات
    assert ot['amount'] == pytest.approx(15 * 1.25 * 1.25, abs=0.01)
    assert {'ot_month_cap', 'ot_day_2h', 'ot_week_3d'} <= _codes(row)
    w = next(w for w in row['law_warnings'] if w['code'] == 'ot_day_2h')
    assert w['n'] == 5


def test_two_hours_a_day_on_three_days_raises_nothing(env):
    conn, pe, L = env
    for day in _workdays(pe, conn)[:3]:
        _punch(conn, 1, day, '08:00', '18:00')          # ساعتان بالضبط، 3 أيام
    assert not ({'ot_day_2h', 'ot_week_3d', 'ot_month_cap'} & _codes(_row(pe, conn)))


def test_the_yearly_180_hours_counts_earlier_months(env):
    conn, pe, L = env
    conn.execute("INSERT INTO payroll_hours_approvals (employee_id, month, year, ot_weekday_mins)"
                 " VALUES (1, 3, ?, ?)", (YEAR, 179 * 60))
    conn.commit()
    _punch(conn, 1, _workdays(pe, conn)[0], '08:00', '18:00')
    w = next(w for w in _row(pe, conn)['law_warnings'] if w['code'] == 'ot_year_180')
    assert w['hours'] == pytest.approx(181.0)


# ================================================== 2. الجزاءات

def test_penalties_above_actual_time_are_capped_at_five_days(env):
    conn, pe, L = env
    _set(conn, late_arrival_policy='full_day')
    for day in _workdays(pe, conn, weeks=2)[:8]:
        _punch(conn, 1, day, '08:13', '16:00')          # 13 دقيقة، تُحسب يومًا كاملًا
    row = _row(pe, conn)
    late = _item(row['deductions'], 'lateness_deduction')
    actual = 8 * 13 / 60 * 1.25                          # أجرُ الوقت الذي لم يُعمل
    assert late['amount'] == pytest.approx(actual + 5 * 10, abs=0.01)
    assert late['capped_by_law'] == pytest.approx(80 - actual - 50, abs=0.01)
    assert 'penalty_capped' in _codes(row)


def test_actual_time_not_worked_is_never_capped(env):
    conn, pe, L = env
    _set(conn, late_arrival_policy='actual_time', penalty_monthly_cap_days=1)
    for day in _workdays(pe, conn, weeks=2)[:8]:
        _punch(conn, 1, day, '11:20', '16:00')          # 200 دقيقة تأخير فعليّ
    late = _item(_row(pe, conn)['deductions'], 'lateness_deduction')
    assert late['amount'] == pytest.approx(8 * 200 / 60 * 1.25, abs=0.01)
    assert 'capped_by_law' not in late


def test_missing_punches_are_capped_too(env):
    conn, pe, L = env
    _set(conn, missing_punch_policy='invalid')
    for day in _workdays(pe, conn, weeks=2)[:8]:
        _punch(conn, 1, day, '08:00')                   # بصمةٌ واحدة
    mp = _item(_row(pe, conn)['deductions'], 'missing_punch_penalty')
    assert mp['amount'] == pytest.approx(50.0), 'ثمانية أيام تصير خمسة'


def test_new_installs_do_not_charge_a_day_per_missing_punch(env):
    conn, pe, L = env
    from utils.settings_utils import get_salary_settings_v2
    assert get_salary_settings_v2(conn)['missing_punch_policy'] == 'penalty_tiered'


# ================================================== 5. التأمينات والاستراحة

def test_pifss_is_deducted_for_kuwaitis_only(env):
    conn, pe, L = env
    _emp(conn, 2, '2020-01-01', nationality='kuwait')
    _set(conn, pifss_enabled=1)
    kw, eg = _row(pe, conn, 2), _row(pe, conn, 1)
    assert _item(kw['deductions'], 'social_insurance_emp')['amount'] == pytest.approx(27.3)
    assert kw['employer_pifss'] == pytest.approx(29.9)
    assert _item(eg['deductions'], 'social_insurance_emp') is None and eg['employer_pifss'] == 0
    _set(conn, pifss_salary_cap=200)
    assert _item(_row(pe, conn, 2)['deductions'], 'social_insurance_emp')['amount'] == pytest.approx(21.0)


def test_pifss_is_off_until_enabled(env):
    conn, pe, L = env
    _emp(conn, 2, '2020-01-01', nationality='kuwait')
    conn.commit()
    assert _item(_row(pe, conn, 2)['deductions'], 'social_insurance_emp') is None


def test_the_break_is_not_counted_as_work(env):
    conn, pe, L = env
    _emp(conn, 2, '2020-01-01', shift='B')
    _emp(conn, 3, '2020-01-01', shift='S')
    conn.execute("UPDATE shift_types SET end_time = '17:00' WHERE name = 'S'")
    conn.commit()
    day = _workdays(pe, conn)[0]
    _punch(conn, 2, day, '08:00', '17:00')
    _punch(conn, 3, day, '08:00', '17:00')
    m = pe.compute_month_metrics(conn, MONTH, YEAR)
    assert m[2]['actual_hours'] == 8.0, '9 ساعات حضور − ساعة استراحة'
    assert m[3]['actual_hours'] == 9.0
    days = {d['date']: d for d in pe.compute_employee_days(conn, 2, MONTH, YEAR)['days']}
    assert days[day]['hours'] == 8.0


def test_a_short_day_keeps_its_hours(env):
    conn, pe, L = env
    assert L.net_span_hours(4.5, 60) == 4.5
    assert L.net_span_hours(5.5, 60) == 5.0, 'لا تنزل تحت الخمس'
    assert L.net_span_hours(9.0, 0) == 9.0


# ================================================== 3. الإجازات

def _lt(conn, kind):
    return conn.execute('SELECT * FROM leave_types WHERE law_kind = ?', (kind,)).fetchone()


def test_statutory_leave_types_are_seeded_and_tagged(env):
    conn, pe, L = env
    for kind in ('hajj', 'maternity', 'bereavement', 'iddah', 'comp_rest'):
        lt = _lt(conn, kind)
        assert lt and lt['is_paid'] == 1 and lt['deducts_annual'] == 0, kind
    assert _lt(conn, 'unpaid')['is_paid'] == 0, 'كانت تُزرع مدفوعة'
    assert _lt(conn, 'annual')['service_months_required'] == 9
    assert _lt(conn, 'annual')['deducts_annual'] == 1


def test_migration_runs_once_per_type(env):
    conn, pe, L = env
    conn.execute("UPDATE leave_types SET is_paid = 1 WHERE law_kind = 'unpaid'")
    conn.commit()
    L.migrate(conn)
    conn.commit()
    assert _lt(conn, 'unpaid')['is_paid'] == 1, 'قرارُ صاحب العمل بعد الترحيل لا يُمسّ'
    assert conn.execute("SELECT COUNT(*) FROM leave_types WHERE law_kind = 'hajj'").fetchone()[0] == 1


def _dec(conn, L, eid, kind, start, days, **kw):
    s = date.fromisoformat(start)
    e = s + timedelta(days=days - 1)
    return L.leave_decision(conn, eid, _lt(conn, kind), s.isoformat(), e.isoformat(), days, **kw)


def test_maternity_and_iddah_are_for_female_workers(env):
    conn, pe, L = env
    _emp(conn, 2, '2025-01-01', gender='female', religion='مسلمة')
    _emp(conn, 3, '2025-01-01', gender='female', religion='christian')
    conn.commit()
    assert _dec(conn, L, 1, 'maternity', '2026-08-02', 10)['error'][0] == 'x.lv_err_female_only'
    ok = _dec(conn, L, 2, 'maternity', '2026-08-02', 70)
    assert ok['paid'] and not ok['error'], 'مدفوعةٌ ولو كان رصيدُ السنويّة صفرًا'
    assert _dec(conn, L, 2, 'maternity', '2026-08-02', 71)['error'] == ('x.lv_err_max_days', {'n': 70})
    assert not _dec(conn, L, 2, 'iddah', '2026-08-02', 130)['error']
    assert _dec(conn, L, 3, 'iddah', '2026-08-02', 22)['error'] == ('x.lv_err_max_days', {'n': 21})


def test_hajj_needs_two_years_and_is_granted_once(env):
    conn, pe, L = env
    _emp(conn, 2, '2025-01-01')
    conn.commit()
    assert _dec(conn, L, 2, 'hajj', '2026-08-02', 10)['error'][0] == 'x.lv_err_hajj_service'
    assert _dec(conn, L, 1, 'hajj', '2026-08-02', 22)['error'] == ('x.lv_err_max_days', {'n': 21})
    assert not _dec(conn, L, 1, 'hajj', '2026-08-02', 21)['error']
    conn.execute("INSERT INTO leave_requests (employee_id, leave_type_id, start_date, end_date, days_count,"
                 " status, is_paid_leave) VALUES (1, ?, '2024-06-01', '2024-06-15', 15, 'approved', 1)",
                 (_lt(conn, 'hajj')['id'],))
    conn.commit()
    assert _dec(conn, L, 1, 'hajj', '2026-08-02', 10)['error'][0] == 'x.lv_err_hajj_once'


def test_bereavement_is_three_days(env):
    conn, pe, L = env
    assert not _dec(conn, L, 1, 'bereavement', '2026-08-02', 3)['error']
    assert _dec(conn, L, 1, 'bereavement', '2026-08-02', 4)['error'][0] == 'x.lv_err_max_days'


def test_annual_leave_waits_nine_months(env):
    conn, pe, L = env
    _emp(conn, 2, '2026-03-01')
    conn.commit()
    assert _dec(conn, L, 2, 'annual', '2026-08-02', 2, portal=True)['error'] == \
        ('x.lv_err_service_months', {'n': 9})
    admin = _dec(conn, L, 2, 'annual', '2026-08-02', 2)
    assert not admin['error'] and admin['warning'][0] == 'x.lv_warn_service_months'
    assert not _dec(conn, L, 2, 'annual', '2026-12-02', 2, portal=True)['error']


def test_the_nine_months_hold_even_if_the_type_says_zero(env):
    conn, pe, L = env
    _emp(conn, 2, '2026-03-01')
    conn.execute("UPDATE leave_types SET service_months_required = 0 WHERE law_kind = 'annual'")
    conn.commit()
    assert _dec(conn, L, 2, 'annual', '2026-08-02', 2, portal=True)['error'][0] == 'x.lv_err_service_months'


def test_an_unknown_hire_date_blocks_nothing(env):
    conn, pe, L = env
    conn.execute("UPDATE employees SET hire_date = '' WHERE id = 1")
    conn.commit()
    assert not _dec(conn, L, 1, 'annual', '2026-08-02', 2, portal=True)['error']
    assert not _dec(conn, L, 1, 'hajj', '2026-08-02', 2, portal=True)['error']


def test_statutory_leave_does_not_reduce_the_end_of_service_balance(env):
    conn, pe, L = env
    _emp(conn, 2, '2024-01-01', gender='female')
    conn.commit()
    from utils.leave_balance import compute_leave_balance
    before = compute_leave_balance(conn, 2, as_of='2026-09-30')['balance']
    for kind, s, e, n in (('maternity', '2026-01-04', '2026-03-14', 50),
                          ('annual', '2026-05-03', '2026-05-07', 5)):
        conn.execute("INSERT INTO leave_requests (employee_id, leave_type_id, start_date, end_date,"
                     " days_count, status, is_paid_leave) VALUES (2, ?, ?, ?, ?, 'approved', 1)",
                     (_lt(conn, kind)['id'], s, e, n))
    conn.commit()
    after = compute_leave_balance(conn, 2, as_of='2026-09-30')['balance']
    assert after == pytest.approx(before - 5), 'السنويّةُ وحدها'


# ================================================== 4. الراحة البديلة والأعياد

def test_working_a_rest_day_earns_a_compensatory_day(env):
    conn, pe, L = env
    _set(conn, comp_rest_since='2026-08-01')
    fri = next(d for d in (date(2026, 8, 1) + timedelta(days=i) for i in range(7)) if d.isoweekday() == 5)
    _punch(conn, 1, fri.isoformat(), '09:00', '13:00')
    _punch(conn, 1, (fri + timedelta(days=1)).isoformat(), '09:00')        # بصمةٌ واحدة لا تُحسب
    _punch(conn, 1, (fri + timedelta(days=2)).isoformat(), '08:00', '16:00')  # يومُ عمل
    _punch(conn, 1, '2026-07-31', '09:00', '13:00')                        # جمعةٌ قبل البداية
    assert L.comp_rest_balance(conn, 1, '2026-08-31')['earned'] == 1
    assert _dec(conn, L, 1, 'comp_rest', '2026-08-20', 2)['error'] == ('x.lv_err_comp_balance', {'n': 1})
    assert not _dec(conn, L, 1, 'comp_rest', '2026-08-20', 1)['error']


def test_working_an_official_holiday_earns_one_too(env):
    conn, pe, L = env
    _set(conn, comp_rest_since='2026-01-01')
    conn.execute("INSERT INTO official_holidays (name, date, type) VALUES ('العيد الوطني', '2026-02-25', 'وطنية')")
    conn.commit()
    _punch(conn, 1, '2026-02-25', '08:00', '12:00')
    assert L.comp_rest_balance(conn, 1, '2026-03-31')['earned'] == 1


def test_fixed_holidays_are_seeded_once_a_year_and_never_in_the_past(env):
    conn, pe, L = env
    conn.execute("DELETE FROM official_holidays WHERE strftime('%Y', date) IN ('2030', '2031')")
    conn.execute("DELETE FROM salary_settings_v2 WHERE setting_name LIKE 'law_holidays_seeded_203%'")
    conn.commit()
    L.seed_fixed_holidays(conn, today=date(2030, 2, 1))

    def got(y):
        return {r[0] for r in conn.execute(
            "SELECT date FROM official_holidays WHERE strftime('%Y', date) = ?", (str(y),))}
    assert got(2030) == {'2030-02-25', '2030-02-26'}, 'رأسُ السنة مضى — لا يُزرع'
    assert got(2031) == {'2031-01-01', '2031-02-25', '2031-02-26'}
    conn.execute("DELETE FROM official_holidays WHERE date = '2031-02-26'")
    conn.commit()
    L.seed_fixed_holidays(conn, today=date(2030, 3, 1))
    assert '2031-02-26' not in got(2031), 'عيدٌ حُذف بقرار لا يعود'


# ================================================== الإنذار (المادة 44)

@pytest.mark.parametrize('reason, served, waived, expected', [
    ('termination', '30', False, 60 * 260 / 30),     # صاحبُ العمل يدفع ما نقص
    ('termination', '90', False, 0.0),
    ('termination', '120', False, 0.0),              # مهلةٌ أطول لا تُنتج بدلًا سالبًا
    ('resignation', '0', False, -90 * 260 / 30),     # المستقيلُ يدفع
    ('resignation', '0', True, 0.0),                 # تنازل صاحبُ العمل
    ('art41', '0', False, 0.0),
    ('resignation_art48', '0', False, 0.0),
    ('termination', None, False, 0.0),               # فارغ = بلا بدل
])
def test_notice_pay(env, reason, served, waived, expected):
    conn, pe, L = env
    from routes.eos_routes import compute_kuwait_eos
    c = compute_kuwait_eos(conn, 1, '2026-01-01', reason, leave_days=0,
                           notice_served_days=served, notice_waived=waived)
    base = compute_kuwait_eos(conn, 1, '2026-01-01', reason, leave_days=0)
    assert c['notice_amount'] == pytest.approx(expected, abs=1e-3)
    assert c['net'] == pytest.approx(base['net'] + expected, abs=1e-3)


def test_no_notice_during_probation(env):
    conn, pe, L = env
    conn.execute("UPDATE employees SET probation_end_date = '2026-03-01' WHERE id = 1")
    conn.commit()
    from routes.eos_routes import compute_kuwait_eos
    c = compute_kuwait_eos(conn, 1, '2026-01-01', 'termination', leave_days=0, notice_served_days='0')
    assert c['in_probation'] and c['notice_amount'] == 0.0


def test_probation_limit_is_one_hundred_working_days(env):
    conn, pe, L = env
    emp = conn.execute('SELECT * FROM employees WHERE id = 1').fetchone()
    lim = L.probation_limit(conn, emp, '2026-08-02')
    d, n = date(2026, 8, 2), 0
    while d <= lim:
        if d.isoweekday() not in (5, 6):
            n += 1
        d += timedelta(days=1)
    assert n == 100 and lim.isoweekday() not in (5, 6)


# ================================================== عبر الشاشات

@pytest.fixture
def web(env):
    import importlib
    conn, pe, L = env
    import utils.auth as auth
    importlib.reload(auth)
    auth.get_current_license_info = lambda: {'ok': True}
    import app as A
    importlib.reload(A)
    A.app.before_request_funcs[None] = [f for f in A.app.before_request_funcs.get(None, [])
                                        if f.__name__ not in ('check_license_globally',
                                                              'enforce_plan_features')]
    A.app.config['TESTING'] = True
    admin = conn.execute("SELECT id FROM users WHERE role='admin'").fetchone()[0]
    c = A.app.test_client()
    with c.session_transaction() as s:
        s.update({'user_id': admin, 'role': 'admin', 'username': 'admin'})
    return c, conn, A


def _add_leave(c, conn, kind, start, end, eid=1):
    return c.post('/leaves/add', data={'employee_id': str(eid), 'leave_type_id': str(_lt(conn, kind)['id']),
                                        'start_date': start, 'end_date': end, 'reason': 'x'})


def test_a_statutory_leave_is_paid_without_annual_balance(web):
    c, conn, A = web
    _emp(conn, 2, '2026-08-01', gender='female')         # بلا رصيدٍ يُذكر
    conn.commit()
    _add_leave(c, conn, 'bereavement', '2026-08-09', '2026-08-11', eid=2)
    r = conn.execute('SELECT * FROM leave_requests WHERE employee_id = 2').fetchone()
    assert r and r['is_paid_leave'] == 1


def test_the_admin_screen_refuses_what_the_law_caps(web):
    c, conn, A = web
    _add_leave(c, conn, 'bereavement', '2026-08-09', '2026-08-14')
    assert conn.execute('SELECT COUNT(*) FROM leave_requests').fetchone()[0] == 0


def test_approving_leave_does_not_count_itself_against_the_balance(web):
    c, conn, A = web
    from utils.leave_balance import compute_leave_balance
    conn.execute("UPDATE employees SET hire_date = '2025-08-01' WHERE id = 1")
    conn.commit()
    bal = compute_leave_balance(conn, 1, as_of='2026-08-02')['balance']
    days = int(bal)
    lt = _lt(conn, 'annual')
    conn.execute("INSERT INTO leave_requests (employee_id, leave_type_id, start_date, end_date, days_count,"
                 " status, is_paid_leave) VALUES (1, ?, '2026-08-02', '2026-08-20', ?, 'pending', 1)",
                 (lt['id'], days))
    conn.commit()
    rid = conn.execute('SELECT id FROM leave_requests').fetchone()[0]
    c.post(f'/leaves/update_status/{rid}', data={'status': 'approved'})
    r = conn.execute('SELECT status, is_paid_leave FROM leave_requests WHERE id = ?', (rid,)).fetchone()
    assert r['status'] == 'approved' and r['is_paid_leave'] == 1


def test_the_portal_refuses_annual_leave_before_nine_months(web):
    c, conn, A = web
    _emp(conn, 2, '2026-06-01')
    conn.execute("INSERT INTO users (username, password, full_name, role, is_active, employee_id)"
                 " VALUES ('e2', 'x', 'e2', 'user', 1, 2)")
    conn.commit()
    uid = conn.execute("SELECT id FROM users WHERE username = 'e2'").fetchone()[0]
    p = A.app.test_client()
    with p.session_transaction() as s:
        s.update({'user_id': uid, 'role': 'user', 'employee_id': 2})
    start = (date.today() + timedelta(days=10)).isoformat()
    end = (date.today() + timedelta(days=12)).isoformat()
    r = p.post('/portal/api/request-leave', json={'leave_type_id': _lt(conn, 'annual')['id'],
                                                  'start_date': start, 'end_date': end, 'reason': 'x'})
    assert r.status_code == 400 and '9' in r.get_json()['message']


def test_probation_over_one_hundred_working_days_is_refused(web):
    c, conn, A = web
    emp = dict(conn.execute('SELECT * FROM employees WHERE id = 1').fetchone())
    form = {k: ('' if v is None else str(v)) for k, v in emp.items()}
    form.update({'hire_date': '2026-08-02', 'probation_end_date': '2027-01-31'})
    c.post('/employees/edit/1', data=form)
    assert conn.execute('SELECT probation_end_date FROM employees WHERE id = 1').fetchone()[0] is None
    form['probation_end_date'] = '2026-10-15'
    c.post('/employees/edit/1', data=form)
    assert conn.execute('SELECT probation_end_date FROM employees WHERE id = 1').fetchone()[0] == '2026-10-15'


def test_a_salary_under_the_minimum_wage_warns(web):
    c, conn, A = web
    emp = dict(conn.execute('SELECT * FROM employees WHERE id = 1').fetchone())
    form = {k: ('' if v is None else str(v)) for k, v in emp.items()}
    form['salary'] = '60'
    r = c.post('/employees/edit/1', data=form, follow_redirects=True)
    # الرسائلُ تُكتب في الصفحة عبر tojson، فالعربيّةُ فيها مُهرَّبة.
    assert json.dumps('الحد الأدنى للأجر')[1:-1] in r.get_data(as_text=True)
    form['salary'] = '300'
    r = c.post('/employees/edit/1', data=form, follow_redirects=True)
    assert json.dumps('الحد الأدنى للأجر')[1:-1] not in r.get_data(as_text=True)


def test_notice_pay_is_saved_and_printed(web):
    c, conn, A = web
    c.post('/eos/terminate', data={'employee_id': '1', 'termination_date': '2026-01-01',
                                   'reason': 'termination', 'notes': '', 'leave_days': '0',
                                   'notice_served_days': '30'})
    rec = conn.execute('SELECT * FROM end_of_service_records').fetchone()
    assert json.loads(rec['breakdown_json'])['notice_amount'] == pytest.approx(520.0)
    body = c.get(f"/eos/print/{rec['id']}").get_data(as_text=True)
    assert 'بدل الإنذار' in body and '520.000' in body


def test_the_shift_api_saves_the_break_and_warns(web):
    c, conn, A = web
    r = c.post('/api/shifts/add', json={'name': 'N', 'start_time': '07:00', 'end_time': '17:00',
                                         'hours_per_day': 10}).get_json()
    assert r['success'] and len(r['warnings']) == 2
    sid = conn.execute("SELECT id FROM shift_types WHERE name = 'N'").fetchone()[0]
    r = c.post('/api/shifts/update', json={'id': sid, 'name': 'N', 'start_time': '07:00',
                                            'end_time': '16:00', 'hours_per_day': 8,
                                            'break_minutes': 60}).get_json()
    assert r['success'] and r['warnings'] == []
    assert conn.execute('SELECT break_minutes FROM shift_types WHERE id = ?', (sid,)).fetchone()[0] == 60


def test_the_compliance_page_and_the_payroll_banner(web):
    c, conn, A = web
    _set(conn, overtime_round_to_minutes=15, missing_punch_policy='invalid')
    body = c.get('/settings/labor-law').get_data(as_text=True)
    assert 'data-code="ot_rounding"' in body and 'lc-bad' in body
    from utils.labor_law import compliance_checks
    for cap, ok in (('0', False), ('10', False), ('5', True), ('3', True)):
        _set(conn, penalty_monthly_cap_days=cap)
        assert next(x for x in compliance_checks(conn) if x['code'] == 'penalty_cap')['ok'] is ok, cap
    assert '15 دقيقة' in body and 'x.lc_' not in body
    assert 'id="lawBanner"' in c.get('/payroll/monthly').get_data(as_text=True)


def test_a_compliant_setup_shows_no_banner(web):
    c, conn, A = web
    from utils.labor_law import compliance_checks
    conn.execute("UPDATE shift_types SET break_minutes = 60")
    y = date.today().year
    conn.execute("INSERT INTO ramadan_periods (start_date, end_date) VALUES (?, ?)",
                 (f'{y + 1}-02-08', f'{y + 1}-03-09'))
    for i in range(20):
        conn.execute("INSERT INTO official_holidays (name, date, type) VALUES ('ع', ?, 'رسمية')",
                     (f'{y}-11-{i + 1:02d}',))
    conn.commit()
    bad = [x['code'] for x in compliance_checks(conn) if not x['ok']]
    assert bad == [], bad
    assert 'id="lawBanner"' not in c.get('/payroll/monthly').get_data(as_text=True)


def test_the_settings_page_saves_the_law_settings(web):
    c, conn, A = web
    body = c.get('/settings').get_data(as_text=True)
    assert 'حصة الموظف %' in body and '%%' not in body
    for name in ('penalty_monthly_cap_days', 'pifss_enabled', 'pifss_employee_pct',
                 'pifss_employer_pct', 'pifss_salary_cap'):
        assert f'name="{name}"' in body, name
    from utils.settings_utils import get_salary_settings_v2
    c.post('/settings/update', data={'penalty_monthly_cap_days': '3', 'pifss_enabled': '1'})
    s = get_salary_settings_v2(conn)
    assert (s['penalty_monthly_cap_days'], s['pifss_enabled']) == ('3', '1')


# ================================================== 2.19: ما بقي حتى المطابقة الكاملة

def _check(conn, code):
    from utils.labor_law import compliance_checks
    return next(x for x in compliance_checks(conn) if x['code'] == code)


def test_every_employee_needs_a_weekly_rest_day(env):
    conn, pe, L = env
    assert _check(conn, 'weekly_rest')['ok']
    conn.execute("UPDATE employees SET weekly_leave_days = 0, weekly_leave_selected_days = NULL WHERE id = 1")
    conn.commit()
    c = _check(conn, 'weekly_rest')
    assert not c['ok'] and c['params']['n'] == 1


def test_scheduled_weeks_over_48_hours_are_flagged(env):
    conn, pe, L = env
    assert _check(conn, 'week_48')['ok'], '8 × 5 = 40'
    conn.execute("UPDATE employees SET weekly_leave_days = 1, weekly_leave_selected_days = NULL WHERE id = 1")
    conn.commit()
    assert _check(conn, 'week_48')['ok'], '8 × 6 = 48 — الحدّ نفسه'
    conn.execute("UPDATE shift_types SET hours_per_day = 9 WHERE name = 'S'")
    conn.commit()
    assert not _check(conn, 'week_48')['ok'], '9 × 6 = 54'


def test_ramadan_days_require_six_hours_and_warn_above(env):
    conn, pe, L = env
    days = _workdays(pe, conn)
    for day in days[:2]:
        _punch(conn, 1, day, '08:00', '16:00')
    before = pe.compute_month_metrics(conn, MONTH, YEAR)[1]['required_hours']
    conn.execute('INSERT INTO ramadan_periods (start_date, end_date) VALUES (?, ?)', (days[0], days[4]))
    conn.commit()
    m = pe.compute_month_metrics(conn, MONTH, YEAR)[1]
    assert m['required_hours'] == pytest.approx(before - 5 * 2), 'خمسةُ أيّام × ساعتان'
    assert m['ramadan_over_6h'] == 2
    w = next(w for w in _row(pe, conn)['law_warnings'] if w['code'] == 'ramadan_6h')
    assert w['n'] == 2


def test_ninety_overtime_days_a_year(env):
    conn, pe, L = env
    conn.execute("INSERT INTO payroll_hours_approvals (employee_id, month, year, ot_days)"
                 " VALUES (1, 3, ?, 89)", (YEAR,))
    conn.commit()
    days = _workdays(pe, conn)
    _punch(conn, 1, days[0], '08:00', '16:30')
    assert 'ot_year_90d' not in _codes(_row(pe, conn)), '89 + 1 = 90'
    _punch(conn, 1, days[1], '08:00', '16:30')
    w = next(w for w in _row(pe, conn)['law_warnings'] if w['code'] == 'ot_year_90d')
    assert w['n'] == 91


def test_attestation_stores_the_overtime_days(env):
    conn, pe, L = env
    for day in _workdays(pe, conn)[:3]:
        _punch(conn, 1, day, '08:00', '17:00')
    admin = conn.execute("SELECT id FROM users WHERE role='admin'").fetchone()[0]
    pe.attest_hours(conn, MONTH, YEAR, [{'employee_id': 1}], admin)
    conn.commit()
    assert conn.execute('SELECT ot_days FROM payroll_hours_approvals WHERE employee_id = 1 '
                        'AND month = ? AND year = ?', (MONTH, YEAR)).fetchone()[0] == 3


def test_nursing_hours_are_not_lateness(env):
    conn, pe, L = env
    _emp(conn, 2, '2020-01-01', gender='female')
    _emp(conn, 3, '2020-01-01', gender='male')
    conn.execute("UPDATE employees SET nursing_until = '2026-12-31' WHERE id IN (2, 3)")
    conn.commit()
    day = _workdays(pe, conn)[0]
    for eid in (2, 3):
        _punch(conn, eid, day, '08:30', '15:00')        # 30 د تأخير + 60 د انصراف
    m = pe.compute_month_metrics(conn, MONTH, YEAR)
    assert (m[2]['late_mins'], m[2]['early_mins']) == (0, 0)
    assert (m[3]['late_mins'], m[3]['early_mins']) == (30, 60), 'للعاملة وحدها'
    assert m[2]['required_hours'] == pytest.approx(m[3]['required_hours'] - 2 * m[2]['required_days'])
    dd = {d['date']: d for d in pe.compute_employee_days(conn, 2, MONTH, YEAR)['days']}
    assert dd[day]['late_mins'] == 0 and dd[day]['early_mins'] == 0


def test_nursing_waives_two_hours_at_most_and_ends_on_its_date(env):
    conn, pe, L = env
    assert L.nursing_waive(90, 60) == (0, 30)
    assert L.nursing_waive(0, 150) == (0, 30)
    _emp(conn, 2, '2020-01-01', gender='female')
    conn.execute("UPDATE employees SET nursing_until = '2026-07-31' WHERE id = 2")
    conn.commit()
    _punch(conn, 2, _workdays(pe, conn)[0], '08:30', '16:00')
    assert pe.compute_month_metrics(conn, MONTH, YEAR)[2]['late_mins'] == 30


def test_sick_days_inside_annual_leave_count_as_sick_and_return_to_the_balance(env):
    conn, pe, L = env
    days = _workdays(pe, conn, weeks=2)
    ann, sick = _lt(conn, 'annual')['id'], _lt(conn, 'sick')['id']
    conn.execute("INSERT INTO leave_requests (employee_id, leave_type_id, start_date, end_date, days_count,"
                 " status, is_paid_leave) VALUES (1, ?, ?, ?, 10, 'approved', 1)", (ann, days[0], days[9]))
    from utils.leave_balance import compute_leave_balance
    conn.commit()
    before = compute_leave_balance(conn, 1, as_of=days[9])
    conn.execute("INSERT INTO leave_requests (employee_id, leave_type_id, start_date, end_date, days_count,"
                 " status, is_paid_leave) VALUES (1, ?, ?, ?, 2, 'approved', 1)", (sick, days[1], days[2]))
    conn.commit()
    after = compute_leave_balance(conn, 1, as_of=days[9])
    assert after['sick_returned'] == 2
    assert after['balance'] == pytest.approx(before['balance'] + 2)
    assert pe.compute_month_metrics(conn, MONTH, YEAR)[1]['sick_days'] == 2
    dd = {d['date']: d for d in pe.compute_employee_days(conn, 1, MONTH, YEAR)['days']}
    assert dd[days[1]]['status'] == 'leave_sick' and dd[days[3]]['status'] == 'leave_paid'


def test_leave_days_follow_the_employees_own_rest_days(env):
    conn, pe, L = env
    from utils.leave_utils import calculate_actual_leave_days
    fri = next(date(2026, 8, 1) + timedelta(days=i) for i in range(7)
               if (date(2026, 8, 1) + timedelta(days=i)).isoweekday() == 5)
    sat = fri + timedelta(days=1)
    assert calculate_actual_leave_days(conn, fri.isoformat(), sat.isoformat()) == 0
    assert calculate_actual_leave_days(conn, fri.isoformat(), sat.isoformat(), 1) == 0
    conn.execute("UPDATE employees SET weekly_leave_start = 5, weekly_leave_days = 1,"
                 " weekly_leave_selected_days = NULL WHERE id = 1")
    conn.commit()
    assert calculate_actual_leave_days(conn, fri.isoformat(), sat.isoformat(), 1) == 1, 'السبتُ يومُ عمله'


def test_loan_installments_are_capped_at_ten_percent(env):
    conn, pe, L = env
    for day in _workdays(pe, conn, weeks=4):
        _punch(conn, 1, day, '08:00', '16:00')   # شهرٌ مُداوَم: الأجرُ مستحقّ
    conn.execute("INSERT INTO employee_loans (employee_id, principal, monthly_installment, start_month,"
                 " start_year, status) VALUES (1, 500, 100, 1, 2026, 'active')")
    conn.commit()
    row = _row(pe, conn)
    assert _item(row['deductions'], 'loan_installment')['amount'] == pytest.approx(26.0)
    w = next(w for w in row['law_warnings'] if w['code'] == 'loan_capped')
    assert w['amount'] == pytest.approx(74.0)
    _set(conn, loan_deduction_cap_pct=0)
    assert _item(_row(pe, conn)['deductions'], 'loan_installment')['amount'] == pytest.approx(100.0)


def test_small_installments_are_untouched(env):
    conn, pe, L = env
    for day in _workdays(pe, conn, weeks=4):
        _punch(conn, 1, day, '08:00', '16:00')   # شهرٌ مُداوَم: الأجرُ مستحقّ
    conn.execute("INSERT INTO employee_loans (employee_id, principal, monthly_installment, start_month,"
                 " start_year, status) VALUES (1, 500, 20, 1, 2026, 'active')")
    conn.commit()
    row = _row(pe, conn)
    assert _item(row['deductions'], 'loan_installment')['amount'] == pytest.approx(20.0)
    assert 'loan_capped' not in _codes(row)


@pytest.mark.parametrize('hire, days', [('2019-01-01', 5 * 10 + 2 * 15),   # 7 سنوات
                                        ('1980-01-01', 312)])              # بلغ أجرَ سنة
def test_daily_paid_end_of_service(env, hire, days):
    conn, pe, L = env
    # لليومية: الراتبُ أجرُ يوم (10)، ويعادل شهريًّا 10 × 26 = 260
    conn.execute("UPDATE employees SET pay_type = 'daily', salary = 10, hire_date = ? WHERE id = 1", (hire,))
    conn.commit()
    from routes.eos_routes import compute_kuwait_eos
    c = compute_kuwait_eos(conn, 1, '2026-01-01', 'termination', leave_days=0)
    assert c['pay_type'] == 'daily'
    assert c['monthly_wage'] == pytest.approx(260) and c['daily_rate'] == pytest.approx(10)
    assert c['pay_days'] == pytest.approx(days, abs=0.01)
    assert c['gratuity'] == pytest.approx(days * 10, abs=0.05)


def test_monthly_paid_is_unchanged(env):
    conn, pe, L = env
    from routes.eos_routes import compute_kuwait_eos
    c = compute_kuwait_eos(conn, 1, '2027-01-01', 'termination', leave_days=0)
    assert c['pay_type'] == 'monthly' and c['pay_days'] == pytest.approx(5 * 15 + 2 * 26, abs=0.01)


@pytest.mark.parametrize('basis, daily', [('26', 10.0), ('30', 260 / 30)])
def test_the_old_salary_reports_use_the_same_daily_basis(env, basis, daily):
    conn, pe, L = env
    _set(conn, daily_rate_basis=basis)
    from utils.salary_utils import calculate_salary_for_employee
    r = calculate_salary_for_employee(conn, 1, MONTH, YEAR, 20, 0, 22, 160)
    assert r['daily_salary'] == pytest.approx(daily, abs=1e-6)


def test_ramadan_periods_are_managed_on_the_check_page(web):
    c, conn, A = web
    c.post('/settings/labor-law/ramadan', data={'start_date': '2027-02-08', 'end_date': '2027-03-09'})
    c.post('/settings/labor-law/ramadan', data={'start_date': '2027-03-01', 'end_date': '2027-03-20'})
    c.post('/settings/labor-law/ramadan', data={'start_date': '2028-01-01', 'end_date': '2028-03-01'})
    c.post('/settings/labor-law/ramadan', data={'start_date': '2028-02-10', 'end_date': '2028-02-01'})
    rows = conn.execute('SELECT id, start_date, end_date FROM ramadan_periods').fetchall()
    assert [(r[1], r[2]) for r in rows] == [('2027-02-08', '2027-03-09')], 'التداخلُ والطولُ والعكسُ مرفوضة'
    assert '2027-02-08' in c.get('/settings/labor-law').get_data(as_text=True)
    c.post(f'/settings/labor-law/ramadan/{rows[0][0]}/delete')
    assert conn.execute('SELECT COUNT(*) FROM ramadan_periods').fetchone()[0] == 0


def test_a_large_loan_installment_warns_on_entry(web):
    c, conn, A = web
    r = c.post('/payroll/loans/add', data={'employee_id': '1', 'principal': '500', 'monthly_installment': '100',
                                           'start_month': '9', 'start_year': '2026'}, follow_redirects=True)
    assert json.dumps('26.000')[1:-1] in r.get_data(as_text=True)


def test_pay_type_and_nursing_are_saved_from_the_form(web):
    c, conn, A = web
    emp = dict(conn.execute('SELECT * FROM employees WHERE id = 1').fetchone())
    form = {k: ('' if v is None else str(v)) for k, v in emp.items()}
    form.update({'pay_type': 'daily', 'nursing_until': '2027-01-31'})
    c.post('/employees/edit/1', data=form)
    r = conn.execute('SELECT pay_type, nursing_until FROM employees WHERE id = 1').fetchone()
    assert (r[0], r[1]) == ('daily', '2027-01-31')
    body = c.get('/employees/edit/1').get_data(as_text=True)
    assert 'name="pay_type"' in body and 'value="2027-01-31"' in body


def test_the_ramadan_and_loan_cap_checks(env):
    conn, pe, L = env
    assert not _check(conn, 'ramadan')['ok']
    conn.execute("INSERT INTO ramadan_periods (start_date, end_date) VALUES (?, ?)",
                 (f'{date.today().year + 1}-02-08', f'{date.today().year + 1}-03-09'))
    conn.commit()
    assert _check(conn, 'ramadan')['ok']
    for pct, ok in (('0', False), ('15', False), ('10', True), ('5', True)):
        _set(conn, loan_deduction_cap_pct=pct)
        assert _check(conn, 'loan_cap')['ok'] is ok, pct


def test_only_working_sick_days_return_to_the_balance(env):
    conn, pe, L = env
    days = _workdays(pe, conn, weeks=2)
    ann, sick = _lt(conn, 'annual')['id'], _lt(conn, 'sick')['id']
    conn.execute("INSERT INTO leave_requests (employee_id, leave_type_id, start_date, end_date, days_count,"
                 " status, is_paid_leave) VALUES (1, ?, ?, ?, 10, 'approved', 1)", (ann, days[0], days[9]))
    conn.execute("INSERT INTO leave_requests (employee_id, leave_type_id, start_date, end_date, days_count,"
                 " status, is_paid_leave) VALUES (1, ?, ?, ?, 2, 'approved', 1)", (sick, days[4], days[5]))
    conn.commit()
    assert L.sick_inside_annual_days(conn, 1, days[0], days[9]) == 2, 'الخميس والأحد — لا الجمعة والسبت'


def test_the_admin_screen_counts_leave_on_the_employees_rest_days(web):
    c, conn, A = web
    conn.execute("UPDATE employees SET weekly_leave_start = 5, weekly_leave_days = 1,"
                 " weekly_leave_selected_days = NULL WHERE id = 1")
    conn.commit()
    fri = next(date(2026, 8, 1) + timedelta(days=i) for i in range(7)
               if (date(2026, 8, 1) + timedelta(days=i)).isoweekday() == 5)
    _add_leave(c, conn, 'bereavement', fri.isoformat(), (fri + timedelta(days=1)).isoformat())
    assert conn.execute('SELECT days_count FROM leave_requests').fetchone()[0] == 1
