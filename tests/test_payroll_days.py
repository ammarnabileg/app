"""كشفُ الرواتب يومًا بيوم — ما عدّه المحرّك، لا حسابٌ ثانٍ له.

## لماذا الفرق لا إعادةُ الحساب

`compute_month_metrics` تمشي الشهرَ يومًا يومًا وتجمع في عدّادات
الشهر. فصفُّ اليوم هو **ما أضافه ذلك اليومُ** إلى العدّادات — لقطةٌ
قبل اليوم وأخرى بعده. ولا يُكتب فرعٌ ثانٍ يصنّف اليوم ويعدّه: نسختان
من قاعدةٍ واحدة تفترقان يومًا، ولا يُعرف أيُّهما الصحيحة.

فالقلبُ هنا: **مجموعُ الأيّام يساوي الشهر** — لكلّ عدّادٍ يُعدّ في
الدورة. و`unentitled_rest_days` وحده يُحسب بعدها أسبوعًا أسبوعًا،
فليس لأيّ يومٍ نصيبٌ منه، ولا يُدّعى له.

## والمالُ شهريٌّ

البصمةُ الناقصة متدرّجةٌ بترتيبها في الشهر، وجزاءُ التواجد والإضافيّ
مسقوفان شهريًّا، وشرائحُ المرضيّة تتبع أشهرًا سابقة. فلا رقمَ صحيحًا
لمال اليوم الواحد، والجدولُ لا يحمله.

يُشغَّل:  python -m pytest tests/test_payroll_days.py -v
"""
import json
import os
import sqlite3
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

MONTH, YEAR = 3, 2026   # مارس ٢٠٢٦ يبدأ بالأحد
# الراحةُ الافتراضيّة الجمعة والسبت.


@pytest.fixture
def live(tmp_path, monkeypatch):
    import importlib

    monkeypatch.setenv('HR_DATA_DIR', str(tmp_path))
    import utils.db as db
    importlib.reload(db)
    db.init_db()

    import utils.cloud_outbox as ob
    import utils.cloud_sync as cs
    import utils.payroll_engine as pe
    importlib.reload(ob)
    importlib.reload(cs)

    conn = sqlite3.connect(os.path.join(str(tmp_path), 'hr_system.db'))
    conn.row_factory = sqlite3.Row
    _month(conn)
    yield conn, pe, ob, cs
    conn.close()


def _punch(conn, emp, day, *times):
    for t in times:
        conn.execute(
            'INSERT INTO attendance_records (employee_id, device_id, check_time,'
            ' check_type) VALUES (?,?,?,?)',
            (emp, 'T1', f'2026-03-{day:02d} {t}:00', 'I'))


