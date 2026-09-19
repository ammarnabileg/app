"""الرفع السحابي: ما يفوت الطابعَ الزمني، وما لا يغادر المقرّ.

## الخلل الذي تُثبِّته هذه الاختبارات

الوكيلان السابقان كانا يسألان «ما `created_at` له أحدثُ من آخر مرّة».
وهذا سؤالٌ يفوته ثلاثةُ أشياء تقع يوميًّا:

* **تعديل** راتبٍ لا يغيّر `created_at`، فالسحابة تبقى على القديم.
* **حذف** صفٍّ لا يُنتج صفًّا، فلا شيء يقول للسحابة أن تمحوه.
* **بصمةٌ متأخّرة** من جهازٍ كان مفصولًا `check_time` لها أقدمُ من
  العلامة المائيّة، فتُتخطّى ولا تصل أبدًا.

وكلُّها تفشل صامتةً: العميل يرى بياناتٍ تبدو سليمة، وهي قديمة.

## وما لا يخرج

`employees.password` كلمةُ الموظّف على جهاز البصمة لا حقلُ عرض،
و`users` فيه كلمات مرور النظام، و`license_settings` فيه مفتاح
الترخيص. خروجُ أيّها إلى السحابة خللٌ أمنيّ لا نقصُ ميزة.

يُشغَّل:  python -m pytest tests/test_cloud_sync.py -v
"""
import os
import sqlite3
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


@pytest.fixture
def live(tmp_path, monkeypatch):
    """نظامٌ حيّ بدفترِ تغييراتٍ مثبَّت."""
    import importlib

    monkeypatch.setenv('HR_DATA_DIR', str(tmp_path))
    import utils.db as db
    importlib.reload(db)
    db.init_db()

    import utils.cloud_outbox as ob
    import utils.cloud_sync as cs
    importlib.reload(ob)
    importlib.reload(cs)

    conn = sqlite3.connect(os.path.join(str(tmp_path), 'hr_system.db'))
    yield conn, ob, cs, db, str(tmp_path)
    conn.close()


def _employee(conn, number, name='أحمد', salary=500):
    conn.execute(
        'INSERT INTO employees (name, employee_number, department, position, '
        'hire_date, salary, is_active) VALUES (?, ?, ?, ?, ?, ?, 1)',
        (name, number, 'الإدارة', 'موظف', '2026-01-01', salary))
    conn.commit()
    return conn.execute('SELECT id FROM employees WHERE employee_number = ?',
                        (number,)).fetchone()[0]


def _drain_baseline(conn, cs, ob):
    """يُنهي المشي الأوّل كي تعزل الاختباراتُ أثرَ الدفتر وحده."""
    for _ in range(200):
        batch = cs.build_batch(conn)
        if not batch['baseline_marks']:
            break
        cs.commit_batch(conn, batch)
    ob.ack(conn, ob.high_water(conn, limit=100000))


class FakeResponse:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body if body is not None else {'status': 'success', 'stats': {}}

    def json(self):
        return self._body


class FakeServer:
    """خادمٌ يلتقط الحمولة — الغرض فحصُ ما يُرسَل لا محاكاةُ الطرف الآخر."""

    def __init__(self, status=200, body=None):
        self.calls = []
        self.status = status
        self.body = body

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append({'url': url, 'json': json, 'headers': headers})
        return FakeResponse(self.status, self.body)


def _configure(db, url='https://client.onz.one/api/sync'):
    db.set_setting('cloud_sync_enabled', '1')
    db.set_setting('cloud_sync_url', url)
    db.set_setting('cloud_sync_client_id', 'uuid-1')
    db.set_setting('cloud_sync_api_key', 'sk_live_test')


# ------------------------------------------------ ما يفوت الطابع الزمني

