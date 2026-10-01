"""زرّا «ارفع الآن» و«أرسل الآن».

## لماذا زرّ أصلًا

من يضبط مفتاحَ الرفع يريد أن يعرف **الآن** أصحَّ أم لا. والخيطُ
الخلفيّ يعمل كلّ ١٢٠ ثانية ولا يقول شيئًا في الشاشة، فكان الضبطُ
تخمينًا ثم انتظارًا ثم تحديثَ صفحةٍ لعلّ «آخر رفعٍ ناجح» تغيّر.
وأشيعُ الأخطاء — مفتاحٌ ناقصُ حرف، أو عنوانٌ فيه مسافة — لا
يُكتشف إلا بمحاولةٍ حقيقيّة.

## وما تحرسه هذه الاختبارات

**(١) `force` يتخطّى «مُطفأ» — والخيطُ الخلفيّ لا يتخطّاه.** الترتيبُ
الطبيعيّ عند الضبط: أضبط، أجرّب، ثم أُفعّل. وزرٌّ يشترط التفعيلَ
أوّلًا يجعل أوّلَ تجربةٍ على بياناتٍ تُرفع فعلًا. لكنّ الخيطَ يجب
أن يبقى محكومًا بالمفتاح في الشاشة، وإلّا صار «مُطفأ» بلا معنى.

**(٢) والنموذجان خارج نموذج الإعدادات.** الصفحةُ كلُّها `<form>`
واحد، و`<form>` داخل `<form>` يُسقطه المتصفّحُ صامتًا — فيصير
الزرُّ يحفظ الإعدادات بدل أن يرفع. وخاصيّةُ `form=` تربط الزرَّ
بنموذجٍ خارجه.

يُشغَّل:  python -m pytest tests/test_sync_run_button.py -v
"""
import os
import re
import sqlite3
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


@pytest.fixture
def live(tmp_path, monkeypatch):
    import importlib

    monkeypatch.setenv('HR_DATA_DIR', str(tmp_path))
    import utils.db as db
    importlib.reload(db)
    db.init_db()

    import utils.cloud_sync as cs
    import utils.message_outbox as mo
    importlib.reload(cs)
    importlib.reload(mo)

    conn = sqlite3.connect(os.path.join(str(tmp_path), 'hr_system.db'))
    mo.init_schema(conn)
    yield conn, cs, mo, db
    conn.close()


class FakeResponse:
    def __init__(self, body):
        self.status_code = 200
        self._body = body

    def json(self):
        return self._body


class FakeServer:
    def __init__(self, body=None):
        self.calls = []
        self.body = body or {'status': 'success', 'stats': {}}

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append({'url': url, 'json': json, 'headers': headers})
        return FakeResponse(self.body)


def _configure(db):
    db.set_setting('cloud_sync_url', 'https://onz.one/api/sync')
    db.set_setting('cloud_sync_client_id', 'uid-1')
    db.set_setting('cloud_sync_api_key', 'sk_live_test')


# ------------------------------------------------ (١) force

def test_force_uploads_while_disabled(live):
    """القلب: يُجرَّب قبل أن يُفعَّل."""
    conn, cs, _mo, db = live
    _configure(db)
    db.set_setting('cloud_sync_enabled', '0')
    conn.execute(
        'INSERT INTO employees (name, employee_number, department, position,'
        ' hire_date, salary, is_active) VALUES (?,?,?,?,?,?,1)',
        ('موظف', 'F1', 'الإدارة', 'موظف', '2026-01-01', 500))
    conn.commit()

    srv = FakeServer()
    res = cs.run_once(conn=conn, session=srv, force=True)
    assert res['ok'] and res['sent'] > 0, res
    assert len(srv.calls) == 1


def test_background_thread_still_respects_disabled(live):
    """والمفتاحُ في الشاشة يبقى حاكمًا — وإلّا صار «مُطفأ» بلا معنى."""
    conn, cs, _mo, db = live
    _configure(db)
    db.set_setting('cloud_sync_enabled', '0')
    srv = FakeServer()
    res = cs.run_once(conn=conn, session=srv)          # بلا force
    assert res.get('skipped') == 'disabled', res
    assert srv.calls == []


def test_gateway_force_matches(live):
    conn, _cs, mo, db = live
    _configure(db)
    db.set_setting('message_gateway_enabled', '0')
    mo.emit(conn, 'presence_due', 'btn-1', to={'employee_id': 1})

    class GW(FakeServer):
        def post(self, url, json=None, headers=None, timeout=None):
            self.calls.append({'url': url, 'json': json})
            return FakeResponse({'status': 'accepted', 'results': [
                {'event': e['event'], 'idempotency_key': e['idempotency_key'],
                 'queued': True} for e in json['events']]})

    gw = GW()
    assert mo.run_once(conn=conn, session=gw, force=True)['sent'] == 1
    assert mo.run_once(conn=conn, session=gw)['skipped'] == 'disabled'