def _month(conn):
    """شهرٌ فيه كلُّ فرعٍ من فروع اليوم — لا شهرٌ عاديّ.

    الأوّل عُيّن في العاشر (فقبله «خارج الخدمة»)، وله إجازاتٌ ثلاث
    الأنواع، وعذرٌ ليومٍ كامل، وغياب، وبصمةٌ ناقصة، وتأخير، وانصرافٌ
    مبكّر، وإضافيّ، وعملٌ في الراحة وفي العطلة، وإذنٌ بالساعة.
    والثاني شهرُه كاملٌ بنافذة تواجدٍ لا يبصم فيها.
    """
    conn.execute(
        "INSERT INTO shift_types (name, start_time, end_time, hours_per_day,"
        " presence_start_time, presence_end_time, flex_mode, is_active)"
        " VALUES ('صباحي-اختبار', '08:00', '16:00', 8, NULL, NULL, 'none', 1)")
    conn.execute(
        "INSERT INTO shift_types (name, start_time, end_time, hours_per_day,"
        " presence_start_time, presence_end_time, flex_mode, is_active)"
        " VALUES ('تواجد-اختبار', '08:00', '16:00', 8, '12:00', '13:00', 'none', 1)")
    for eid, num, hire, shift in ((1, '1', '2026-03-10', 'صباحي-اختبار'),
                                  (2, '2', '2025-01-01', 'تواجد-اختبار')):
        conn.execute(
            'INSERT INTO employees (id, employee_number, name, department,'
            ' position, hire_date, salary, is_active, shift_type)'
            ' VALUES (?,?,?,?,?,?,?,1,?)',
            (eid, num, f'موظف {num}', 'الإدارة', 'محاسب', hire, 3000, shift))

    conn.execute("INSERT INTO official_holidays (name, date, type, is_paid)"
                 " VALUES ('عطلة', '2026-03-19', 'official', 1)")

    types = {}
    for name, paid in (('مرضية اختبار', 1), ('سنوية اختبار', 1), ('بدون راتب اختبار', 0),
                       ('إذن اختبار', 1)):
        cur = conn.execute('INSERT INTO leave_types (name, days_per_year, is_paid)'
                           ' VALUES (?,?,?)', (name, 30, paid))
        types[name] = cur.lastrowid
    for tname, s, e, paid, kind, st, et in (
            ('مرضية اختبار', '2026-03-11', '2026-03-12', 1, 'full', None, None),
            ('سنوية اختبار', '2026-03-15', '2026-03-15', 1, 'full', None, None),
            ('بدون راتب اختبار', '2026-03-16', '2026-03-16', 0, 'full', None, None),
            ('إذن اختبار', '2026-03-26', '2026-03-26', 1, 'hourly', '10:00', '12:00')):
        conn.execute(
            'INSERT INTO leave_requests (employee_id, leave_type_id, start_date,'
            ' end_date, days_count, leave_duration_type, start_time, end_time,'
            " status, is_paid_leave) VALUES (1,?,?,?,1,?,?,?,'approved',?)",
            (types[tname], s, e, kind, st, et, paid))

    conn.execute("INSERT INTO attendance_excuses (employee_id, date, reason, type,"
                 " side) VALUES (1, '2026-03-17', 'مأمورية', 'full', 'in')")

    # الأوّل. ١٨ غياب، ١٩ عطلة عمل فيها، ٢٢ بصمةٌ واحدة، ٢٣ تأخير،
    # ٢٤ انصرافٌ مبكّر، ٢٥ إضافيّ، ٢٦ إذنٌ بالساعة، ٢٧ جمعةٌ عمل فيها.
    _punch(conn, 1, 10, '08:00', '16:00')
    _punch(conn, 1, 19, '09:00', '12:00')
    _punch(conn, 1, 22, '08:00')
    _punch(conn, 1, 23, '08:40', '16:00')
    _punch(conn, 1, 24, '08:00', '15:00')
    _punch(conn, 1, 25, '08:00', '18:10')
    _punch(conn, 1, 26, '08:00', '16:00')
    _punch(conn, 1, 27, '09:00', '13:00')
    for day in (29, 30, 31):
        _punch(conn, 1, day, '08:00', '16:00')
    # الثاني: يبصم دخولًا وخروجًا فقط، فيفوته التواجدُ كلَّ يومٍ عمل فيه.
    for day in (1, 2, 3, 4, 5, 8):
        _punch(conn, 2, day, '07:55', '16:05')
    conn.commit()


def _metrics(conn, pe, sink=None):
    return pe.compute_month_metrics(conn, MONTH, YEAR, day_sink=sink)


def _days(sink, emp):
    return [r for r in sink if r['employee_id'] == emp]


def _by_date(sink, emp):
    return {r['date']: r for r in _days(sink, emp)}


def _save(conn, pe):
    pe.attest_hours(conn, MONTH, YEAR,
                    [{'employee_id': 1}, {'employee_id': 2}], 1)
    conn.commit()
    pe.save_monthly_payroll(conn, MONTH, YEAR, 1)
    conn.commit()
    return conn.execute('SELECT id FROM payroll_runs WHERE month = ? AND year = ?',
                        (MONTH, YEAR)).fetchone()['id']


# --------------------------------------------- الشهرُ لم يتغيّر بالمراقبة

def test_the_sink_does_not_change_the_month(live):
    """اللقطةُ تقرأ ولا تكتب. شهرٌ تغيّر لأنه رُوقب يعني أن كلَّ كشفٍ
    حُفظ بعد اليوم غيرُ الذي حُفظ قبله."""
    conn, pe, _ob, _cs = live
    plain = _metrics(conn, pe)
    sink = []
    watched = _metrics(conn, pe, sink)
    assert plain == watched
    assert sink, 'المراقبةُ لم تلتقط يومًا'