def test_editing_a_salary_reaches_the_cloud(live):
    """القلب. `created_at` لا يتغيّر بالتعديل — فالسؤالُ عنه يُخفيه."""
    conn, ob, cs, db, _ = live
    eid = _employee(conn, '901', salary=500)
    _drain_baseline(conn, cs, ob)

    conn.execute('UPDATE employees SET salary = 900 WHERE id = ?', (eid,))
    conn.commit()

    batch = cs.build_batch(conn)
    rows = batch['data'].get('employees', [])
    assert rows, 'التعديل لم يُنتج شيئًا يُرسَل — السحابة تبقى على الراتب القديم'
    assert rows[0]['salary'] == 900


def test_deleting_a_row_sends_a_delete(live):
    """الحذف لا يُنتج صفًّا، فالترشيحُ بالطابع الزمني لا يراه أصلًا."""
    conn, ob, cs, db, _ = live
    eid = _employee(conn, '902')
    _drain_baseline(conn, cs, ob)

    conn.execute('DELETE FROM employees WHERE id = ?', (eid,))
    conn.commit()

    batch = cs.build_batch(conn)
    assert eid in batch['deletes'].get('employees', []), (
        'الموظّف المحذوف يبقى ظاهرًا للعميل في السحابة بعد محوه')


def test_a_backdated_punch_is_not_skipped(live):
    """جهازٌ كان مفصولًا يُفرِغ بصماتِ أمس بعد بصماتِ اليوم.

    `check_time` لها أقدمُ من العلامة المائيّة، فالوكيل القديم
    يتخطّاها صامتًا. والدفتر يرتّب بواقعة الإدراج لا بتاريخ العمل.
    """
    conn, ob, cs, db, _ = live
    eid = _employee(conn, '903')
    _drain_baseline(conn, cs, ob)

    conn.execute('INSERT INTO attendance_records (employee_id, device_id, '
                 'check_time, check_type) VALUES (?, 1, ?, ?)',
                 (eid, '2026-09-19 08:00:00', 'IN'))
    conn.commit()
    _drain_baseline(conn, cs, ob)
    batch = cs.build_batch(conn)
    cs.commit_batch(conn, batch)

    # البصمة المتأخّرة: تاريخُها أقدم، وإدراجُها أحدث.
    conn.execute('INSERT INTO attendance_records (employee_id, device_id, '
                 'check_time, check_type) VALUES (?, 1, ?, ?)',
                 (eid, '2026-09-18 08:00:00', 'IN'))
    conn.commit()

    batch = cs.build_batch(conn)
    times = [r['check_time'] for r in batch['data'].get('attendance_records', [])]
    assert '2026-09-18 08:00:00' in times, (
        'البصمة المتأخّرة لم تُرسَل — وهي لن تُرسَل أبدًا بعد الآن')


# ------------------------------------------------ ما لا يغادر المقرّ

def test_the_device_password_never_leaves(live):
    """`employees.password` كلمةُ الموظّف على جهاز البصمة."""
    conn, ob, cs, db, _ = live
    eid = _employee(conn, '904')
    conn.execute("UPDATE employees SET password = '1234', privilege = 14 "
                 "WHERE id = ?", (eid,))
    conn.commit()

    batch = cs.build_batch(conn)
    for row in batch['data'].get('employees', []):
        assert 'password' not in row, 'كلمةُ مرور الجهاز تُرفَع إلى السحابة'
        assert 'privilege' not in row


@pytest.mark.parametrize('table', [
    'users', 'license_settings', 'password_reset_tokens',
    'fingerprint_templates', 'fingerprint_faces',
])
def test_sensitive_tables_are_not_synced(live, table):
    """جداولُ كلماتِ المرور والترخيص والقوالبِ الحيويّة خارج المزامنة."""
    conn, ob, cs, db, _ = live
    assert table not in ob.SYNC_TABLES, f'{table} يُرفَع إلى السحابة'


def test_no_trigger_was_installed_on_a_sensitive_table(live):
    """قفلٌ على الطرف الآخر: لا مُشغِّل يكتب دفترًا لجدولٍ محظور."""
    conn, ob, cs, db, _ = live
    names = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger' "
        "AND name LIKE 'cloud_out_%'")]
    for banned in ('users', 'license_settings', 'fingerprint_templates'):
        assert not any(n.startswith(f'cloud_out_{banned}_') for n in names)


# ------------------------------------------------ الرفع نفسه

