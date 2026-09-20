"""بوّابة الرسائل — الطرفُ المحلّي.

## ما تحرسه هذه الاختبارات

**(١) لا نصَّ ولا رقم يغادران المقرّ.** الرسائل تخرج من رقمنا،
فالصياغةُ قالبٌ في اللوحة. ولو حمل الحدثُ هاتفًا لصار كلُّ تركيبٍ —
وكلُّ مفتاحٍ يُسرَق منه — قادرًا على الإرسال من رقمنا إلى أيّ رقم.

**(٢) حدثٌ واحد لكلّ واقعة، لا لكلّ حساب.** `notify_employee` تقيّد
لكلّ حسابات الموظّف؛ ومن له ثلاثةُ حسابات كان سيأخذ ثلاثَ رسائل
واتساب عن اعتمادٍ واحد.

**(٣) والتفرّدُ في العمود لا في الكود.** مسحُ التواجد يُنادى من كلّ
طلبٍ يفتحه أيُّ مستخدم، فـ«بصمتك مستحقّة» قد تُطلَق عشراتِ المرّات
في الدقيقة.

**(٤) وما انقضى عمرُه لا يُرسَل.** تركيبٌ كان مفصولًا أسبوعًا يُفرِغ
صندوقَه حين يعود، فلولا الإسقاطُ لانهالت عليه رسائلُ الأسبوع الماضي
دفعةً واحدة — ومنها «بصمتك مستحقّة الآن» عن يوم الثلاثاء.

**(٥) والإخفاق لا يُبطل ما نجح.** القيد يقع بعد حفظ الطلب.

يُشغَّل:  python -m pytest tests/test_message_outbox.py -v
"""
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta

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

    import utils.message_outbox as mo
    import utils.notifications as notif
    importlib.reload(mo)
    importlib.reload(notif)

    conn = sqlite3.connect(os.path.join(str(tmp_path), 'hr_system.db'))
    conn.row_factory = sqlite3.Row
    mo.init_schema(conn)
    yield conn, mo, notif, db
    conn.close()


class FakeResponse:
    def __init__(self, status=202, body=None):
        self.status_code = status
        self._body = body

    def json(self):
        if self._body is None:
            raise ValueError('no body')
        return self._body


class FakeGateway:
    """يلتقط الحمولة ويردّ كما تردّ اللوحة: `202` ومفتاحٌ لكلّ حدث."""

    def __init__(self, status=202, queued=True, reason=None, body=None):
        self.calls = []
        self.status = status
        self.queued = queued
        self.reason = reason
        self.body = body

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append({'url': url, 'json': json, 'headers': headers})
        if self.body is not None:
            return FakeResponse(self.status, self.body)
        results = [{'event': e['event'],
                    'idempotency_key': e['idempotency_key'],
                    'queued': self.queued,
                    'reason': self.reason}
                   for e in (json or {}).get('events', [])]
        return FakeResponse(self.status, {'status': 'accepted', 'results': results})


def _configure(db, enabled='1'):
    db.set_setting('message_gateway_enabled', enabled)
    db.set_setting('cloud_sync_url', 'https://onz.one/api/sync')
    db.set_setting('cloud_sync_client_id', 'uuid-1')
    db.set_setting('cloud_sync_api_key', 'sk_live_test')


def _employee(conn, number, name='أحمد'):
    conn.execute(
        'INSERT INTO employees (name, employee_number, department, position, '
        'hire_date, salary, is_active) VALUES (?, ?, ?, ?, ?, ?, 1)',
        (name, number, 'الإدارة', 'موظف', '2026-01-01', 500))
    conn.commit()
    return conn.execute('SELECT id FROM employees WHERE employee_number = ?',
                        (number,)).fetchone()[0]


def _user_for(conn, employee_id, username):
    conn.execute(
        'INSERT INTO users (username, password, full_name, role, employee_id,'
        ' is_active) VALUES (?, ?, ?, ?, ?, 1)',
        (username, 'x', username, 'employee', employee_id))
    conn.commit()


def _rows(conn):
    return [dict(r) for r in conn.execute(
        'SELECT * FROM message_outbox ORDER BY id')]


# ------------------------------------------------ (١) لا نصّ ولا رقم