# ------------------------------------------------ القلب: الأيّامُ = الشهر

LOOP_FIELDS = [f for f in __import__('utils.payroll_engine', fromlist=['x']).METRIC_FIELDS
               if f != 'unentitled_rest_days']


@pytest.mark.parametrize('emp', [1, 2])
def test_days_sum_to_the_month(live, emp):
    conn, pe, _ob, _cs = live
    sink = []
    month = _metrics(conn, pe, sink)[emp]
    for f in LOOP_FIELDS:
        total = sum(r['delta'].get(f, 0) for r in _days(sink, emp))
        if f in ('required_hours', 'actual_hours', 'hourly_perm_hours'):
            assert total == pytest.approx(month[f], abs=0.01), f
        else:
            assert total == month[f], f


def test_the_fixture_exercises_every_counter(live):
    """«الأيّامُ = الشهر» على عدّادٍ صفرٍ في الطرفين لا يقيس شيئًا.
    فكلُّ عدّادٍ من عدّادات الدورة يجب أن يتحرّك في هذا الشهر."""
    conn, pe, _ob, _cs = live
    m = _metrics(conn, pe)
    moved = {f for f in LOOP_FIELDS if m[1][f] or m[2][f]}
    assert moved == set(LOOP_FIELDS), set(LOOP_FIELDS) - moved


def test_one_row_per_day_per_employee(live):
    conn, pe, _ob, _cs = live
    sink = []
    _metrics(conn, pe, sink)
    for emp in (1, 2):
        dates = [r['date'] for r in _days(sink, emp)]
        assert len(dates) == len(set(dates)) == 31, emp


# ------------------------------------------------------ تصنيفُ اليوم

def test_each_day_is_labelled_by_the_branch_that_counted_it(live):
    conn, pe, _ob, _cs = live
    sink = []
    _metrics(conn, pe, sink)
    d = _by_date(sink, 1)
    k = {ds[-2:]: r['kind'] for ds, r in d.items()}

    assert all(k[f'{x:02d}'] == 'outside' for x in range(1, 10))
    assert k['10'] == 'work'
    assert k['11'] == k['12'] == 'leave_sick'
    assert k['13'] == k['14'] == 'rest'
    assert k['15'] == 'leave_paid'
    assert k['16'] == 'leave_unpaid'
    assert k['17'] == 'excused'
    assert k['18'] == 'absent'
    assert k['19'] == 'holiday'
    assert k['22'] == 'work'        # بصمةٌ ناقصة: يومُ عملٍ بصم فيه
    assert k['27'] == 'rest'
    assert d['2026-03-11']['note'] == 'مرضية اختبار'
    assert d['2026-03-16']['note'] == 'بدون راتب اختبار'


def test_a_day_carries_what_it_added(live):
    """الصفُّ يقول ما حدث في يومه — لا ما حدث في الشهر."""
    conn, pe, _ob, _cs = live
    sink = []
    _metrics(conn, pe, sink)
    d = _by_date(sink, 1)

    assert d['2026-03-18']['delta'] == {'required_days': 1, 'required_hours': 8.0,
                                        'absent_days': 1}
    assert d['2026-03-22']['delta'].get('partial_days') == 1
    assert d['2026-03-22']['punches'] == 1 and d['2026-03-22']['last'] is None
    assert d['2026-03-23']['delta'].get('late_mins', 0) > 0
    assert d['2026-03-24']['delta'].get('early_mins', 0) > 0
    assert d['2026-03-25']['delta'].get('ot_weekday_mins', 0) > 0
    assert d['2026-03-26']['delta'].get('hourly_perm_hours') == 2.0
    assert d['2026-03-27']['delta'].get('ot_weekend_mins', 0) > 0
    assert d['2026-03-19']['delta'].get('ot_holiday_mins', 0) > 0
    assert d['2026-03-10']['first'] == '08:00' and d['2026-03-10']['last'] == '16:00'
    assert d['2026-03-10']['span_hours'] == 8.0
    assert d['2026-03-01']['delta'] == {}           # خارج الخدمة: لم يُعدّ
    assert _by_date(sink, 2)['2026-03-01']['delta'].get('presence_missing') == 1