def test_a_failed_upload_keeps_the_batch_for_retry(live):
    """الخادم يردّ 500 — فالدفتر يبقى، والدفعة تُعاد.

    محوُ الدفتر قبل التأكيد يعني ضياعَ الدفعة نهائيًّا عند أوّل
    انقطاع، بلا أثرٍ يدلّ على ما ضاع.
    """
    conn, ob, cs, db, _ = live
    _employee(conn, '905')
    _configure(db)

    before = ob.pending_count(conn)
    server = FakeServer(status=500)
    res = cs.run_once(conn=conn, session=server)

    assert res['ok'] is False
    assert ob.pending_count(conn) == before, 'الدفعة مُحيت رغم فشل الرفع'


def test_a_successful_upload_clears_the_journal(live):
    conn, ob, cs, db, _ = live
    _employee(conn, '906')
    _configure(db)

    server = FakeServer()
    res = cs.run_once(conn=conn, session=server)

    assert res['ok'] is True and res['sent'] > 0
    assert ob.pending_count(conn) == 0


def test_the_api_key_travels_in_the_header_not_the_body(live):
    conn, ob, cs, db, _ = live
    _employee(conn, '907')
    _configure(db)

    server = FakeServer()
    cs.run_once(conn=conn, session=server)

    call = server.calls[0]
    assert call['headers']['X-API-KEY'] == 'sk_live_test'
    assert 'sk_live_test' not in str(call['json']), 'المفتاح مكرَّر في الجسم'


def test_ten_edits_to_one_row_send_one_row(live):
    """الطيّ. تعديلٌ متكرّر على صفٍّ واحد لا يعني عشرَ نسخٍ على الشبكة."""
    conn, ob, cs, db, _ = live
    eid = _employee(conn, '908')
    _drain_baseline(conn, cs, ob)

    for s in range(10):
        conn.execute('UPDATE employees SET salary = ? WHERE id = ?', (s, eid))
    conn.commit()

    batch = cs.build_batch(conn)
    rows = [r for r in batch['data'].get('employees', []) if r['id'] == eid]
    assert len(rows) == 1
    assert rows[0]['salary'] == 9, 'أُرسلت حالةٌ وسيطة لا الحالةَ الأخيرة'


def test_declared_types_are_sent_so_the_server_need_not_guess(live):
    """الخادم القديم يستنتج النوع من أوّل صفّ: راتبٌ `0` يجعله عددًا صحيحًا.

    فـ`1500.75` بعده تُبتر إلى `1500` — نقصٌ في المال صامت. ونحن
    نعرف النوع المُعلَن فنرسله.
    """
    conn, ob, cs, db, _ = live
    _employee(conn, '909', salary=0)

    batch = cs.build_batch(conn)
    types = batch['schema'].get('employees', {})
    assert types, 'لم تُرسَل الأنواع — الخادم سيخمّنها من أوّل قيمة'
    assert 'INT' not in types.get('salary', ''), (
        f"نوع الراتب المُعلَن {types.get('salary')!r} — الكسور ستُبتر")


def test_sync_disabled_sends_nothing(live):
    conn, ob, cs, db, _ = live
    _employee(conn, '910')
    db.set_setting('cloud_sync_enabled', '0')

    server = FakeServer()
    res = cs.run_once(conn=conn, session=server)

    assert res['skipped'] == 'disabled'
    assert server.calls == []


def test_unconfigured_sync_reports_instead_of_crashing(live):
    """وكيلٌ بلا مُراقب: سقوطُه يعني توقّفَ الرفع إلى أن ينتبه أحد."""
    conn, ob, cs, db, _ = live
    db.set_setting('cloud_sync_enabled', '1')
    db.set_setting('cloud_sync_url', '')

    res = cs.run_once(conn=conn, session=FakeServer())
    assert res['ok'] is False and res['skipped'] == 'unconfigured'