def test_payload_carries_no_text_and_no_phone(live):
    """القلب. ما يخرج حدثٌ وحقولُه — لا جملةٌ ولا هاتف."""
    conn, mo, _n, db = live
    _configure(db)
    mo.emit(conn, 'leave_decided', 'lv1-ok',
            data={'employee_name': 'أحمد', 'status': 'approved'},
            to={'employee_id': 7})

    gw = FakeGateway()
    res = mo.run_once(conn=conn, session=gw)
    assert res['ok'] and res['sent'] == 1

    body = gw.calls[0]['json']
    flat = json.dumps(body, ensure_ascii=False)
    assert 'text' not in body['events'][0]
    assert 'message' not in body['events'][0]
    # لا مفاتيحَ عنوانٍ صريحة في أيّ موضع من الحمولة.
    for banned in ('"phone"', '"number"', '"to_number"', '"email"'):
        assert banned not in flat, f'{banned} في الحمولة'
    assert body['events'][0]['to'] == {'employee_id': 7}


def test_api_key_travels_in_header_not_body(live):
    conn, mo, _n, db = live
    _configure(db)
    mo.emit(conn, 'presence_due', 'p1', to={'employee_id': 1})
    gw = FakeGateway()
    mo.run_once(conn=conn, session=gw)
    assert gw.calls[0]['headers']['X-API-KEY'] == 'sk_live_test'
    assert 'sk_live_test' not in json.dumps(gw.calls[0]['json'])


def test_system_events_are_not_even_known_locally(live):
    """`otp_code` من تركيبٍ يعني رمزًا يبدو أصيلًا لأنه من رقمنا."""
    conn, mo, _n, _db = live
    for kind in ('otp_code', 'subscription_expiring', 'license_expiring'):
        assert kind not in mo.EVENTS
        assert mo.emit(conn, kind, f'x-{kind}') is None
    assert _rows(conn) == []


# ------------------------------------------------ (٢) حدثٌ لكلّ واقعة

def test_three_accounts_one_employee_send_one_event(live):
    """القلب. الإشعارُ لكلّ حساب، والرسالةُ للواقعة."""
    conn, mo, notif, _db = live
    eid = _employee(conn, '901')
    for i in range(3):
        _user_for(conn, eid, f'u{i}')

    notif.leave_decided(conn, eid, True, 'سنوية', '2026-03-01', '2026-03-05',
                        request_id=44)

    accounts = conn.execute(
        'SELECT COUNT(*) FROM notifications WHERE kind = ?',
        (notif.KIND_LEAVE_DECIDED,)).fetchone()[0]
    assert accounts == 3, 'الإشعار يصل كلَّ حساب'
    assert len(_rows(conn)) == 1, 'والرسالةُ واحدة'


def test_presence_sweep_emits_once_per_day(live):
    """أكثرُ حدثٍ إطلاقًا: يُنادى من كلّ طلبٍ يفتحه أيُّ مستخدم."""
    conn, mo, notif, _db = live
    from utils import presence as _p

    eid = _employee(conn, '902')
    _user_for(conn, eid, 'u1')
    for _ in range(25):
        notif.presence_reminder(conn, eid, _p.STATE_DUE, '08:00', '09:00')

    rows = _rows(conn)
    assert len(rows) == 1, f'قُيّد {len(rows)} بدل واحد'
    assert rows[0]['event'] == 'presence_due'


def test_due_and_missed_are_two_events(live):
    """الحالُ يتبدّل في اليوم نفسه: تذكيرٌ ثم إخبارٌ بالفوات."""
    conn, mo, notif, _db = live
    from utils import presence as _p

    eid = _employee(conn, '903')
    _user_for(conn, eid, 'u1')
    notif.presence_reminder(conn, eid, _p.STATE_DUE, '08:00', '09:00')
    notif.presence_reminder(conn, eid, _p.STATE_MISSED, '08:00', '09:00')
    assert {r['event'] for r in _rows(conn)} == {'presence_due', 'presence_missed'}


def test_decision_reversal_is_a_second_message(live):
    """يُعتمد ثم يُرفض. مفتاحٌ بلا القرار كان يبتلع الثانية."""
    conn, mo, notif, _db = live
    eid = _employee(conn, '904')
    _user_for(conn, eid, 'u1')
    notif.leave_decided(conn, eid, True, 'سنوية', '2026-03-01', '2026-03-05',
                        request_id=77)
    notif.leave_decided(conn, eid, False, 'سنوية', '2026-03-01', '2026-03-05',
                        request_id=77)
    rows = _rows(conn)
    assert len(rows) == 2
    assert {json.loads(r['data_json'])['status'] for r in rows} == \
        {'approved', 'rejected'}