# --------------------------------------------------------- الحفظ

def test_save_writes_the_days(live):
    conn, pe, _ob, _cs = live
    run_id = _save(conn, pe)
    rows = conn.execute('SELECT * FROM payroll_run_days WHERE run_id = ?'
                        ' ORDER BY employee_id, day', (run_id,)).fetchall()
    # الأوّل: ٣١ يومًا منها ٩ قبل التعيين لا تُكتب. والثاني الشهرُ كلُّه.
    assert sum(1 for r in rows if r['employee_id'] == 1) == 22
    assert sum(1 for r in rows if r['employee_id'] == 2) == 31
    assert not any(r['kind'] == 'outside' for r in rows)

    by = {(r['employee_id'], r['day']): r for r in rows}
    late = by[(1, '2026-03-23')]
    assert late['kind'] == 'work' and late['late_mins'] > 0
    assert json.loads(late['delta_json'])['late_mins'] == late['late_mins']
    ot = by[(1, '2026-03-25')]
    assert ot['ot_mins'] == json.loads(ot['delta_json'])['ot_weekday_mins']
    # والإضافيُّ في الراحة والعطلة من العمود نفسِه — لا يسقط منه.
    fri, hol = by[(1, '2026-03-27')], by[(1, '2026-03-19')]
    assert fri['kind'] == 'rest' and fri['ot_mins'] > 0
    assert fri['ot_mins'] == json.loads(fri['delta_json'])['ot_weekend_mins']
    assert hol['ot_mins'] == json.loads(hol['delta_json'])['ot_holiday_mins'] > 0
    assert by[(2, '2026-03-01')]['presence_missing'] == 1


def test_saved_days_match_the_saved_line(live):
    """الأيّامُ والسطرُ من حسابٍ واحد — فساعاتُ السطر مجموعُ ساعات أيّامه."""
    conn, pe, _ob, _cs = live
    run_id = _save(conn, pe)
    for emp in (1, 2):
        line = conn.execute('SELECT required_hours, actual_hours FROM payroll_run_lines'
                            ' WHERE run_id = ? AND employee_id = ?',
                            (run_id, emp)).fetchone()
        days = conn.execute('SELECT SUM(required_hours) rq, SUM(actual_hours) ac'
                            ' FROM payroll_run_days WHERE run_id = ? AND employee_id = ?',
                            (run_id, emp)).fetchone()
        assert days['ac'] == pytest.approx(line['actual_hours'], abs=0.01), emp
        assert days['rq'] == pytest.approx(line['required_hours'], abs=0.01), emp


def test_resave_replaces_the_days(live):
    """إعادةُ الحفظ بعد تصحيح بصمة: الأيّامُ القديمة تُمحى لا تتراكم."""
    conn, pe, _ob, _cs = live
    run_id = _save(conn, pe)
    before = conn.execute('SELECT COUNT(*) FROM payroll_run_days').fetchone()[0]

    _punch(conn, 1, 18, '08:00', '16:00')     # الغائبُ حضر
    conn.commit()
    assert _save(conn, pe) == run_id
    rows = conn.execute('SELECT * FROM payroll_run_days WHERE run_id = ?',
                        (run_id,)).fetchall()
    assert len(rows) == before
    fixed = [r for r in rows if r['employee_id'] == 1 and r['day'] == '2026-03-18']
    assert len(fixed) == 1 and fixed[0]['kind'] == 'work'


# ------------------------------------------------------- المزامنة

def _drain(conn, cs, ob):
    for _ in range(300):
        batch = cs.build_batch(conn)
        if not batch['baseline_marks']:
            break
        cs.commit_batch(conn, batch)
    ob.ack(conn, ob.high_water(conn, limit=100000))
    conn.row_factory = sqlite3.Row   # `read_rows` تُعيده None


