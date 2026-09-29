"""نهاية الخدمة: أسبابُ القانون الكويتيّ كاملةً، وحدُّ الخمس سنوات، والتأمينات.

## ما تغيّر

- **المادة 41:** فصلٌ بلا مكافأة — لا يُقبل بغير بندٍ (أ–هـ) ودليلٍ مكتوب،
  ويظهران في المطبوع. كان كلُّ سببٍ غيرِ الاستقالة يُعطى المكافأةَ كاملة.
- **الاستقالةُ بمكافأةٍ كاملة:** العاملةُ خلال سنةٍ من زواجها، والمستقيلُ
  لإخلال صاحب العمل (المادة 48).
- **حدُّ الخمس سنوات:** «من 3 إلى 5» تشمل الخامسة. والمدّةُ كانت أيّامًا ÷ 365،
  فخمسُ سنواتٍ بالتقويم (فيها سنةٌ كبيسة) صارت 5.003 ونقلت المستقيلَ من
  النصف إلى الثلثين.
- **الكويتيّ:** يُطرح ما سُدّد للتأمينات عن المكافأة، ولا يمسّ الإجازات.

يُشغَّل:  python -m pytest tests/test_eos_kuwait_reasons.py -v
"""
import json
import os
import sqlite3
import sys
from datetime import date

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


# ------------------------------------------------ المدّة والنسبة

@pytest.mark.parametrize('hire, term, years', [
    (date(2021, 1, 1), date(2026, 1, 1), 5.0),       # فيها 2024 الكبيسة
    (date(2016, 3, 15), date(2026, 3, 15), 10.0),
    (date(2023, 3, 1), date(2026, 3, 1), 3.0),
    (date(2020, 2, 29), date(2025, 2, 28), 5.0),
    (date(2021, 1, 1), date(2026, 7, 2), 5.0 + 182 / 365.0),
])
def test_service_years_follow_the_calendar(hire, term, years):
    from routes.eos_routes import _service_years
    assert _service_years(hire, term) == pytest.approx(years, abs=1e-9)


@pytest.mark.parametrize('reason, years, fraction', [
    ('resignation', 2.99, 0.0),
    ('resignation', 3.0, 0.5),
    ('resignation', 5.0, 0.5),            # الخامسة داخل النصف
    ('resignation', 5.01, 2 / 3),
    ('resignation', 9.99, 2 / 3),
    ('resignation', 10.0, 1.0),
    ('resignation_marriage', 0.5, 1.0),
    ('resignation_art48', 1.0, 1.0),
    ('art41', 20.0, 0.0),
    ('termination', 1.0, 1.0),
])
def test_entitlement_fraction(reason, years, fraction):
    from routes.eos_routes import _eos_fraction
    assert _eos_fraction(reason, years) == pytest.approx(fraction)


# ------------------------------------------------ الحساب على قاعدة

@pytest.fixture
def env(tmp_path, monkeypatch):
    import importlib
    monkeypatch.setenv('HR_DATA_DIR', str(tmp_path))
    import utils.db as db
    importlib.reload(db)
    db.init_db()
    conn = sqlite3.connect(os.path.join(str(tmp_path), 'hr_system.db'))
    conn.row_factory = sqlite3.Row
    rows = [(1, '1', 'مستقيل', '2021-01-01', 260, 'Egypt', 'male'),
            (2, '2', 'كويتي', '2016-01-01', 520, 'kuwait', 'male'),
            (3, '3', 'عاملة', '2025-06-01', 260, '', 'female'),
            (4, '4', 'رجل', '2025-06-01', 260, '', 'male')]
    for r in rows:
        conn.execute("INSERT INTO employees (id, employee_number, name, hire_date, salary,"
                     " nationality, gender, is_active, department, position)"
                     " VALUES (?,?,?,?,?,?,?,1,'الإدارة','فنّي')", r)
    conn.commit()
    import routes.eos_routes as R
    importlib.reload(R)
    return conn, R


def test_exactly_five_years_resignation_gets_half(env):
    conn, R = env
    c = R.compute_kuwait_eos(conn, 1, '2026-01-01', 'resignation', leave_days=0)
    assert c['years'] == 5.0
    assert c['fraction'] == 0.5
    # 5 × 15 = 75 يومًا × (260 ÷ 26 = 10) = 750، والنصف 375
    assert c['gross_gratuity'] == 750.0 and c['gratuity'] == 375.0


def test_article_41_pays_no_indemnity_but_keeps_leave(env):
    conn, R = env
    c = R.compute_kuwait_eos(conn, 1, '2026-01-01', 'art41', leave_days=10)
    assert c['gratuity'] == 0.0
    assert c['leave_amount'] == 100.0, 'رصيدُ الإجازات حقٌّ مكتسب لا يُسقطه الفصل'
    assert c['net'] == 100.0


@pytest.mark.parametrize('reason', ['resignation_marriage', 'resignation_art48'])
def test_the_full_indemnity_resignations(env, reason):
    conn, R = env
    c = R.compute_kuwait_eos(conn, 3, '2026-03-01', reason, leave_days=0)
    plain = R.compute_kuwait_eos(conn, 3, '2026-03-01', 'resignation', leave_days=0)
    assert plain['gratuity'] == 0.0, 'أقلُّ من 3 سنوات'
    assert c['gratuity'] == c['gross_gratuity'] > 0


