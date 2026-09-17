"""بصمة التواجد: هل يُذكَّر من ينساها، ومرّةً واحدة؟

نسيانُها تخصم مالًا — `presence_penalty` يأخذ ربع يوم افتراضًا عن
كل مرّة. والموظف يعرف بها من قسيمة راتبه بعد أن فات الأوان، وهو
كان في مكتبه على بعد خطوات من الجهاز.

والوقت يُحقن في كل اختبار: اختبارٌ يعتمد ساعة الجهاز يمرّ صباحًا
ويسقط بعد الظهر، ولا يكون قد فحص شيئًا.

يُشغَّل:  python -m pytest tests/test_presence_reminder.py -v
"""
import os
import sqlite3
import sys
from datetime import datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TODAY = datetime.now().strftime('%Y-%m-%d')


@pytest.fixture
def desk(tmp_path):
    """موظفٌ شفتُه فيه نافذة تواجد من ١٢:٠٠ إلى ١٤:٠٠."""
    import importlib

    os.environ['HR_DATA_DIR'] = str(tmp_path)

    import utils.auth as auth
    importlib.reload(auth)
    auth.get_current_license_info = lambda: {'ok': True}

    import utils.db as db
    importlib.reload(db)
    db.init_db()

    # التطبيق يُعاد تحميله على هذه القاعدة، ويُنزَع حارس الترخيص —
    # وإلا حوّلت الطلبات إلى صفحة الدخول فعاد `get_json()` فارغًا،
    # واختبارُ المسار لا يفحص المسار.
    import app as A
    importlib.reload(A)
    A.app.before_request_funcs[None] = [
        f for f in A.app.before_request_funcs.get(None, [])
        if f.__name__ != 'check_license_globally'
    ]
    A.app.config['TESTING'] = True

    path = os.path.join(str(tmp_path), 'hr_system.db')
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    # أسماءٌ خاصة بالاختبار: النظام يبذر شفتاتٍ باسم «صباحي» وغيره.
    cur.execute("INSERT INTO shift_types (name, start_time, end_time,"
                " presence_start_time, presence_end_time)"
                " VALUES ('اختبار-بنافذة', '08:00', '16:00', '12:00', '14:00')")
    cur.execute("INSERT INTO shift_types (name, start_time, end_time)"
                " VALUES ('اختبار-بلا-نافذة', '08:00', '16:00')")

    required = [r for r in cur.execute('PRAGMA table_info(employees)')
                if r[3] == 1 and r[4] is None and r[1] != 'id']

    def mkemp(number, name, shift):
        v = {r[1]: (1 if r[2].upper() in ('INTEGER', 'REAL', 'NUMERIC') else 'x')
             for r in required}
        v.update({'employee_number': number, 'name': name, 'is_active': 1,
                  'shift_type': shift})
        cur.execute(f"INSERT INTO employees ({', '.join(v)}) "
                    f"VALUES ({', '.join('?' * len(v))})", list(v.values()))
        return cur.lastrowid

    emp = mkemp('P1', 'موظف مكتب', 'اختبار-بنافذة')
    free = mkemp('P2', 'موظف بلا نافذة', 'اختبار-بلا-نافذة')

    from werkzeug.security import generate_password_hash
    cur.execute('INSERT INTO users (username, password, full_name, role,'
                ' is_active, employee_id) VALUES (?,?,?,?,1,?)',
                ('p1', generate_password_hash('x'), 'موظف', 'user', emp))
    user = cur.lastrowid
    con.commit()

    def punch_at(hhmm, emp_id=emp):
        con.execute('INSERT INTO attendance_records (employee_id, device_id,'
                    ' check_time, check_type, verify_code, source)'
                    " VALUES (?, 0, ?, 1, 15, 'test')",
                    (emp_id, f'{TODAY} {hhmm}:00'))
        con.commit()

    def client_for(user_id, emp_id):
        c = A.app.test_client()
        with c.session_transaction() as sess:
            sess.update({'user_id': user_id, 'role': 'user',
                         'employee_id': emp_id})
        return c

    return {'con': con, 'emp': emp, 'free': free, 'user': user,
            'punch_at': punch_at, 'client_for': client_for}


def _at(hhmm):
    h, m = hhmm.split(':')
    return datetime.strptime(f'{TODAY} {h}:{m}:00', '%Y-%m-%d %H:%M:%S')


# --------------------------------------------------------- القاعدة

def test_no_window_means_nothing_is_expected(desk):
    """من لا نافذة في شفته لا يُذكَّر — ولا يُخصم عليه أصلًا."""
    from utils import presence

    st = presence.status(desk['con'], desk['free'], now=_at('13:00'))
    assert st['state'] == presence.STATE_NOT_REQUIRED