def _collect_many(conn, cs, ob, *tables):
    """ما يصل السحابةَ لكلّ جدول: {جدول: (صفوفٌ بلا تكرار، محذوفات)}.

    الدفعةُ تُثبَّت كلُّها معًا — فجمعُ جدولٍ ثم جمعُ آخر بعده يجد
    الثاني فارغًا لأن الأوّل أقرّ دفعتَه. فالجداولُ المقيسة معًا تُجمع
    في مشيٍ واحد.
    """
    seen = {t: {} for t in tables}
    dels = {t: [] for t in tables}
    for _ in range(80):
        batch = cs.build_batch(conn)
        for t in tables:
            for row in batch['data'].get(t, []):
                seen[t][row['id']] = row
            dels[t].extend(batch['deletes'].get(t, []))
        if not batch['baseline_marks'] and not batch['up_to_seq']:
            break
        cs.commit_batch(conn, batch)
    conn.row_factory = sqlite3.Row   # `read_rows` تُعيده None
    return {t: (list(seen[t].values()), dels[t]) for t in tables}


def _collect(conn, cs, ob, table):
    return _collect_many(conn, cs, ob, table)[table]


def test_saved_days_do_not_leave(live):
    """كشفٌ لم يُعتمد: أيّامُه كسطوره، لا تغادر المقرّ."""
    conn, pe, ob, cs = live
    _save(conn, pe)
    rows, _ = _collect(conn, cs, ob, 'payroll_run_days')
    assert rows == []


def test_approving_sends_the_days(live):
    """الطريقُ الحقيقيّ: حفظٌ (مسودّة) ثم اعتماد.

    الاعتمادُ يُحدّث `payroll_runs` وحده — والأيّامُ نفسُها لم تتغيّر،
    فمشغّلُها لا يسجّلها. ولولا ما يُعيد تسجيلها عند تغيّر حالة الكشف
    لبقيت في المقرّ إلى الأبد: خرجت حذفًا وهي مسودّة، ولا شيء يُخرجها
    بعد الاعتماد.
    """
    conn, pe, ob, cs = live
    run_id = _save(conn, pe)
    _drain(conn, cs, ob)

    pe.lock_run(conn, MONTH, YEAR, 1)
    conn.commit()
    rows, _ = _collect(conn, cs, ob, 'payroll_run_days')
    assert len(rows) == 53
    assert {r['run_id'] for r in rows} == {run_id}


def test_approving_sends_the_lines(live):
    """والعيبُ نفسُه في السطور منذ ٢.١٣ — فيُقاس هنا أيضًا."""
    conn, pe, ob, cs = live
    _save(conn, pe)
    _drain(conn, cs, ob)

    pe.lock_run(conn, MONTH, YEAR, 1)
    conn.commit()
    lines, _ = _collect(conn, cs, ob, 'payroll_run_lines')
    assert {r['employee_id'] for r in lines} == {1, 2}


def test_approving_out_of_order_still_sends(live):
    """الأخطرُ هنا، ولم يكن يُقاس.

    المشيُ الأوّل يرشّح ثم يتقدّم مؤشّرُه إلى آخر صفٍّ **أرسله**. فكشفٌ
    يُعتمد بعد كشفٍ أحدثَ منه (مارس يُراجَع وأبريل اعتُمد) تقع صفوفُه
    تحت المؤشّر — فلا المشيُ يعود إليها، ولا الدفترُ سجّلها لأنها لم
    تتغيّر. وتبقى في المقرّ إلى الأبد، والبوّابةُ تعرض مارسَ بلا سطور.
    """
    conn, pe, ob, cs = live
    march = _save(conn, pe)
    # أبريل: الشهرُ نفسُه منقولًا — يكفي أن تكون صفوفُه أحدث.
    conn.execute("INSERT INTO payroll_runs (month, year, status, employees_count)"
                 " VALUES (4, 2026, 'saved', 1)")
    april = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
    conn.execute("INSERT INTO payroll_run_lines (run_id, employee_id, basic, net)"
                 " VALUES (?, 1, 1, 1)", (april,))
    conn.execute("INSERT INTO payroll_run_days (run_id, employee_id, day, kind)"
                 " VALUES (?, 1, '2026-04-01', 'work')", (april,))
    conn.commit()
    _drain(conn, cs, ob)

    pe.lock_run(conn, 4, 2026, 1)
    conn.commit()
    _collect(conn, cs, ob, 'payroll_run_days')

    pe.lock_run(conn, MONTH, YEAR, 1)
    conn.commit()
    got = _collect_many(conn, cs, ob, 'payroll_run_days', 'payroll_run_lines')
    days, lines = got['payroll_run_days'][0], got['payroll_run_lines'][0]
    assert sum(1 for r in days if r['run_id'] == march) == 53
    assert {r['employee_id'] for r in lines if r['run_id'] == march} == {1, 2}