def test_unconfigured_is_reported_not_crashed(live):
    """زرٌّ يُسقط الصفحة أسوأ من زرٍّ يقول «أكمل الإعداد»."""
    conn, cs, mo, db = live
    db.set_setting('cloud_sync_enabled', '0')
    assert cs.run_once(conn=conn, force=True)['skipped'] == 'unconfigured'
    assert mo.run_once(conn=conn, force=True)['skipped'] == 'unconfigured'


# ------------------------------------------------ (٢) النموذجان

def _template():
    return open(os.path.join(ROOT, 'templates', 'settings.html'),
                encoding='utf-8').read()


def test_run_forms_live_outside_the_settings_form():
    """`<form>` داخل `<form>` يُسقطه المتصفّحُ صامتًا.

    فيصير الزرُّ يحفظ الإعدادات بدل أن يرفع — بلا خطأ، وبلا ما
    يدلّ على السبب.
    """
    html = _template()
    main_open = html.index("url_for('main.update_settings')")
    # **أوّل** إغلاقٍ بعد فتح النموذج الكبير هو إغلاقُه هو — ما دام
    # لا نموذجَ متداخلًا. ولو تداخل أحدُهما لصار هذا الإغلاقُ إغلاقَه،
    # فيقع تصريحُه قبله ويسقط التأكيد. (وآخرُ `</form>` في الملفّ لا
    # يصلح مرجعًا: هو إغلاقُ نموذج التشغيل نفسِه.)
    main_close = html.index('</form>', main_open)
    for form_id in ('cloudSyncRunForm', 'msgGwRunForm'):
        decl = html.index(f'id="{form_id}"')
        assert decl > main_close, \
            f'{form_id} داخل نموذج الإعدادات — سيُسقطه المتصفّح'


def test_buttons_bind_by_form_attribute():
    """الزرُّ داخل البطاقة، والنموذجُ خارجها — الرابطُ خاصيّةُ form."""
    html = _template()
    for form_id in ('cloudSyncRunForm', 'msgGwRunForm'):
        assert re.search(r'<button[^>]*type="submit"[^>]*form="' + form_id + '"',
                         html, re.S), form_id


def test_run_routes_exist():
    import routes.main_routes as mr
    for name in ('run_cloud_sync_now', 'run_message_gateway_now'):
        assert hasattr(mr, name), name


def test_run_routes_require_admin_permission():
    """مسارٌ يرفع بيانات الشركة كلَّها لا يُفتح لكل من سجّل دخوله."""
    src = open(os.path.join(ROOT, 'routes', 'main_routes.py'),
               encoding='utf-8').read()
    for name in ('run_cloud_sync_now', 'run_message_gateway_now'):
        block = src[src.index('def ' + name) - 200:src.index('def ' + name)]
        assert "require_permission('admin.settings')" in block, name
        assert '@login_required' in block, name


# ------------------------------------------------ الرفع التلقائيّ: ظاهرٌ حالُه

def test_auto_status_reports_off_waiting_running_and_stale():
    from datetime import datetime
    from utils.cloud_sync import auto_status
    now = datetime(2026, 9, 30, 12, 0, 0)
    assert auto_status(False, '2026-09-30 11:59:00', now) == 'off'
    assert auto_status(True, '', now) == 'waiting'
    assert auto_status(True, '2026-09-30 11:58:00', now) == 'running'
    assert auto_status(True, '2026-09-30 11:30:00', now) == 'stale'


def test_the_background_thread_records_its_attempts_but_not_while_disabled(live):
    conn, cs, mo, db = live
    cs.mark_auto_attempt({'ok': True, 'skipped': 'disabled'})
    assert not db.get_setting(cs.SETTING_LAST_AUTO, '')
    cs.mark_auto_attempt({'ok': True, 'idle': True})
    assert db.get_setting(cs.SETTING_LAST_AUTO, '')


def test_enabling_wakes_the_thread_instead_of_a_ten_minute_wait(live):
    conn, cs, mo, db = live
    cs.WAKE.clear()
    import time as _t
    t0 = _t.time()
    cs.wake()
    assert cs.sleep_or_wake(30) is True and _t.time() - t0 < 1
    assert not cs.WAKE.is_set(), 'يُمسح بعد الإيقاظ فلا تصير كلُّ نومةٍ صفرًا'


@pytest.fixture
def web(live, monkeypatch):
    import importlib
    conn, cs, mo, db = live
    import utils.auth as auth
    importlib.reload(auth)
    auth.get_current_license_info = lambda: {'ok': True, 'days_left': 300}
    import app as A
    importlib.reload(A)
    A.app.before_request_funcs[None] = [f for f in A.app.before_request_funcs.get(None, [])
                                        if f.__name__ not in ('check_license_globally', 'enforce_plan_features')]
    A.app.config['TESTING'] = True
    admin = conn.execute("SELECT id FROM users WHERE role='admin'").fetchone()[0]
    c = A.app.test_client()
    with c.session_transaction() as s:
        s.update({'user_id': admin, 'role': 'admin', 'username': 'admin'})
    fake = FakeServer()
    import requests
    monkeypatch.setattr(requests, 'post', fake.post)
    return c, conn, cs, db, fake