def test_pifss_is_deducted_from_the_indemnity_only(env):
    conn, R = env
    base = R.compute_kuwait_eos(conn, 2, '2026-01-01', 'termination', leave_days=5)
    c = R.compute_kuwait_eos(conn, 2, '2026-01-01', 'termination', leave_days=5, pifss=1000)
    assert c['is_kuwaiti'] and not R.compute_kuwait_eos(conn, 1, '2026-01-01', 'termination')['is_kuwaiti']
    assert c['gratuity_before_pifss'] == base['gratuity']
    assert c['pifss_deduction'] == 1000.0
    assert c['gratuity'] == pytest.approx(base['gratuity'] - 1000, abs=1e-3)
    assert c['leave_amount'] == base['leave_amount']
    assert c['net'] == pytest.approx(base['net'] - 1000, abs=1e-3)


def test_pifss_never_turns_the_indemnity_negative(env):
    conn, R = env
    c = R.compute_kuwait_eos(conn, 2, '2026-01-01', 'termination', leave_days=5, pifss=10 ** 6)
    assert c['gratuity'] == 0.0
    assert c['pifss_deduction'] == c['gratuity_before_pifss']
    assert c['net'] == c['leave_amount']
    for junk in ('-50', 'abc', ''):
        assert R.compute_kuwait_eos(conn, 2, '2026-01-01', 'termination', pifss=junk)['pifss_deduction'] == 0.0


# ------------------------------------------------ الشاشة والمطبوع

@pytest.fixture
def web(env):
    import importlib
    conn, R = env
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
        s['user_id'] = admin
        s['role'] = 'admin'
        s['username'] = 'admin'
    return c, conn


def _post(c, **kw):
    data = {'employee_id': '1', 'termination_date': '2026-01-01',
            'reason': 'resignation', 'notes': '', 'leave_days': '0'}
    data.update(kw)
    return c.post('/eos/terminate', data=data)


def _records(conn):
    return conn.execute('SELECT * FROM end_of_service_records').fetchall()


@pytest.mark.parametrize('extra', [
    {'art41_clause': '', 'notes': 'تحقيق رقم 5'},        # بلا بند
    {'art41_clause': 'z', 'notes': 'تحقيق رقم 5'},       # بندٌ ليس في المادة
    {'art41_clause': 'a', 'notes': '   '},               # بلا دليل
])
def test_article_41_is_refused_without_clause_and_evidence(web, extra):
    c, conn = web
    _post(c, reason='art41', **extra)
    assert _records(conn) == []
    assert conn.execute('SELECT is_active FROM employees WHERE id=1').fetchone()[0] == 1


def test_an_unknown_reason_is_refused(web):
    c, conn = web
    _post(c, reason='whatever')
    assert _records(conn) == []


def test_marriage_resignation_is_for_female_workers(web):
    c, conn = web
    _post(c, employee_id='4', termination_date='2026-03-01', reason='resignation_marriage')
    assert _records(conn) == []
    _post(c, employee_id='3', termination_date='2026-03-01', reason='resignation_marriage')
    (rec,) = _records(conn)
    assert rec['fraction'] == 1.0 and rec['gratuity_amount'] > 0


def test_article_41_is_saved_and_printed_with_its_clause(web):
    c, conn = web
    _post(c, reason='art41', art41_clause='b', notes='محضر تحقيق 12/2026', leave_days='3')
    (rec,) = _records(conn)
    assert rec['reason'] == 'art41' and rec['gratuity_amount'] == 0.0
    assert rec['net_payout'] == 30.0
    assert json.loads(rec['breakdown_json'])['art41_clause'] == 'b'

    body = c.get(f"/eos/print/{rec['id']}").get_data(as_text=True)
    assert 'المادة 41' in body
    assert 'مخلّ بالشرف' in body, 'البند'
    assert 'محضر تحقيق 12/2026' in body, 'الدليل'
    assert 'x.art41' not in body and 'x.eos_' not in body


def test_pifss_reaches_the_record_and_the_print(web):
    c, conn = web
    _post(c, employee_id='2', reason='termination', pifss_deduction='400')
    (rec,) = _records(conn)
    b = json.loads(rec['breakdown_json'])
    assert b['pifss_deduction'] == 400.0
    assert rec['gratuity_amount'] == pytest.approx(b['gratuity_before_pifss'] - 400, abs=1e-3)
    body = c.get(f"/eos/print/{rec['id']}").get_data(as_text=True)
    assert 'التأمينات' in body and '400.000' in body


def test_the_calculator_api_takes_pifss(web):
    c, _ = web
    r = c.get('/api/eos/calculate?employee_id=2&termination_date=2026-01-01'
              '&reason=termination&leave_days=0&pifss_deduction=100').get_json()
    assert r['calc']['pifss_deduction'] == 100.0 and r['calc']['is_kuwaiti'] is True


@pytest.mark.parametrize('reason, label', [
    ('termination', 'فصل / إقالة'), ('death', 'وفاة'), ('resignation_art48', 'المادة 48'),
])
def test_every_reason_prints_its_label_not_its_key(web, reason, label):
    c, conn = web
    _post(c, reason=reason)
    (rec,) = _records(conn)
    body = c.get(f"/eos/print/{rec['id']}").get_data(as_text=True)
    assert label in body and f'x.{reason}' not in body
    assert label in c.get('/eos/list').get_data(as_text=True)


def test_the_form_offers_the_new_reasons(web):
    c, _ = web
    body = c.get('/eos/terminate').get_data(as_text=True)
    for v in ('art41', 'resignation_marriage', 'resignation_art48'):
        assert f'value="{v}"' in body
    assert 'name="art41_clause"' in body and 'name="pifss_deduction"' in body