def test_unlocking_deletes_the_days(live):
    """فكُّ القفل: الأيّامُ تُحذف من السحابة مع كشفها، لا تبقى يتيمة."""
    conn, pe, ob, cs = live
    run_id = _save(conn, pe)
    pe.lock_run(conn, MONTH, YEAR, 1)
    conn.commit()
    rows, _ = _collect(conn, cs, ob, 'payroll_run_days')
    assert len(rows) == 53

    pe.unlock_run(conn, MONTH, YEAR, 1, 'تصحيح بصمة')
    conn.commit()
    got = _collect_many(conn, cs, ob, 'payroll_run_days', 'payroll_run_lines')
    again, dels = got['payroll_run_days']
    assert again == []
    assert {r['id'] for r in rows} <= set(dels)
    lines_again, line_dels = got['payroll_run_lines']
    assert lines_again == [] and len(set(line_dels)) == 2


def test_other_updates_to_the_run_do_not_resend(live):
    """الإعادةُ عند تغيّر **الحالة** وحدها — لا عند كلّ لمسةٍ للكشف،
    وإلّا خرجت ثلاثون صفًّا لكلّ موظّفٍ كلّما تغيّر حقلٌ لا يعنيها."""
    conn, pe, ob, cs = live
    run_id = _save(conn, pe)
    pe.lock_run(conn, MONTH, YEAR, 1)
    conn.commit()
    _drain(conn, cs, ob)
    _collect(conn, cs, ob, 'payroll_run_days')

    # حقلٌ آخر، ثم الحالةُ نفسُها مكتوبةً ثانيةً (إعادةُ الحفظ تفعل ذلك).
    conn.execute('UPDATE payroll_runs SET total_net = total_net WHERE id = ?', (run_id,))
    conn.execute("UPDATE payroll_runs SET status = 'approved' WHERE id = ?", (run_id,))
    conn.commit()
    rows, dels = _collect(conn, cs, ob, 'payroll_run_days')
    assert rows == [] and dels == []


def test_baseline_sends_approved_days_only(live):
    """المشيُ الأوّل لتركيبٍ جديد: المعتمَدُ وحده."""
    conn, pe, ob, _cs = live
    run_id = _save(conn, pe)
    pe.lock_run(conn, MONTH, YEAR, 1)
    conn.execute("INSERT INTO payroll_runs (month, year, status, employees_count)"
                 " VALUES (2, 2026, 'saved', 1)")
    draft = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
    conn.execute("INSERT INTO payroll_run_days (run_id, employee_id, day, kind)"
                 " VALUES (?, 1, '2026-02-01', 'work')", (draft,))
    conn.commit()

    ids = ob.baseline_batch(conn, 'payroll_run_days', size=1000)
    runs = {r[0] for r in conn.execute(
        'SELECT DISTINCT run_id FROM payroll_run_days WHERE id IN (%s)'
        % ','.join('?' * len(ids)), ids)}
    assert len(ids) == 53 and runs == {run_id}


class _Server:
    """خادمٌ يقبل كلَّ دفعة، ويُعلن ما يقبل كما يفعل v87 — أو لا يُعلن
    شيئًا كـv86. ويحفظ ما وصله لكلّ جدول."""

    def __init__(self, accepts=None):
        self.accepts = accepts
        self.got = {}

    def post(self, url, json=None, headers=None, timeout=None):
        for t, rows in (json or {}).get('data', {}).items():
            for r in rows:
                self.got.setdefault(t, {})[r['id']] = r
        body = {'status': 'success', 'stats': {}}
        if self.accepts is not None:
            body['accepts'] = self.accepts
        server = self

        class _R:
            status_code = 200

            def json(self):
                return body
        return _R()


_traffic = iter(range(1, 1000))