def _flashes(c):
    with c.session_transaction() as s:
        return [m for _cat, m in s.pop('_flashes', [])]


def test_the_button_while_disabled_says_automatic_upload_is_off(web):
    """كانت الشكوى: «مش بيرفع أوتوماتيك — ارفع الآن بس». الزرُّ يعمل والرفعُ
    مُطفأ عن قصد؛ فيُقال ذلك صراحةً في الشاشة وعلى الزرّ."""
    c, conn, cs, db, fake = web
    _configure(db)
    db.set_setting('cloud_sync_enabled', '0')
    conn.execute("INSERT INTO employees (name, employee_number, department, position, hire_date, salary,"
                 " is_active) VALUES ('x', '77', 'D', 'P', '2026-01-01', 100, 1)")
    conn.commit()
    r = c.post('/settings/cloud-sync/run')
    assert r.status_code == 302 and fake.calls
    msgs = _flashes(c)
    assert any('الرفع التلقائي مُطفأ' in m for m in msgs), msgs
    body = c.get('/settings').get_data(as_text=True)
    assert 'id="cloudAutoStatus" data-state="off"' in body


def test_enabling_from_settings_wakes_the_thread_and_shows_waiting(web):
    c, conn, cs, db, fake = web
    _configure(db)
    cs.WAKE.clear()
    c.post('/settings/update', data={'cloud_sync_form_present': '1', 'cloud_sync_enabled': '1',
                              'cloud_sync_url': 'https://onz.one/api/sync', 'cloud_sync_client_id': 'uid-1'})
    assert db.get_setting('cloud_sync_enabled') == '1', _flashes(c)
    assert cs.WAKE.is_set(), 'التفعيل يُوقظ الخيط فيبدأ الآن'
    assert 'data-state="waiting"' in c.get('/settings').get_data(as_text=True)
    cs.mark_auto_attempt({'ok': True, 'idle': True})
    assert 'data-state="running"' in c.get('/settings').get_data(as_text=True)


def test_upload_now_uses_what_is_typed_in_the_card_without_pressing_save(web):
    """الشكوى: كتب الوجهةَ والمعرّفَ والمفتاح وضغط «ارفع الآن» فقيل له «أكمل
    وجهة الرفع…» — الزرّ كان يرفع بالمحفوظ وحده. الآن يحفظ البطاقةَ ثم يرفع."""
    c, conn, cs, db, fake = web
    assert not db.get_setting('cloud_sync_url') and not db.get_setting('cloud_sync_api_key')
    r = c.post('/settings/cloud-sync/run', data={
        'cloud_sync_form_present': '1', 'cloud_sync_enabled': '1',
        'cloud_sync_url': ' https://onz.one/api/sync ', 'cloud_sync_client_id': 'bd58efae-uid',
        'cloud_sync_api_key': 'sk_live_typed_not_saved'})
    assert r.status_code == 302
    msgs = _flashes(c)
    assert not any('ناقص' in m or 'أكمل' in m for m in msgs), msgs
    assert fake.calls, 'لم يُرفع شيء'
    assert fake.calls[0]['url'] == 'https://onz.one/api/sync'
    assert 'sk_live_typed_not_saved' in str(fake.calls[0]['headers'])
    assert db.get_setting('cloud_sync_api_key') == 'sk_live_typed_not_saved'
    assert db.get_setting('cloud_sync_enabled') == '1'


def test_a_missing_field_is_named(web):
    c, conn, cs, db, fake = web
    c.post('/settings/cloud-sync/run', data={
        'cloud_sync_form_present': '1', 'cloud_sync_url': 'https://onz.one/api/sync',
        'cloud_sync_client_id': 'uid-1'})
    msgs = _flashes(c)
    assert any('ناقص: مفتاح الرفع' in m for m in msgs), msgs
    assert not fake.calls


def test_the_page_sends_the_card_with_the_upload_button(web):
    c, conn, cs, db, fake = web
    body = c.get('/settings').get_data(as_text=True)
    assert "getElementById('cloudSyncRunForm')" in body
    for n in ('cloud_sync_url', 'cloud_sync_client_id', 'cloud_sync_api_key', 'cloud_sync_enabled'):
        assert f"'{n}'" in body


def test_saving_or_uploading_with_an_empty_key_field_keeps_the_saved_key(web):
    """الحقلُ لا يُعاد إلى الصفحة فيصل فارغًا — والفارغُ لا يمحو المفتاح، من «حفظ» ولا من «ارفع الآن»."""
    c, conn, cs, db, fake = web
    _configure(db)
    card = {'cloud_sync_form_present': '1', 'cloud_sync_url': 'https://onz.one/api/sync',
            'cloud_sync_client_id': 'uuid-1', 'cloud_sync_api_key': ''}
    c.post('/settings/update', data=card)
    assert db.get_setting('cloud_sync_api_key') == 'sk_live_test'
    c.post('/settings/cloud-sync/run', data=card)
    assert db.get_setting('cloud_sync_api_key') == 'sk_live_test'