# ------------------------------------------------ (٣) التفرّد

def test_same_key_twice_enqueues_once(live):
    conn, mo, _n, _db = live
    assert mo.emit(conn, 'presence_due', 'pr-7-2026-03-01-d') is not None
    assert mo.emit(conn, 'presence_due', 'pr-7-2026-03-01-d') is None
    assert len(_rows(conn)) == 1


def test_unique_is_enforced_by_the_column(live):
    """لا بالشرط في الكود: عمّالُ الخادم يتسابقون على الفحص."""
    conn, _mo, _n, _db = live
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            'INSERT INTO message_outbox (event, idempotency_key, occurred_at)'
            ' VALUES (?, ?, ?)', ('presence_due', 'dup', '2026-01-01 00:00:00'))
        conn.execute(
            'INSERT INTO message_outbox (event, idempotency_key, occurred_at)'
            ' VALUES (?, ?, ?)', ('presence_due', 'dup', '2026-01-01 00:00:00'))


def test_excuse_key_is_stable_across_processes(live):
    """`hash()` مملوحةٌ لكلّ عمليّة، فالمفتاحُ بها يتبدّل بعد الإقلاع."""
    conn, _mo, _n, _db = live
    import subprocess
    code = ('import hashlib;'
            "print(hashlib.md5('استئذان شخصي'.encode('utf-8')).hexdigest()[:6])")
    a = subprocess.run([sys.executable, '-c', code], capture_output=True,
                       text=True, env={**os.environ, 'PYTHONHASHSEED': '1'})
    b = subprocess.run([sys.executable, '-c', code], capture_output=True,
                       text=True, env={**os.environ, 'PYTHONHASHSEED': '2'})
    assert a.stdout.strip() == b.stdout.strip() != ''


# ------------------------------------------------ (٤) العمر

def test_stale_events_are_dropped_not_sent(live):
    """«بصمتك مستحقّة الآن» عمرُها ساعتان — فلا تُرسَل بعد ستّة أيّام."""
    conn, mo, _n, db = live
    _configure(db)
    old = (datetime.now() - timedelta(days=6)).strftime('%Y-%m-%d %H:%M:%S')
    mo.emit(conn, 'presence_due', 'old-1', to={'employee_id': 1},
            occurred_at=old)
    mo.emit(conn, 'leave_decided', 'fresh-1', to={'employee_id': 1})

    gw = FakeGateway()
    res = mo.run_once(conn=conn, session=gw)
    assert res['expired'] == 1
    sent = [e['event'] for e in gw.calls[0]['json']['events']]
    assert sent == ['leave_decided'], sent
    assert _rows(conn) == []


def test_age_is_measured_from_occurrence_not_upload(live):
    """يومان على حدثٍ عمرُه يوم: انقضى ولو وصل الآن."""
    conn, mo, _n, _db = live
    two_days = (datetime.now() - timedelta(days=2)).strftime('%Y-%m-%d %H:%M:%S')
    assert mo._expired('leave_decided', two_days, datetime.now()) is True
    assert mo._expired('leave_decided', mo._now(), datetime.now()) is False


def test_unreadable_date_does_not_silently_drop(live):
    """إسقاطُ ما لا يُفهَم يُخفي عطبًا؛ وإرسالُه يُظهره."""
    conn, mo, _n, _db = live
    assert mo._expired('presence_due', 'لا تاريخ', datetime.now()) is False


# ------------------------------------------------ الرفع والمطابقة

def test_results_are_matched_by_key_not_position(live):
    """الردُّ بالترتيب عقدٌ لم يكتبه أحد. المفتاحُ في الطلب والردّ."""
    conn, mo, _n, db = live
    _configure(db)
    mo.emit(conn, 'presence_due', 'k-a', to={'employee_id': 1})
    mo.emit(conn, 'leave_decided', 'k-b', to={'employee_id': 2})

    # مقلوبٌ عمدًا، وأحدهما مرفوض.
    gw = FakeGateway(body={'status': 'accepted', 'results': [
        {'event': 'leave_decided', 'idempotency_key': 'k-b', 'queued': False,
         'reason': 'no_recipient'},
        {'event': 'presence_due', 'idempotency_key': 'k-a', 'queued': True},
    ]})
    res = mo.run_once(conn=conn, session=gw)
    assert res['ok'] and res['sent'] == 2
    # **السببُ لا عددُه.** المطابقةُ بالموضع ترفض واحدًا من اثنين
    # أيضًا، فالعدد `1` في الحالين. ولا يفترقان إلا في أيِّ حدثٍ
    # نُسب إليه الرفض: `leave_decided` هو المرفوض في الردّ.
    assert res['refusals'] == ['leave_decided: no_recipient'], res['refusals']
    assert _rows(conn) == []


