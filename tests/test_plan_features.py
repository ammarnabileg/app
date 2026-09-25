"""مزايا الخطّة: أيُّ الشاشات يفتحها الاشتراك.

الرموز **موقَّعة فعلًا** (من test_plan_limits)، والطلبات على التطبيق كاملًا.

ما يُقاس:
  * خطّةُ «حضور»: الرواتب والسلف ونهاية الخدمة والتطبيق الميدانيّ مقفولة —
    صفحةٌ تشرح وتدلّ على الترقية، وواجهاتُها JSON بسبب.
  * الأساسُ لا يُقفل: الحضور، وأنواعُ الورديات (داخل blueprint الرواتب
    القديم)، والعطلُ الرسميّة، وتقريرُ الحضور المفصّل.
  * الرخصُ القديمة (بلا الحقل) لا قيدَ عليها، ولا رمزٌ لمفتاحٍ آخر.
  * الخريطةُ لا تشير إلى شاشةٍ غير موجودة — اسمٌ مكتوبٌ خطأً يترك الشاشةَ مفتوحة.

يُشغَّل:  python -m pytest tests/test_plan_features.py -v
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from test_plan_limits import NEXT_YEAR, le2, lic, token, web  # noqa: F401 — fixtures

ATTENDANCE = ['attendance', 'leaves', 'excuses', 'reports', 'portal']
PRO = ATTENDANCE + ['payroll', 'payroll_days', 'eos', 'loans', 'whatsapp']


@pytest.fixture(autouse=True)
def fresh_cache():
    """الكاش دقيقة: يُفرَّغ قبل كلّ حالةٍ وبعدها فلا تتسرّب خطّةٌ إلى غيرها."""
    import utils.plan_features as F
    F.invalidate()
    yield
    F.invalidate()


def hold(env, features, key=None):
    L = env['L']
    k = le2(L, NEXT_YEAR)
    L.save_license_key(k, 'acme')
    extra = {} if features is ... else {'features': features}
    env['db'].set_setting('license_token', token(key or k, NEXT_YEAR, **extra))
    import utils.plan_features as F
    F.invalidate()


def locked(resp):
    return resp.status_code == 403 and (
        b'feature-locked' in resp.data or b'feature_not_in_plan' in resp.data)


# ------------------------------------------------ خطّة الحضور

@pytest.mark.parametrize('path', [
    '/payroll/monthly', '/payroll/ledger', '/payroll/loans', '/eos/list', '/field',
    '/salaries', '/fingerprint/salary_reports', '/leave_settings',
])
def test_attendance_plan_locks_paid_screens(web, path):
    hold(web, ['attendance', 'reports', 'portal'])
    r = web['c'].get(path)
    assert locked(r), (path, r.status_code)


def test_the_locked_page_explains_and_links_to_upgrade(web):
    hold(web, ATTENDANCE)
    body = web['c'].get('/payroll/monthly').get_data(as_text=True)
    assert 'id="feature-locked"' in body
    assert 'data-feature="payroll"' in body
    assert 'onz.one/client/subscription' in body


def test_locked_apis_answer_json_with_the_reason(web):
    hold(web, ATTENDANCE)
    r = web['c'].get('/api/payroll/monthly?month=9&year=2026')
    assert r.status_code == 403
    j = r.get_json()
    assert j['error'] == 'feature_not_in_plan' and j['feature'] == 'payroll'
    assert j['upgrade_url'].endswith('/client/subscription')


def test_loans_are_their_own_feature(web):
    hold(web, [f for f in PRO if f != 'loans'])
    assert locked(web['c'].get('/payroll/loans'))
    assert not locked(web['c'].get('/payroll/monthly'))


@pytest.mark.parametrize('path', [
    '/shift_types',                                        # داخل salary_bp — وهو حضور
    '/official_holidays',                                  # داخل leave_bp — يقرؤه الحضور
    '/fingerprint/employee_report/api?employee_id=1&month=9&year=2026',   # تقريرُ الحضور
    '/employees', '/license',
])
def test_core_screens_stay_open_on_the_smallest_plan(web, path):
    hold(web, ['attendance'])
    r = web['c'].get(path)
    assert not locked(r), (path, r.status_code)


def test_attendance_is_core_even_if_a_plan_omits_it(web):
    import utils.plan_features as F
    hold(web, ['payroll'])
    assert F.has_feature('attendance') and F.has_feature('reports')
    assert not F.has_feature('leaves')


def test_pro_opens_payroll_but_not_the_field_app(web):
    hold(web, PRO)
    assert not locked(web['c'].get('/payroll/monthly'))
    assert not locked(web['c'].get('/eos/list'))
    assert locked(web['c'].get('/field'))


def test_portal_requests_follow_their_features(web):
    hold(web, ['attendance', 'portal', 'excuses'])
    r = web['c'].post('/portal/api/request-leave', json={})
    assert r.status_code == 403 and r.get_json()['feature'] == 'leaves'
    r = web['c'].post('/portal/api/request-excuse', json={})
    assert not locked(r)
    r = web['c'].get('/portal/api/punch-status')
    assert not locked(r), 'البصمةُ أساس'


# ------------------------------------------------ بلا قيد

def test_a_legacy_license_without_the_field_opens_everything(web):
    hold(web, ...)
    for path in ('/payroll/monthly', '/field', '/eos/list'):
        assert not locked(web['c'].get(path)), path


def test_a_token_for_another_key_restricts_nothing(web):
    hold(web, ['attendance'], key='LE2-someone-else')
    assert not locked(web['c'].get('/payroll/monthly'))


def test_no_license_token_restricts_nothing(web):
    assert not locked(web['c'].get('/payroll/monthly'))


def test_an_upgrade_shows_after_the_cache_is_cleared(web):
    import utils.plan_features as F
    hold(web, ATTENDANCE)
    assert locked(web['c'].get('/payroll/monthly'))
    web['db'].set_setting('license_token', token(web['L'].get_saved_license_key(), NEXT_YEAR, features=PRO))
    web['L'].invalidate_license_cache()      # كما يفعل الفحصُ حين يصل رمزٌ جديد
    assert not locked(web['c'].get('/payroll/monthly'))
    assert F.licensed_features() == frozenset(PRO)


# ------------------------------------------------ الخريطة نفسها

def test_the_map_names_only_real_screens(web):
    import app as A
    import utils.plan_features as F
    endpoints = {r.endpoint for r in A.app.url_map.iter_rules()}
    rules = {r.rule for r in A.app.url_map.iter_rules()}
    assert set(F.ENDPOINT_FEATURE) <= endpoints, set(F.ENDPOINT_FEATURE) - endpoints
    assert set(F.BLUEPRINT_FEATURE) <= set(A.app.blueprints), set(F.BLUEPRINT_FEATURE) - set(A.app.blueprints)
    assert F.OPEN_RULES <= rules


def test_every_payroll_screen_is_covered(web):
    """شاشةُ رواتب جديدة تُضاف إلى blueprint الرواتب تُقفل تلقائيًّا."""
    import app as A
    import utils.plan_features as F
    for r in A.app.url_map.iter_rules():
        if r.endpoint.startswith(('payroll.', 'eos.', 'field.')):
            assert F.feature_for(r.endpoint, r.rule) is not None, r.endpoint


def test_the_cache_does_not_cross_installations(web, tmp_path, monkeypatch):
    """خطّةُ تركيبٍ في الكاش لا تسري على تركيبٍ آخر في العمليّة نفسها."""
    import utils.plan_features as F
    hold(web, ['attendance'])
    assert F.licensed_features() == frozenset({'attendance'})
    other = tmp_path / 'other'
    other.mkdir()
    monkeypatch.setenv('HR_DATA_DIR', str(other))
    import importlib
    import utils.db as d
    importlib.reload(d)
    d.init_db()
    assert F.licensed_features() is None