def _sync(conn, cs, server):
    """رفعٌ حتى يفرغ الدفتر — بعد بصمةٍ جديدة.

    الإعادةُ تنتظر ردَّ الخادم، والردُّ لا يأتي إلّا مع رفع: دفترٌ
    فارغٌ لا يُرسل شيئًا. وفي التركيب الحقيقيّ البصماتُ تأتي كلَّ يوم
    عمل، فالإعادةُ تقع مع أوّلها. فهنا بصمةٌ قبل كلّ رفع — وإلّا مرّت
    فحوصُ «لم يُعَد» لأنه لم يُرسَل شيءٌ أصلًا.
    """
    conn.execute('INSERT INTO attendance_records (employee_id, device_id,'
                 " check_time, check_type) VALUES (2, 'T1', ?, 'I')",
                 (f'2026-04-01 08:{next(_traffic) % 60:02d}:00',))
    conn.commit()
    before = len(server.got.get('attendance_records', {}))
    for _ in range(80):
        res = cs.run_once(conn=conn, session=server, force=True)
        conn.row_factory = sqlite3.Row
        if not res.get('ok') or res.get('idle'):
            assert len(server.got.get('attendance_records', {})) > before, \
                'لم يصل الخادمَ رفعٌ — الفحصُ بعده لا يقيس شيئًا'
            return res
    raise AssertionError('الرفعُ لم يفرغ')


def _payroll(server):
    return {t: v for t, v in server.got.items() if t.startswith('payroll_')}


def test_resend_waits_for_a_server_that_accepts_payroll(live):
    """تركيبٌ عمل بـ٢.١٣ على خادم v86: كشفٌ معتمَد «رُفع» ورُمي.

    الإعادةُ لا تُطلق إلى خادمٍ قديم — تُرمى ثانيةً وتضيع — بل تنتظر
    خادمًا يُعلن أنه يقبل الرواتب، ثم تقع مرّةً واحدة.
    """
    conn, pe, ob, cs = live
    import utils.db as db
    db.set_setting('cloud_sync_url', 'http://cloud.invalid/')
    db.set_setting('cloud_sync_client_id', 'uuid-1')
    db.set_setting('cloud_sync_api_key', 'k')

    run_id = _save(conn, pe)
    pe.lock_run(conn, MONTH, YEAR, 1)
    conn.commit()
    old = _Server(accepts=None)             # v86: يقبل ويرمي، ولا يُعلن
    assert _sync(conn, cs, old).get('ok')
    assert len(old.got.get('payroll_run_days', {})) == 53, 'الرفعُ الأوّل لم يُخرجها'

    # الآن الخادمُ رمى كلَّ شيء، والدفترُ مُقَرّ: لا شيء يُعاد وحده.
    again = _Server(accepts=None)
    _sync(conn, cs, again)
    assert not _payroll(again), 'خادمٌ قديم تلقّى إعادة — ستُرمى وتضيع'

    partial = _Server(accepts=['employees', 'payroll_runs'])
    _sync(conn, cs, partial)
    assert not _payroll(partial), 'أُعيدت إلى خادمٍ لا يقبل الأيّام'

    new = _Server(accepts=list(ob.SYNC_TABLES))   # v87
    _sync(conn, cs, new)    # الردُّ يكشف القبول، والإعادةُ تُسجَّل وتُرسَل في الدورة نفسِها
    assert set(new.got.get('payroll_runs', {})) == {run_id}, list(new.got)
    assert {r['employee_id'] for r in new.got['payroll_run_lines'].values()} == {1, 2}
    assert len(new.got['payroll_run_days']) == 53

    once = _Server(accepts=list(ob.SYNC_TABLES))
    _sync(conn, cs, once)
    assert not _payroll(once), 'الإعادةُ تتكرّر في كلّ رفع'


def test_resend_skips_drafts(live):
    """الإعادةُ تمرّ بالترشيح: مسودّةٌ لا تخرج ولو أُعيد كلُّ شيء."""
    conn, pe, ob, _cs = live
    _save(conn, pe)                         # مسودّة
    ob.ack(conn, ob.high_water(conn, limit=100000))
    assert ob.resend_when_accepted(conn, list(ob.SYNC_TABLES)) is True
    queued = conn.execute(f'SELECT COUNT(*) FROM {ob.OUTBOX_TABLE}').fetchone()[0]
    assert queued == 0