def test_refused_events_are_dropped_not_retried_forever(live):
    """اللوحة قالت «قرأتُ وقرّرتُ ألّا أُرسل» — والتركيب لا يُغيّره."""
    conn, mo, _n, db = live
    _configure(db)
    mo.emit(conn, 'presence_due', 'r-1', to={'employee_id': 1})
    gw = FakeGateway(queued=False, reason='daily_quota')
    res = mo.run_once(conn=conn, session=gw)
    assert res['ok'] and res['refused'] == 1
    assert _rows(conn) == []


def test_channel_refusal_reason_is_read_not_replaced_by_a_placeholder(live):
    """وجده الفحصُ الطرفيّ: `duplicate` كان يُقرأ «مرفوض».

    اللوحة تضع سببَ الرفض القناويّ في `refused` لا في `reason`.
    فكلُّ رفضٍ من هذا النوع — وهو أشيعُها — كان يُسجَّل بكلمةٍ لا
    تقول شيئًا، وهي السطرُ الذي يُشخَّص منه العطب.
    """
    conn, mo, _n, db = live
    _configure(db)
    mo.emit(conn, 'leave_decided', 'c-1', to={'employee_id': 1})
    gw = FakeGateway(body={'status': 'accepted', 'results': [
        {'event': 'leave_decided', 'idempotency_key': 'c-1', 'queued': False,
         'channels': [], 'refused': {'whatsapp': 'duplicate'}},
    ]})
    res = mo.run_once(conn=conn, session=gw)
    assert res['refusals'] == ['leave_decided: whatsapp=duplicate'], res['refusals']


def test_top_level_reason_still_wins(live):
    """`unknown_event` و`system_only` يأتيان في `reason` قبل القنوات."""
    conn, mo, _n, db = live
    _configure(db)
    mo.emit(conn, 'leave_decided', 'c-2', to={'employee_id': 1})
    gw = FakeGateway(body={'status': 'accepted', 'results': [
        {'event': 'leave_decided', 'idempotency_key': 'c-2', 'queued': False,
         'reason': 'system_only'},
    ]})
    res = mo.run_once(conn=conn, session=gw)
    assert res['refusals'] == ['leave_decided: system_only']


def test_network_failure_keeps_the_event(live):
    conn, mo, _n, db = live
    _configure(db)
    mo.emit(conn, 'presence_due', 'n-1', to={'employee_id': 1})

    class Dead:
        def post(self, *a, **k):
            raise OSError('الشبكة مقطوعة')

    res = mo.run_once(conn=conn, session=Dead())
    assert res['ok'] is False
    rows = _rows(conn)
    assert len(rows) == 1 and rows[0]['attempts'] == 1
    assert 'الشبكة' in (rows[0]['last_error'] or '')


def test_server_error_keeps_the_event(live):
    conn, mo, _n, db = live
    _configure(db)
    mo.emit(conn, 'presence_due', 's-1', to={'employee_id': 1})
    res = mo.run_once(conn=conn, session=FakeGateway(status=500))
    assert res['ok'] is False
    assert len(_rows(conn)) == 1


def test_unparsable_reply_keeps_the_event(live):
    """الإعادةُ آمنة: مفتاحُ التفرّد في اللوحة يردّ المكرّر."""
    conn, mo, _n, db = live
    _configure(db)
    mo.emit(conn, 'presence_due', 'u-1', to={'employee_id': 1})
    res = mo.run_once(conn=conn, session=FakeGateway(body={'ok': 'maybe'}))
    assert res['ok'] is False
    assert len(_rows(conn)) == 1


def test_partial_reply_drops_only_what_was_decided(live):
    """ردٌّ ناقص: ما حُسم يُمحى، وما لم يُذكر يُعاد."""
    conn, mo, _n, db = live
    _configure(db)
    mo.emit(conn, 'presence_due', 'p-a', to={'employee_id': 1})
    mo.emit(conn, 'leave_decided', 'p-b', to={'employee_id': 2})
    gw = FakeGateway(body={'status': 'accepted', 'results': [
        {'event': 'presence_due', 'idempotency_key': 'p-a', 'queued': True},
    ]})
    res = mo.run_once(conn=conn, session=gw)
    assert res['sent'] == 1
    rows = _rows(conn)
    assert len(rows) == 1 and rows[0]['idempotency_key'] == 'p-b'