def test_before_the_window_opens_nothing_is_due(desk):
    from utils import presence
    desk['punch_at']('08:05')

    st = presence.status(desk['con'], desk['emp'], now=_at('10:00'))
    assert st['state'] == presence.STATE_BEFORE
    assert st['start'] == '12:00' and st['end'] == '14:00'


def test_inside_the_window_without_a_punch_is_due(desk):
    from utils import presence
    desk['punch_at']('08:05')

    st = presence.status(desk['con'], desk['emp'], now=_at('12:30'))
    assert st['state'] == presence.STATE_DUE


def test_a_punch_inside_the_window_settles_it(desk):
    from utils import presence
    desk['punch_at']('08:05')
    desk['punch_at']('12:40')

    st = presence.status(desk['con'], desk['emp'], now=_at('13:00'))
    assert st['state'] == presence.STATE_DONE
    assert st['punched_at'] == '12:40'


def test_a_punch_outside_the_window_does_not_count(desk):
    """بصمة الحضور صباحًا ليست بصمة تواجد — وهذا بيت القصيد."""
    from utils import presence
    desk['punch_at']('08:05')
    desk['punch_at']('15:00')

    st = presence.status(desk['con'], desk['emp'], now=_at('15:30'))
    assert st['state'] == presence.STATE_MISSED


def test_being_absent_all_day_is_absence_not_a_missed_presence(desk):
    """من لم يحضر يُحاسَب غيابًا. وعدُّه «تواجدًا ناقصًا» أيضًا
    يخصم عليه مرّتين عن يومٍ واحد — وهذا ما يتجنّبه التقرير."""
    from utils import presence

    st = presence.status(desk['con'], desk['emp'], now=_at('15:30'))
    assert st['state'] == presence.STATE_ABSENT


# -------------------------------------------------------- التذكير

def _inbox(desk):
    from utils import notifications as notif
    return notif.listing(desk['con'], desk['user'])


def test_the_employee_is_reminded_while_the_window_is_open(desk):
    from utils import notifications as notif
    desk['punch_at']('08:05')

    st, sent = notif.check_presence_for(desk['con'], desk['emp'], now=_at('12:30'))
    assert st['state'] == 'due'
    assert sent == 1

    item = _inbox(desk)[0]
    assert 'مطلوبة الآن' in item['title']
    assert '12:00' in item['body'] and '14:00' in item['body']
    assert item['target'] == 'punch'


def test_the_reminder_is_sent_once_however_often_the_app_is_opened(desk):
    """التذكير يُنادى من كل طلبٍ يفتحه الموظف.

    فبلا فحصٍ للتكرار يمتلئ الصندوق بعشرين نسخةً من رسالةٍ واحدة،
    ويصير الصندوق نفسه ضجيجًا يُتجاهَل — وهو أسوأ من لا تذكير.
    """
    from utils import notifications as notif
    desk['punch_at']('08:05')

    for minute in ('12:05', '12:15', '12:30', '13:00', '13:45'):
        notif.check_presence_for(desk['con'], desk['emp'], now=_at(minute))

    due = [n for n in _inbox(desk) if n['kind'] == 'presence_due']
    assert len(due) == 1, f'قُيّد {len(due)} تذكيرًا بدل واحد'


def test_after_the_window_the_message_says_what_to_do(desk):
    """ليس لومًا: يُقال له اطلب «تصحيح بصمة» قبل أن يُخصم."""
    from utils import notifications as notif
    desk['punch_at']('08:05')

    _, sent = notif.check_presence_for(desk['con'], desk['emp'], now=_at('15:00'))
    assert sent == 1

    item = [n for n in _inbox(desk) if n['kind'] == 'presence_missed'][0]
    assert 'فاتتك' in item['title']
    assert 'تصحيح بصمة' in item['body']


def test_someone_who_punched_is_never_reminded(desk):
    from utils import notifications as notif
    desk['punch_at']('08:05')
    desk['punch_at']('12:40')

    for minute in ('12:45', '13:30', '15:00'):
        _, sent = notif.check_presence_for(desk['con'], desk['emp'], now=_at(minute))
        assert sent == 0

    assert _inbox(desk) == []


def test_an_employee_without_a_window_is_never_reminded(desk):
    from utils import notifications as notif

    for minute in ('12:30', '15:00'):
        _, sent = notif.check_presence_for(desk['con'], desk['free'], now=_at(minute))
        assert sent == 0


def test_both_states_can_occur_in_one_day_but_each_once(desk):
    """ذُكِّر داخل النافذة ولم يبصم: يُخبَر بعدها أنها فاتت. رسالتان
    مختلفتان لا تكرارٌ لواحدة."""
    from utils import notifications as notif
    desk['punch_at']('08:05')

    notif.check_presence_for(desk['con'], desk['emp'], now=_at('12:30'))
    notif.check_presence_for(desk['con'], desk['emp'], now=_at('13:00'))
    notif.check_presence_for(desk['con'], desk['emp'], now=_at('15:00'))
    notif.check_presence_for(desk['con'], desk['emp'], now=_at('16:00'))

    kinds = [n['kind'] for n in _inbox(desk)]
    assert kinds.count('presence_due') == 1
    assert kinds.count('presence_missed') == 1