def test_a_network_error_is_reported_not_raised(live):
    conn, ob, cs, db, _ = live
    _employee(conn, '911')
    _configure(db)

    class Broken:
        def post(self, *a, **k):
            raise OSError('connection reset')

    res = cs.run_once(conn=conn, session=Broken())
    assert res['ok'] is False and 'connection reset' in res['error']
    assert ob.pending_count(conn) > 0, 'الدفعة ضاعت رغم أن الرفع لم يقع'


# ------------------------------------------------ الاستعادة

def test_restoring_a_backup_makes_the_walk_start_over(live, tmp_path):
    """الاستعادة تستبدل المحتوى — فما في السحابة يخصّ قاعدةً أخرى.

    والصفوف المستعادة لم تمرّ بمُشغِّل، فلا أثر لها في الدفتر.
    متابعةُ الرفع من حيث وقف تترك السحابةَ على بياناتٍ زائلة.
    """
    conn, ob, cs, db, data_dir = live
    _employee(conn, '912')
    _drain_baseline(conn, cs, ob)
    assert ob.get_state(conn, 'baseline:employees') is not None

    import importlib
    import utils.backup as bk
    importlib.reload(bk)

    src = os.path.join(str(tmp_path), 'old.db')
    other = sqlite3.connect(src)
    cols = [r[1] for r in conn.execute('PRAGMA table_info(shift_types)')]
    other.execute('CREATE TABLE shift_types (%s)' % ', '.join(f'"{c}"' for c in cols))
    other.commit()
    other.close()

    bk.restore(src, ['shift_types'])

    fresh = sqlite3.connect(os.path.join(data_dir, 'hr_system.db'))
    try:
        assert fresh.execute(
            "SELECT COUNT(*) FROM cloud_sync_state WHERE k LIKE 'baseline:%'"
        ).fetchone()[0] == 0, 'المشي الأوّل لم يُصفَّر بعد الاستعادة'
    finally:
        fresh.close()


def test_restore_puts_the_triggers_back(live, tmp_path):
    """`DROP TABLE` يُسقط مُشغِّلات الجدول معه — بلا ما يُنبّه."""
    conn, ob, cs, db, data_dir = live
    import importlib
    import utils.backup as bk
    importlib.reload(bk)

    src = os.path.join(str(tmp_path), 'old2.db')
    other = sqlite3.connect(src)
    cols = [r[1] for r in conn.execute('PRAGMA table_info(shift_types)')]
    other.execute('CREATE TABLE shift_types (%s)' % ', '.join(f'"{c}"' for c in cols))
    other.commit()
    other.close()

    bk.restore(src, ['shift_types'])

    fresh = sqlite3.connect(os.path.join(data_dir, 'hr_system.db'))
    try:
        names = [r[0] for r in fresh.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' "
            "AND name LIKE 'cloud_out_shift_types_%'")]
        assert len(names) == 3, (
            f'مُشغِّلات shift_types بعد الاستعادة: {names} — الدفتر صامت عنها')
    finally:
        fresh.close()


# ------------------------------------------------ لا وكيلَ ثالث

def test_there_is_only_one_sync_agent():
    """بروتوكولان لا يلتقيان في شجرةٍ واحدة أوقعا العطب أصلًا.

    `cloud_sync_agent.py` كان يكلّم نقطةَ نهايةٍ لا وجود لها بمفتاحٍ
    وهميّ، و`launcher/sync_agent.py` يكلّم الحقيقيّة. فمن شغّل الأوّل
    لم يرفع شيئًا ولم يعرف لماذا.
    """
    assert not os.path.exists(os.path.join(ROOT, 'launcher', 'sync_agent.py'))
    src = open(os.path.join(ROOT, 'cloud_sync_agent.py'), encoding='utf-8').read()
    # الشرح يذكر العنوان القديم عمدًا؛ المقصود ألّا يبقى ثابتًا حيًّا يقصده.
    assert 'CLOUD_API_BASE' not in src, 'ما زال يحمل وجهةً خاصّةً به'
    assert 'COMPANY_API_KEY' not in src, 'ما زال يحمل مفتاحًا مكتوبًا في الشيفرة'
    assert 'from utils.cloud_sync import' in src


if __name__ == '__main__':
    raise SystemExit(pytest.main([__file__, '-v']))