# ------------------------------------------------ الإعداد

def test_disabled_sends_nothing(live):
    conn, mo, _n, db = live
    _configure(db, enabled='0')
    mo.emit(conn, 'presence_due', 'd-1', to={'employee_id': 1})
    gw = FakeGateway()
    res = mo.run_once(conn=conn, session=gw)
    assert res['skipped'] == 'disabled'
    assert gw.calls == []
    assert len(_rows(conn)) == 1, 'ما قُيّد يبقى حتى يُفعَّل الرفع'


def test_url_is_derived_from_origin_not_by_trimming(live):
    """`/api/sync` و`/api/v1/events` مساران مختلفان على أصلٍ واحد."""
    _conn, mo, _n, _db = live
    assert mo._derive_url('https://onz.one/api/sync') == \
        'https://onz.one/api/v1/events'
    assert mo._derive_url('https://onz.one/panel/api/sync') == \
        'https://onz.one/api/v1/events'
    assert mo._derive_url('') == ''
    assert mo._derive_url('لا عنوان') == ''


def test_explicit_url_overrides_the_derived_one(live):
    conn, mo, _n, db = live
    _configure(db)
    db.set_setting('message_gateway_url', 'https://msg.example/in')
    mo.emit(conn, 'presence_due', 'o-1', to={'employee_id': 1})
    gw = FakeGateway()
    mo.run_once(conn=conn, session=gw)
    assert gw.calls[0]['url'] == 'https://msg.example/in'


def test_unconfigured_is_reported_not_crashed(live):
    conn, mo, _n, db = live
    db.set_setting('message_gateway_enabled', '1')
    res = mo.run_once(conn=conn, session=FakeGateway())
    assert res['ok'] is False and res['skipped'] == 'unconfigured'


def test_batch_never_exceeds_the_panel_limit(live):
    """اللوحة ترفض ما زاد على خمسين بـ`batch_too_large`."""
    conn, mo, _n, db = live
    _configure(db)
    for i in range(80):
        mo.emit(conn, 'presence_due', f'b-{i}', to={'employee_id': i})
    gw = FakeGateway()
    mo.run_once(conn=conn, session=gw)
    assert len(gw.calls[0]['json']['events']) <= 50


# ------------------------------------------------ (٥) لا يُبطل ما نجح

def test_a_broken_outbox_does_not_break_the_notification(live, monkeypatch):
    """القيد يقع بعد حفظ الطلب. فسقوطُه هنا كان سيُلغي اعتمادًا وقع."""
    conn, mo, notif, _db = live
    eid = _employee(conn, '905')
    _user_for(conn, eid, 'u1')

    def boom(*a, **k):
        raise RuntimeError('القرص ممتلئ')

    monkeypatch.setattr(mo, 'emit', boom)
    n = notif.leave_decided(conn, eid, True, 'سنوية', '2026-03-01',
                            '2026-03-05', request_id=88)
    assert n == 1, 'الإشعار قُيّد رغم سقوط البوّابة'


def test_emit_swallows_a_dead_connection(live):
    conn, mo, _n, _db = live

    class Dead:
        def cursor(self):
            raise sqlite3.ProgrammingError('مغلق')

        def execute(self, *a, **k):
            raise sqlite3.ProgrammingError('مغلق')

    assert mo.emit(Dead(), 'presence_due', 'x-1') is None


def test_outbox_is_capped(live, monkeypatch):
    """تركيبٌ مقطوعٌ شهرًا لا يُنمي جدولًا بلا حدّ على قرصٍ محلّي."""
    conn, mo, _n, _db = live
    monkeypatch.setattr(mo, 'MAX_OUTBOX', 10)
    for i in range(25):
        mo.emit(conn, 'presence_due', f'c-{i}', to={'employee_id': i})
    rows = _rows(conn)
    assert len(rows) == 10
    # يُسقَط أقدمُه: الأحدث أولى بالإرسال.
    assert rows[-1]['idempotency_key'] == 'c-24'


def test_bad_key_is_refused_before_the_round_trip(live):
    conn, mo, _n, _db = live
    assert mo.emit(conn, 'presence_due', '') is None
    assert mo.emit(conn, 'presence_due', 'x' * 81) is None
    assert _rows(conn) == []