# ------------------------------------------- عبر المسار كما يراه المستخدم

def test_the_punch_endpoint_carries_the_window(desk):
    """الشاشة تعرض النافذة، فالمسار يجب أن يحملها فعلًا.

    اختبارات الوحدة تفحص القاعدة، ولا تُثبت أنها وصلت إلى الردّ.
    """
    c = desk['client_for'](desk['user'], desk['emp'])
    desk['punch_at']('08:05')
    j = c.get('/portal/api/punch-status').get_json()

    assert j['success'] is True, j
    assert 'presence_window' in j, 'النافذة لا تصل إلى الشاشة'
    assert j['presence_window']['start'] == '12:00'
    assert j['presence_window']['end'] == '14:00'


def test_bootstrap_carries_the_window_too(desk):
    """التطبيق يقرؤها من التهيئة عند الفتح."""
    c = desk['client_for'](desk['user'], desk['emp'])
    j = c.get('/portal/api/bootstrap').get_json()
    assert j['presence'] is not None
    assert j['presence']['start'] == '12:00'


def test_an_employee_without_a_window_gets_a_harmless_answer(desk):
    """من لا نافذة له: `not_required` لا خطأ ولا حقلٌ غائب."""
    con = desk['con']
    from werkzeug.security import generate_password_hash
    con.execute('INSERT INTO users (username, password, full_name, role,'
                " is_active, employee_id) VALUES (?,?,?,'user',1,?)",
                ('p2', generate_password_hash('x'), 'بلا نافذة', desk['free']))
    con.commit()
    uid = con.execute("SELECT id FROM users WHERE username='p2'").fetchone()[0]

    c = desk['client_for'](uid, desk['free'])
    j = c.get('/portal/api/punch-status').get_json()
    assert j['presence_window']['state'] == 'not_required'


# ------------------------------------------------- المسح الشامل

def test_the_sweep_reminds_someone_who_never_opened_anything(desk):
    """بيت القصيد.

    الفحص الفرديّ لا يقع إلا حين يفتح الموظف شيئًا — ومن لم يفتح
    شيئًا هو بالضبط من يحتاج التذكير. فالمسح يمرّ على الجميع.
    """
    from utils import notifications as notif
    desk['punch_at']('08:05')

    sent = notif.sweep_presence(desk['con'], now=_at('12:30'), force=True)

    assert sent == 1
    assert 'مطلوبة الآن' in _inbox(desk)[0]['title']


def test_the_sweep_skips_those_without_a_window(desk):
    """الاستعلام يضمّ فقط من لشفته نافذة — لا يُفحص الباقون أصلًا."""
    from utils import notifications as notif
    desk['punch_at']('08:05')
    desk['punch_at']('08:06', emp_id=desk['free'])

    sent = notif.sweep_presence(desk['con'], now=_at('12:30'), force=True)
    assert sent == 1, 'ذُكِّر من لا نافذة له'


def test_the_sweep_is_throttled(desk):
    """يُنادى من كل طلبٍ يفتحه أيّ مستخدم، فبلا خانقٍ يمرّ على كل
    الموظفين مع كل نقرة."""
    from utils import notifications as notif
    desk['punch_at']('08:05')

    first = notif.sweep_presence(desk['con'], now=_at('12:30'))
    second = notif.sweep_presence(desk['con'], now=_at('12:31'))
    third = notif.sweep_presence(desk['con'], now=_at('12:35'))

    assert first == 1
    assert second == 0 and third == 0, 'مرّ المسح رغم الخانق'


def test_the_throttle_opens_again_after_the_interval(desk):
    from utils import notifications as notif
    from datetime import timedelta

    desk['punch_at']('08:05')
    notif.sweep_presence(desk['con'], now=_at('12:30'))

    later = _at('12:30') + timedelta(minutes=notif.SWEEP_EVERY_MINUTES + 1)
    # لا تذكير جديد لأن الأول قُيّد، لكن المسح نفسه يجري — ويُتحقَّق
    # منه بأن الخانق سمح.
    assert notif._sweep_due(desk['con'], later) is True


def test_the_cron_script_runs_and_reports(desk, capsys):
    """السكربت طريق cron. لو انكسر استيرادُه لم يظهر ذلك إلا ليلًا."""
    import tools.presence_sweep as sweep

    rc = sweep.main(['--force'])
    out = capsys.readouterr().out

    assert rc == 0
    assert 'تذكيرات بصمة التواجد' in out


if __name__ == '__main__':
    raise SystemExit(pytest.main([__file__, '-v']))
