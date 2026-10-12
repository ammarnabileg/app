# -*- coding: utf-8 -*-
"""الموظّفُ الموقوف لا يبصم — على الجهاز، ولا في التقارير.

## ما كان يحدث

إيقافُ موظّفٍ (غير نشط، أو إنهاء خدمة) كان:
- يبعث لأجهزة ADMS أمرَ حذفه — من شاشة التعديل وحدها، لا من إنهاء الخدمة؛
- ولا يبعث **شيئًا** للأجهزة المتّصلة مباشرةً (IP + بورت، مثل K40): يبقى عليها
  ويبصم كلَّ يوم؛
- وبصماتُه تُسحب وتُحفظ كأيّ موظّف (البرنامجُ لا يسأل: أنشطٌ هو؟) فتظهر في
  تقارير الحضور ويُحسب له حضور.

## ما يحدث الآن

١) **على الجهاز يُعطَّل لا يُحذف** (٢.٣٢.١). يبقى عليه ببصماته، والجهازُ يرفض بصمتَه:
   في سجلّ المستخدم على أجهزة ZKTeco بايتُ الصلاحية، وأوّلُ بتٍّ فيه «معطّل»
   (كما يضبطه برنامج ZKTeco نفسُه — Enabled في الـSDK). المباشرُ بـpyzk فورًا في
   الخلفيّة، وADMS بأمر USERINFO (Pri مع البت، وEnabled=0). وعند التفعيل يُفكّ البتّ
   فيبصم ببصماته كما كان. وتُحفظ بصماتُه عندنا احتياطًا قبل التعطيل. وجهازٌ مقفول
   وقتَها يُعالَج في المزامنة التالية (كلَّ ساعة).
٢) **وبصمتُه لا تُحفظ في الحضور** — من ADMS ولا من المباشر — بعد تاريخ إيقافه.
   تُقيَّد في `ignored_punches` (لمن يسأل: «بصم وهو موقوف؟») ولا تدخل تقريرًا.
   وما بصمه **قبل** إيقافه يُحفظ عاديًّا (جهازٌ لم يُزامَن من أسبوع لا يُسقط أسبوعَ عمل).

## مَن الموقوف

غيرُ النشط، إلا من أُضيف غيرَ نشطٍ لأنّ عددَ الموظّفين تجاوز حدَّ الاشتراك
(`source='plan_cap'`): ذاك يُترك يبصم عمدًا حتى يزيد العميلُ العدد — بصماتُه لا تضيع.

وتاريخُ الإيقاف: اليومُ التالي لتاريخ نهاية الخدمة، وإلّا وقتُ آخر تحويلٍ إلى «غير نشط»
في سجلّ الموظّف. وموقوفٌ بلا تاريخ (بيانات قديمة) تُرفض كلُّ بصماته الجديدة.
"""
import base64
import logging
import threading
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

PLAN_CAP_SOURCE = 'plan_cap'
# أوّلُ بتٍّ في صلاحية مستخدم ZKTeco: «معطّل». (0 مستخدم، 14 مسؤول — والبتُّ فوقها.)
DISABLED_BIT = 1


def _parse(ts):
    if not ts:
        return None
    s = str(ts).strip()
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d'):
        try:
            return datetime.strptime(s[:19] if 'T' in fmt or ':' in fmt else s[:10], fmt)
        except ValueError:
            continue
    return None


def ensure_schema(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS ignored_punches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        employee_id INTEGER NOT NULL,
        device_id INTEGER,
        check_time TEXT NOT NULL,
        reason TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (employee_id, check_time)
    )''')
    # موظّفٌ حُذف من جهازٍ مباشر لأنه أُوقف — ليُعاد إليه وحدَه عند التفعيل
    # (لا يُرفع موظّفٌ لجهاز فرعٍ لم يكن عليه أصلًا).
    conn.execute('''CREATE TABLE IF NOT EXISTS device_removed_users (
        device_id INTEGER NOT NULL,
        user_id TEXT NOT NULL,
        removed_at TEXT DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (device_id, user_id)
    )''')
    # حذفٌ من جهازٍ مباشر كان مقفولًا وقتَها — يُعاد في المزامنة التالية حتى يتمّ.
    conn.execute('''CREATE TABLE IF NOT EXISTS device_pending_deletes (
        device_id INTEGER NOT NULL,
        user_id TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        last_error TEXT,
        PRIMARY KEY (device_id, user_id)
    )''')
    # موظّفٌ محصورٌ في أجهزةٍ بعينها (مكانُ عمله): موجودٌ على كلّ الأجهزة ببصماته،
    # ومعطّلٌ على غيرها. مَن ليس له صفٌّ هنا يبصم على كلّ الأجهزة (كما كان).
    conn.execute('''CREATE TABLE IF NOT EXISTS employee_device_rules (
        employee_number TEXT PRIMARY KEY,
        since TEXT NOT NULL
    )''')
    conn.execute('''CREATE TABLE IF NOT EXISTS adms_restrict_waits (
        device_id INTEGER NOT NULL,
        user_id TEXT NOT NULL,
        asked_at TEXT NOT NULL,
        PRIMARY KEY (device_id, user_id)
    )''')
    conn.execute('''CREATE TABLE IF NOT EXISTS employee_allowed_devices (
        employee_number TEXT NOT NULL,
        device_id INTEGER NOT NULL,
        PRIMARY KEY (employee_number, device_id)
    )''')


# ------------------------------------------------------------ مَن الموقوف

def blocked_map(conn):
    """{employee_number: تاريخ الإيقاف أو None} للموظّفين الموقوفين.

    None = موقوفٌ بلا تاريخٍ معروف: تُرفض كلُّ بصماته الجديدة."""
    out = {}
    try:
        rows = conn.execute('''
            SELECT e.id, e.employee_number, e.end_of_service_date,
              (SELECT MAX(datetime(a.changed_at, 'localtime')) FROM employee_audit_log a
                WHERE a.employee_id = e.id AND a.field = 'is_active'
                  AND COALESCE(a.new_value, '') IN ('0', 'False', 'false')
                  AND COALESCE(a.source, '') != ?) AS deactivated_at,
              (SELECT COUNT(*) FROM employee_audit_log a
                WHERE a.employee_id = e.id AND a.source = ?) AS cap_marks,
              (SELECT COUNT(*) FROM employee_audit_log a
                WHERE a.employee_id = e.id AND a.field = 'is_active'
                  AND COALESCE(a.source, '') != ?) AS manual_marks
            FROM employees e
            WHERE COALESCE(e.is_active, 1) = 0''',
                            (PLAN_CAP_SOURCE, PLAN_CAP_SOURCE, PLAN_CAP_SOURCE)).fetchall()
    except Exception as e:                      # noqa: BLE001 — جدولُ السجلّ غائب في قاعدةٍ قديمة
        logger.warning(f'blocked_map: {e}')
        rows = [dict(r) for r in conn.execute(
            'SELECT id, employee_number, end_of_service_date, NULL AS deactivated_at, '
            '0 AS cap_marks, 0 AS manual_marks FROM employees WHERE COALESCE(is_active, 1) = 0')]
    for r in rows:
        num = str(r['employee_number'] or '').strip()
        if not num:
            continue
        if r['cap_marks'] and not r['manual_marks'] and not r['end_of_service_date']:
            continue                            # فوق حدّ الاشتراك — يبصم حتى يُفعَّل
        cutoff = None
        eos = _parse(r['end_of_service_date'])
        if eos:
            cutoff = datetime(eos.year, eos.month, eos.day) + timedelta(days=1)
        deact = _parse(r['deactivated_at'])
        if deact and (cutoff is None or deact < cutoff):
            cutoff = deact
        out[num] = cutoff
    return out


def is_blocked_punch(blocked, employee_number, at):
    """هل تُرفض هذه البصمة؟ `blocked` من blocked_map."""
    key = str(employee_number or '').strip()
    if key not in blocked:
        return False
    cutoff = blocked[key]
    if cutoff is None:
        return True
    t = at if isinstance(at, datetime) else _parse(at)
    return t is None or t >= cutoff


# ------------------------------------------------------------ الأجهزة المسموحة لكلّ موظّف

WRONG_DEVICE_REASON = 'بصم على جهاز غير مسموح له'


def restriction_map(conn):
    """{employee_number: (since, frozenset(device_ids))} للمحصورين في أجهزةٍ بعينها."""
    ensure_schema(conn)
    allowed = {}
    for r in conn.execute('SELECT employee_number, device_id FROM employee_allowed_devices'):
        allowed.setdefault(str(r['employee_number']).strip(), set()).add(int(r['device_id']))
    out = {}
    for r in conn.execute('SELECT employee_number, since FROM employee_device_rules'):
        num = str(r['employee_number']).strip()
        out[num] = (_parse(r['since']), frozenset(allowed.get(num, ())))
    return out


def denied_on(restr, employee_number, device_id):
    """هل الموظّفُ ممنوعٌ من البصم على هذا الجهاز؟ (جهازٌ غير معروف: لا.)"""
    key = str(employee_number or '').strip()
    if key not in restr or not device_id:
        return False
    return int(device_id) not in restr[key][1]


def is_wrong_device_punch(restr, employee_number, device_id, at):
    """بصمةٌ من جهازٍ غير مسموحٍ له، بعد حصره — لا تدخل الحضور.
    ما بصمه قبل الحصر يُحسب (جهازٌ لم يُزامَن من أسبوع لا يُسقط أسبوعَ عمل)."""
    if not denied_on(restr, employee_number, device_id):
        return False
    since = restr[str(employee_number).strip()][0]
    t = at if isinstance(at, datetime) else _parse(at)
    return since is None or t is None or t >= since


def adms_enable_payload(conn, employee_number):
    import json
    p = json.loads(adms_disable_payload(conn, employee_number))
    p['Pri'] = int(p['Pri']) & ~DISABLED_BIT
    p['Enabled'] = 1
    return json.dumps(p)


def _adms_devices(conn):
    return conn.execute('SELECT id FROM fingerprint_devices WHERE is_active = 1 AND COALESCE(is_adms, 0) = 1').fetchall()


# أجهزة ADMS (SpeedFace وأمثاله) بتتجاهل Enabled=0 وبتّ «معطّل» في Pri: الموظّف بيفضل يبصم.
# فالممنوعُ على جهاز ADMS **بيتمسح منه**، بعد ما نتأكّد إن بصماته عندنا بإصدار الجهاز ده —
# ولو اتسمح له تاني بيترفع عليه ببصماته من عندنا. ولو بصماته مش عندنا: نطلبها من الجهاز
# الأوّل، والمسح بيتم لما توصل (أو بعد RESTRICT_WAIT_HOURS لو الجهاز مارجّعش حاجة).
RESTRICT_WAIT_HOURS = 6


def _restorable(conn, device, pin):
    """بصماتُ الموظّف عندنا وتنفع على الجهاز ده؟ (ممكن نمسحه ونرجّعه زي ما كان)."""
    from utils import biometric_templates as bio
    ids = {str(device[k]).strip() for k in ('serial_number', 'device_ip') if k in device.keys() and device[k]}
    vers = bio.device_versions(device)
    for r in conn.execute("SELECT COALESCE(template_type, 1) t, COALESCE(major_ver, '') v, COALESCE(device_sn, '') sn "
                          "FROM fingerprint_templates WHERE pin = ?", (str(pin).strip(),)):
        if str(r['sn']) in ids or (vers.get(int(r['t'])) and bio.norm_ver(r['v']) == vers[int(r['t'])]):
            return True
    try:
        if ids and conn.execute(f"SELECT 1 FROM fingerprint_faces WHERE pin = ? AND device_sn IN "
                                f"({','.join('?' * len(ids))})", [str(pin).strip()] + list(ids)).fetchone():
            return True
    except Exception:
        pass
    return False


def _queue_once(conn, device_id, ctype, payload):
    if conn.execute("SELECT 1 FROM adms_commands WHERE device_id = ? AND command_type = ? AND payload = ? "
                    "AND status = 'PENDING'", (device_id, ctype, payload)).fetchone():
        return False
    conn.execute("INSERT INTO adms_commands (device_id, command_type, payload, status) VALUES (?, ?, ?, 'PENDING')",
                 (device_id, ctype, payload))
    return True


def adms_restrict(conn, device_id, pin, now=None):
    """الموظّف ممنوع على جهاز ADMS ده: يتمسح منه (بعد ما بصماته تبقى عندنا).
    يُرجع 'deleted' أو 'backup' (طلبنا بصماته من الجهاز الأوّل) أو None."""
    import json
    ensure_schema(conn)
    pin = str(pin).strip()
    device = conn.execute('SELECT * FROM fingerprint_devices WHERE id = ?', (int(device_id),)).fetchone()
    if not device or not pin:
        return None
    now = now or datetime.now()
    wait = conn.execute('SELECT asked_at FROM adms_restrict_waits WHERE device_id = ? AND user_id = ?',
                        (int(device_id), pin)).fetchone()
    timed_out = wait is not None and (_parse(wait['asked_at']) or now) <= now - timedelta(hours=RESTRICT_WAIT_HOURS)
    if _restorable(conn, device, pin) or timed_out:
        _queue_once(conn, int(device_id), 'DATA DELETE USERINFO', json.dumps({'PIN': pin}))
        conn.execute('DELETE FROM adms_restrict_waits WHERE device_id = ? AND user_id = ?', (int(device_id), pin))
        conn.execute('INSERT OR REPLACE INTO device_removed_users (device_id, user_id) VALUES (?, ?)',
                     (int(device_id), pin))
        return 'deleted'
    if wait is None:
        for bt in (1, 9):
            _queue_once(conn, int(device_id), 'DATA QUERY BIODATA', json.dumps({'Type': bt, 'PIN': pin}))
        _queue_once(conn, int(device_id), 'DATA QUERY FINGERTMP', json.dumps({'PIN': pin}))
        conn.execute('INSERT INTO adms_restrict_waits (device_id, user_id, asked_at) VALUES (?, ?, ?)',
                     (int(device_id), pin, now.strftime('%Y-%m-%d %H:%M:%S')))
    return 'backup'


def adms_restrict_pending(conn, device_id):
    """كل ما الجهاز يسأل عن أوامر: اللي طلبنا بصماتهم ووصلت (أو عدّى وقت الانتظار) يتمسحوا."""
    ensure_schema(conn)
    rows = conn.execute('SELECT user_id FROM adms_restrict_waits WHERE device_id = ?', (int(device_id),)).fetchall()
    if not rows:
        return 0
    restr = restriction_map(conn)
    blocked = blocked_map(conn)
    n = 0
    for r in rows:
        # لسه لازم يتشال من الجهاز؟ (ممنوع عليه، أو موقوف)
        if not denied_on(restr, r['user_id'], device_id) and str(r['user_id']).strip() not in blocked:
            conn.execute('DELETE FROM adms_restrict_waits WHERE device_id = ? AND user_id = ?',
                         (int(device_id), r['user_id']))
            continue
        if adms_restrict(conn, device_id, r['user_id']) == 'deleted':
            n += 1
    return n


def adms_full_upload(conn, device_id, pin):
    """يرجّع الموظّف على جهاز ADMS ببياناته وبصماته المحفوظة (بعد ما اتمسح منه)."""
    from utils.fingerprint_utils import adms_template_commands
    device = conn.execute('SELECT * FROM fingerprint_devices WHERE id = ?', (int(device_id),)).fetchone()
    n = int(_queue_once(conn, int(device_id), 'DATA UPDATE USERINFO', adms_enable_payload(conn, pin)))
    for ctype, payload in adms_template_commands(conn, pin, device):
        n += int(_queue_once(conn, int(device_id), ctype, payload))
    return n


def apply_rules_adms(conn, numbers):
    """أوامرُ ADMS بعد تغيير الأجهزة المسموحة لموظّفين: يتمسح من الممنوعة (ببصماته محفوظة
    عندنا)، ويترفع ببصماته على اللي اتمسح منه وبقى مسموح. الموقوفُ مايترفعش. يُرجع عددَ الأجهزة."""
    ensure_schema(conn)
    restr = restriction_map(conn)
    blocked = blocked_map(conn)
    devices = [int(d['id']) for d in _adms_devices(conn)]
    n = 0
    for num in {str(x).strip() for x in numbers if str(x).strip()}:
        for dev_id in devices:
            if denied_on(restr, num, dev_id):
                if adms_restrict(conn, dev_id, num):
                    n += 1
                continue
            conn.execute('DELETE FROM adms_restrict_waits WHERE device_id = ? AND user_id = ?', (dev_id, num))
            if conn.execute('SELECT 1 FROM device_removed_users WHERE device_id = ? AND user_id = ?',
                            (dev_id, num)).fetchone():
                if num not in blocked:
                    adms_full_upload(conn, dev_id, num)
                    n += 1
                conn.execute('DELETE FROM device_removed_users WHERE device_id = ? AND user_id = ?', (dev_id, num))
    return n


def set_allowed_devices(conn, numbers, device_ids):
    """يحصر الموظّفين في `device_ids` — أو None: يرجعون يبصمون على كلّ الأجهزة.

    يُرجع أرقامَ من تغيّر فعلًا (لتُبعث لهم أوامرُ الأجهزة). وقتُ الحصر يتجدّد مع كلّ
    تغيير: البصماتُ قبله تبقى محسوبة."""
    ensure_schema(conn)
    restr = restriction_map(conn)
    new = None if device_ids is None else frozenset(int(x) for x in device_ids)
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    changed = []
    for num in {str(x).strip() for x in numbers if str(x).strip()}:
        old = restr.get(num)
        if new is None:
            if old is None:
                continue
            conn.execute('DELETE FROM employee_device_rules WHERE employee_number = ?', (num,))
            conn.execute('DELETE FROM employee_allowed_devices WHERE employee_number = ?', (num,))
        else:
            if old is not None and old[1] == new:
                continue
            conn.execute('INSERT OR REPLACE INTO employee_device_rules (employee_number, since) VALUES (?, ?)',
                         (num, now))
            conn.execute('DELETE FROM employee_allowed_devices WHERE employee_number = ?', (num,))
            conn.executemany('INSERT INTO employee_allowed_devices (employee_number, device_id) VALUES (?, ?)',
                             [(num, d) for d in sorted(new)])
        changed.append(num)
    return changed


def branch_devices(conn):
    """{اسم الفرع: {device_ids}} — من فرع الجهاز (رقمه أو اسمه)."""
    names = {}
    try:
        names = {int(r['id']): str(r['name'] or '').strip() for r in conn.execute('SELECT id, name FROM branches')}
    except Exception:
        pass
    out = {}
    for d in conn.execute('SELECT * FROM fingerprint_devices WHERE is_active = 1').fetchall():
        keys = d.keys()
        name = ''
        if 'branch_id' in keys and d['branch_id'] and int(d['branch_id']) in names:
            name = names[int(d['branch_id'])]
        elif 'branch_name' in keys:
            name = str(d['branch_name'] or '').strip()
        if name:
            out.setdefault(name, set()).add(int(d['id']))
    return out


def bio_readiness(conn, devices):
    """هل بصمةُ الموظّف تنفع على كلّ جهاز؟ — عشان المستخدم مايحبسش موظّف في أجهزة
    مايعرفش يبصم عليها (البصمة بإصدار خوارزمية، ومابتتقريش على جهاز بإصدار تاني).

    يُرجع (cells, labels):
      cells[(pin, device_id)]: 'ok' بصمته من الجهاز ده أو بإصداره · 'none' الجهاز إصداره
        معروف ومفيش عنده نسخة بيه · 'unknown' إصدار الجهاز لسه مش معروف · 'empty' مالوش
        بصمات محفوظة خالص.
      labels[device_id]: إصدارات الجهاز للعرض («بصمة 10 · وجه 7»).
    """
    from utils import biometric_templates as bio
    have = {}          # pin -> list of (type, ver, sn)
    for r in conn.execute('''SELECT pin, COALESCE(template_type, 1) t, COALESCE(major_ver, '') v,
                                    COALESCE(device_sn, '') sn
                             FROM fingerprint_templates GROUP BY 1, 2, 3, 4'''):
        have.setdefault(str(r['pin']).strip(), []).append((int(r['t']), bio.norm_ver(r['v']), str(r['sn'])))
    try:
        for r in conn.execute("SELECT DISTINCT pin, COALESCE(device_sn, '') sn FROM fingerprint_faces"):
            have.setdefault(str(r['pin']).strip(), []).append((9, '', str(r['sn'])))
    except Exception:
        pass
    cells, labels = {}, {}
    info = []
    for d in devices:
        keys = d.keys()
        ids = {str(d[k]).strip() for k in ('serial_number', 'device_ip') if k in keys and d[k]}
        vers = bio.device_versions(d)
        labels[int(d['id'])] = ' · '.join(f"{bio.TYPE_LABELS.get(t, t)} {v}" for t, v in sorted(vers.items()))
        info.append((int(d['id']), ids, vers))
    pins = {str(r[0]).strip() for r in conn.execute('SELECT employee_number FROM employees')}
    for pin in pins:
        rows = have.get(pin, [])
        for dev_id, ids, vers in info:
            if not rows:
                cells[(pin, dev_id)] = 'empty'
            elif any(sn in ids for _t, _v, sn in rows) or \
                    any(vers.get(t) and v == vers[t] for t, v, _sn in rows):
                cells[(pin, dev_id)] = 'ok'
            elif vers:
                cells[(pin, dev_id)] = 'none'
            else:
                cells[(pin, dev_id)] = 'unknown'
    return cells, labels


def place_labels(conn, device_ids, names=None):
    """أسماء الأماكن بدل أسماء الأجهزة: كل أجهزة الفرع ← اسم الفرع، وجزء منها ← «الفرع (الجهاز)»،
    وجهاز مالوش فرع ← اسمه."""
    ids = {int(x) for x in device_ids}
    if names is None:
        names = {int(r['id']): r['device_name'] for r in conn.execute('SELECT id, device_name FROM fingerprint_devices')}
    by_branch = branch_devices(conn)
    out, used = [], set()
    for b in sorted(by_branch):
        mine = by_branch[b] & ids
        if not mine:
            continue
        used |= mine
        if mine == by_branch[b]:
            out.append(b)
        else:
            out.append(f"{b} ({'، '.join(names.get(i, str(i)) for i in sorted(mine))})")
    out += [names.get(i, str(i)) for i in sorted(ids - used)]
    return out


def record_ignored(conn, employee_id, device_id, check_time, reason='موظف موقوف'):
    ensure_schema(conn)
    conn.execute('INSERT OR IGNORE INTO ignored_punches (employee_id, device_id, check_time, reason) '
                 'VALUES (?, ?, ?, ?)', (employee_id, device_id, str(check_time)[:19], reason))


def move_existing(conn, blocked=None):
    """بصماتٌ سُجّلت **قبل** هذا الإصلاح لموظّفٍ بعد إيقافه — تُنقل من الحضور إلى
    `ignored_punches` فتختفي من التقارير. لا تُحذف: تبقى مقروءةً لمن يسأل.

    فقط ما تاريخُ إيقافه معروف (لا يُخمَّن)، ومن الأجهزة (لا ما أدخله المسؤولُ بيده)،
    وفي فترةٍ لم يُعتمد كشفُها (لا يتغيّر راتبٌ صُرف)."""
    ensure_schema(conn)
    blocked = blocked_map(conn) if blocked is None else blocked
    moved = 0
    try:
        from utils.payroll_engine import date_period_locked
    except Exception:
        date_period_locked = None
    for num, cutoff in blocked.items():
        if cutoff is None:
            continue
        rows = conn.execute('''SELECT a.id, a.employee_id, a.device_id, a.check_time
                               FROM attendance_records a JOIN employees e ON e.id = a.employee_id
                               WHERE CAST(e.employee_number AS TEXT) = ? AND a.check_time >= ?
                                 AND COALESCE(a.source, 'device') IN ('device', 'adms', '')''',
                            (num, cutoff.strftime('%Y-%m-%d %H:%M:%S'))).fetchall()
        for r in rows:
            day = str(r['check_time'])[:10]
            if date_period_locked:
                try:
                    if date_period_locked(conn, day):
                        continue
                except Exception:
                    pass
            record_ignored(conn, r['employee_id'], r['device_id'], r['check_time'],
                           'موظف موقوف — نُقلت من الحضور')
            conn.execute('DELETE FROM attendance_records WHERE id = ?', (r['id'],))
            moved += 1
    if moved:
        conn.commit()
    return moved


def mark_plan_cap(conn, employee_id):
    """موظّفٌ أُضيف غيرَ نشط لتجاوز حدّ الاشتراك — يبصم ولا يُحذف من الأجهزة."""
    try:
        conn.execute('INSERT INTO employee_audit_log (employee_id, field, old_value, new_value, category, '
                     'source, note) VALUES (?, ?, ?, ?, ?, ?, ?)',
                     (employee_id, 'is_active', '', '0', 'status', PLAN_CAP_SOURCE,
                      'أُضيف من جهاز البصمة غيرَ نشط: تجاوز حدّ الاشتراك'))
    except Exception:
        pass


# ------------------------------------------------------------ ADMS

def adms_disable_payload(conn, employee_number):
    """حمولةُ USERINFO تُعطّل الموظّف على جهاز ADMS — ببياناته كما هي."""
    import json
    row = conn.execute('SELECT name, privilege, password, card_number, group_id FROM employees '
                       'WHERE CAST(employee_number AS TEXT) = ?', (str(employee_number),)).fetchone()
    r = dict(row) if row else {}
    try:
        pri = int(r.get('privilege') or 0)
    except (TypeError, ValueError):
        pri = 0
    return json.dumps({
        'PIN': str(employee_number), 'Name': str(r.get('name') or ''),
        'Pri': pri | DISABLED_BIT, 'Passwd': str(r.get('password') or ''),
        'Card': str(r.get('card_number') or '0'), 'Grp': str(r.get('group_id') or '1'),
        'Enabled': 0,
    })


def adms_block(conn, device_id, employee_number):
    """موقوف بصم على جهاز ADMS: يتمسح منه بعد ما بصماته تتحفظ عندنا (adms_restrict).
    كان بيتبعتله Enabled=0، وأجهزة SpeedFace بتتجاهله فيفضل يبصم."""
    return bool(adms_restrict(conn, device_id, employee_number))


# ------------------------------------------------------------ الأجهزة المباشرة (pyzk)

def _connect(device):
    from zk import ZK
    # كما يتّصل fingerprint_sync تمامًا — الإعدادُ نفسُه الذي يعمل عند العملاء.
    zk = ZK(device['device_ip'], port=int(device['device_port'] or 4370), timeout=10)
    return zk.connect()


def write_user(dev, user, privilege):
    """يكتب سجلَّ مستخدمٍ على الجهاز بصلاحيةٍ كما هي — ومعها بتُّ «معطّل».

    `set_user` في pyzk يردّ كلَّ صلاحيةٍ غير 0 و14 إلى 0 فيضيع البتّ ويبقى الموظّفُ
    يبصم. فيُرسَل الأمرُ نفسُه (CMD_USER_WRQ) بالتعبئة نفسِها التي يستعملها pyzk 0.9
    (المثبَّت في requirements) — والفرقُ الصلاحيةُ وحدها.
    """
    from struct import pack
    from zk import const
    enc = getattr(dev, 'encoding', 'UTF-8')
    pwd = str(user.password or '').encode(enc, errors='ignore')
    name = str(user.name or '').encode(enc, errors='ignore')
    card = int(user.card or 0)
    if getattr(dev, 'user_packet_size', 72) == 28:
        gid = int(user.group_id) if str(user.group_id or '').isdigit() else 0
        cs = pack('HB5s8sIxBHI', int(user.uid), int(privilege), pwd, name, card, gid, 0, int(user.user_id))
    else:
        cs = pack('HB8s24s4sx7sx24s', int(user.uid), int(privilege), pwd,
                  name.ljust(24, b'\x00')[:24], pack('<I', card)[:4],
                  str(user.group_id or '').encode(), str(user.user_id).encode())
    resp = dev._ZK__send_command(const.CMD_USER_WRQ, cs, 1024)
    if not resp.get('status'):
        raise RuntimeError("الجهاز رفض تحديث المستخدم")
    dev.refresh_data()


def _direct_devices(conn):
    return conn.execute("SELECT * FROM fingerprint_devices WHERE is_active = 1 "
                        "AND COALESCE(is_adms, 0) = 0 AND COALESCE(device_ip, '') != ''").fetchall()


def _fp_version(dev, conn=None, device=None):
    """إصدارُ خوارزمية البصمة على الجهاز — ويُحفظ له (لتعرف صفحةُ «مين يبصم فين»
    أيُّ بصمات الموظّف تنفع عليه)."""
    try:
        v = str(dev.get_fp_version())
    except Exception:
        return None
    if conn is not None and device is not None:
        try:
            from utils import biometric_templates as bio
            bio.remember_version(conn, device['id'], bio.FINGER, v)
            sn = dev.get_serialnumber()
            if sn and not (device['serial_number'] if 'serial_number' in device.keys() else None):
                conn.execute('UPDATE fingerprint_devices SET serial_number = ? WHERE id = ?', (str(sn), device['id']))
        except Exception:
            pass
    return v


def backup_templates(conn, dev, device, user, fp_ver=None):
    """بصماتُ مستخدمٍ من الجهاز إلى `fingerprint_templates` (لا تُستبدل بصمةٌ محفوظة)."""
    saved = 0
    try:
        temps = dev.get_templates()
    except Exception as e:                      # noqa: BLE001
        logger.warning(f'get_templates failed on {device["device_ip"]}: {e}')
        return 0
    sn = None
    try:
        sn = dev.get_serialnumber()
    except Exception:
        pass
    for f in temps or []:
        if getattr(f, 'uid', None) != user.uid or not getattr(f, 'template', None):
            continue
        from utils import biometric_templates as bio
        saved += bio.save(conn, user.user_id, int(f.fid), base64.b64encode(bytes(f.template)).decode('ascii'),
                          template_type=1, major_ver=fp_ver, fmt='pyzk', valid=int(f.valid or 1),
                          device_sn=sn or device['device_ip'], replace=False)
    return saved


def enforce_device(conn, device, blocked=None):
    """يُعطّل على جهازٍ مباشر كلَّ موظّفٍ موقوف — بعد حفظ بصماته. يُرجع عددَ من عُطّل."""
    ensure_schema(conn)
    blocked = blocked_map(conn) if blocked is None else blocked
    restr = restriction_map(conn)
    # الموقوف، ومَن حُصر في أجهزةٍ ليس هذا منها.
    off = set(blocked) | {n for n in restr if denied_on(restr, n, device['id'])}
    if not off:
        return 0
    dev = _connect(device)
    removed = 0
    try:
        users = dev.get_users() or []
        targets = [u for u in users if str(u.user_id).strip() in off
                   and not (int(u.privilege or 0) & DISABLED_BIT)]
        if not targets:
            return 0
        dev.disable_device()
        fp_ver = _fp_version(dev, conn, device)
        try:
            for u in targets:
                # احتياطًا: بصماتُه عندنا قبل أيّ تغيير على الجهاز.
                backup_templates(conn, dev, device, u, fp_ver)
                # يُعطَّل ولا يُحذف: يبقى ببصماته، والجهازُ يرفض بصمتَه.
                write_user(dev, u, int(u.privilege or 0) | DISABLED_BIT)
                conn.execute('INSERT OR REPLACE INTO device_removed_users (device_id, user_id) VALUES (?, ?)',
                             (device['id'], str(u.user_id)))
                removed += 1
            conn.commit()
        finally:
            try:
                dev.enable_device()
            except Exception:
                pass
    finally:
        try:
            dev.disconnect()
        except Exception:
            pass
    return removed


def restore_device(conn, device):
    """يُفعّل على جهازٍ مباشر من عُطّل عليه وصار نشطًا (أو يرفعه ببصماته إن كان حُذف)."""
    ensure_schema(conn)
    rows = conn.execute('''SELECT r.user_id, e.name, e.privilege, e.password, e.card_number, e.group_id
                           FROM device_removed_users r
                           JOIN employees e ON CAST(e.employee_number AS TEXT) = r.user_id
                           WHERE r.device_id = ? AND COALESCE(e.is_active, 1) = 1''',
                        (device['id'],)).fetchall()
    # مَن حُصر في أجهزةٍ ليس هذا منها يبقى معطّلًا عليه.
    restr = restriction_map(conn)
    rows = [r for r in rows if not denied_on(restr, r['user_id'], device['id'])]
    if not rows:
        return 0
    from zk.finger import Finger
    dev = _connect(device)
    restored = 0
    try:
        dev.disable_device()
        try:
            fp_ver = _fp_version(dev, conn, device)
            users = dev.get_users() or []
            used = {u.uid for u in users}
            by_id = {str(u.user_id): u for u in users}
            for r in rows:
                uid_s = str(r['user_id'])
                if uid_s in by_id:
                    user = by_id[uid_s]
                    if int(user.privilege or 0) & DISABLED_BIT:
                        # كان معطّلًا ببصماته: يُفكّ البتُّ فقط فيبصم كما كان.
                        write_user(dev, user, int(user.privilege or 0) & ~DISABLED_BIT)
                        conn.execute('DELETE FROM device_removed_users WHERE device_id = ? AND user_id = ?',
                                     (device['id'], uid_s))
                        restored += 1
                        continue
                else:
                    # حُذف من الجهاز (نسخة 2.32.0.0 كانت تحذف): يُرفع من جديد ببصماته المحفوظة.
                    uid = int(uid_s) if uid_s.isdigit() and int(uid_s) not in used and int(uid_s) < 65535 \
                        else (max(used) + 1 if used else 1)
                    used.add(uid)
                    try:
                        card = int(r['card_number'] or 0)
                    except (TypeError, ValueError):
                        card = 0
                    dev.set_user(uid=uid, name=str(r['name'] or '')[:24], privilege=int(r['privilege'] or 0),
                                 password=str(r['password'] or ''), group_id=str(r['group_id'] or ''),
                                 user_id=uid_s, card=card)
                    user = next((u for u in (dev.get_users() or []) if str(u.user_id) == uid_s), None)
                if user is not None:
                    from utils import biometric_templates as bio
                    temps, _skipped = bio.pick(conn, uid_s, {1: fp_ver} if fp_ver else {}, types={1})
                    fingers = []
                    for t in temps:
                        try:
                            fingers.append(Finger(user.uid, int(t['finger_id']), int(t['valid'] or 1),
                                                  base64.b64decode(t['template_data'])))
                        except Exception:
                            continue
                    if fingers:
                        try:
                            dev.save_user_template(user, fingers)
                        except Exception as e:           # noqa: BLE001 — بصمةٌ بإصدارٍ آخر
                            logger.warning(f'save_user_template {uid_s}: {e}')
                conn.execute('DELETE FROM device_removed_users WHERE device_id = ? AND user_id = ?',
                             (device['id'], uid_s))
                restored += 1
            conn.commit()
        finally:
            try:
                dev.enable_device()
            except Exception:
                pass
    finally:
        try:
            dev.disconnect()
        except Exception:
            pass
    return restored


def enforce_all(conn=None):
    """على كلّ الأجهزة المباشرة: احذف الموقوفين، وأرجع من فُعّل. لا يُطلق استثناءً."""
    owned = conn is None
    if owned:
        from utils.db import get_db_connection
        conn = get_db_connection()
    out = {'removed': 0, 'restored': 0, 'errors': []}
    try:
        ensure_schema(conn)
        blocked = blocked_map(conn)
        try:
            out['moved'] = move_existing(conn, blocked)
        except Exception as e:                  # noqa: BLE001
            out['errors'].append(f'move: {str(e)[:120]}')
        for d in _direct_devices(conn):
            try:
                out['deleted'] = out.get('deleted', 0) + run_pending_deletes(conn, d)
                out['removed'] += enforce_device(conn, d, blocked)
                out['restored'] += restore_device(conn, d)
            except Exception as e:              # noqa: BLE001 — جهازٌ مقفول لا يوقف البقيّة
                out['errors'].append(f"{d['device_ip']}: {str(e)[:120]}")
        conn.commit()
    finally:
        if owned:
            try:
                conn.close()
            except Exception:
                pass
    return out


def _delete_on_device(dev, user_ids, conn=None, device=None):
    """يحذف من جهازٍ مفتوح — وبصماتُ كلٍّ منهم تُحفظ عندنا أولًا (لتُرفع إن استُرجع)."""
    ids = {str(x).strip() for x in user_ids}
    targets = [u for u in dev.get_users() or [] if str(u.user_id).strip() in ids]
    if targets and conn is not None and device is not None:
        fp_ver = _fp_version(dev)
        for u in targets:
            backup_templates(conn, dev, device, u, fp_ver)
    for u in targets:
        dev.delete_user(uid=u.uid)
    return len(targets)


def delete_from_direct(employee_number, device_ids=None, conn=None):
    """موظّفٌ حُذف من البرنامج: يُحذف من الأجهزة المباشرة (كلِّها، أو المختارة).

    `employee_number`: رقمٌ واحد أو قائمة (مسح الكلّ: اتّصالٌ واحد بكلّ جهاز).
    جهازٌ مقفول وقتَها لا يُنسى: يُقيَّد في `device_pending_deletes` وتُعيد
    المزامنةُ كلَّ ساعة المحاولةَ حتى يُحذف منه (كان يبقى عليه يبصم بلا صاحب).
    """
    owned = conn is None
    if owned:
        from utils.db import get_db_connection
        conn = get_db_connection()
    nums = [employee_number] if isinstance(employee_number, (str, int)) else list(employee_number)
    nums = [str(n).strip() for n in nums if str(n).strip()]
    done = 0
    try:
        ensure_schema(conn)
        wanted = {int(x) for x in device_ids} if device_ids is not None else None
        for d in _direct_devices(conn):
            if wanted is not None and int(d['id']) not in wanted:
                continue
            try:
                dev = _connect(d)
                try:
                    done += _delete_on_device(dev, nums, conn, d)
                finally:
                    dev.disconnect()
                conn.executemany('DELETE FROM device_pending_deletes WHERE device_id = ? AND user_id = ?',
                                 [(d['id'], n) for n in nums])
            except Exception as e:              # noqa: BLE001
                logger.warning(f'delete {nums[:5]} from {d["device_ip"]}: {e}')
                conn.executemany('''INSERT INTO device_pending_deletes (device_id, user_id, last_error)
                                    VALUES (?, ?, ?) ON CONFLICT(device_id, user_id)
                                    DO UPDATE SET last_error = excluded.last_error''',
                                 [(d['id'], n, str(e)[:200]) for n in nums])
        conn.commit()
    finally:
        if owned:
            try:
                conn.close()
            except Exception:
                pass
    return done


def delete_from_direct_now(employee_number, device_ids=None):
    if not isinstance(employee_number, (str, int)):
        employee_number = [str(n) for n in employee_number]
    else:
        employee_number = str(employee_number)
    threading.Thread(target=delete_from_direct, args=(employee_number, device_ids),
                     daemon=True).start()


def run_pending_deletes(conn, device):
    """ما لم يُحذف من جهازٍ كان مقفولًا: يُحذف الآن. يُرجع العدد."""
    ensure_schema(conn)
    rows = [r['user_id'] for r in conn.execute(
        'SELECT user_id FROM device_pending_deletes WHERE device_id = ?', (device['id'],)).fetchall()]
    if not rows:
        return 0
    dev = _connect(device)
    try:
        done = _delete_on_device(dev, rows, conn, device)
    finally:
        try:
            dev.disconnect()
        except Exception:
            pass
    conn.execute('DELETE FROM device_pending_deletes WHERE device_id = ?', (device['id'],))
    conn.commit()
    return done


def cancel_pending_deletes(conn, employee_number):
    """موظّفٌ استُرجع من سلّة المحذوفات: لا يُحذف من الأجهزة بعد ذلك."""
    ensure_schema(conn)
    conn.execute('DELETE FROM device_pending_deletes WHERE user_id = ?', (str(employee_number).strip(),))


# ------------------------------------------------------------ نقلُ موظّفٍ ببصماته بين الأجهزة

def _template_compatible(row, fp_ver):
    """بصمةٌ تنفع على جهازٍ بإصدار خوارزميةٍ معيّن؟ (10 لا تُقرأ على 12 والعكس.)"""
    keys = row.keys()
    fmt = str(row['format']) if 'format' in keys and row['format'] is not None else ''
    if fmt == '1':                       # ISO — أجهزةُ ZK بالاتّصال المباشر لا تقرؤها
        return False
    major = str(row['major_ver']).strip() if 'major_ver' in keys and row['major_ver'] not in (None, '') else ''
    if not major or not fp_ver:
        return True                      # لا نعرف — نجرّب، والجهازُ يرفض إن لم تنفع
    return major.split('.')[0] == str(fp_ver).split('.')[0]


def pull_direct(conn, device, pins=None):
    """بصماتُ الموظّفين من جهازٍ مباشر إلى البرنامج — لتُرفع منه لأيّ جهازٍ آخر.

    `pins`: أرقامُ موظّفين بعينهم، أو None للكلّ. تُستبدل البصمةُ المحفوظة لنفس الإصبع
    (السحبُ طلبٌ صريح، والأحدثُ هو الصحيح). يُرجع ملخّصًا.
    """
    pins = {str(p).strip() for p in (pins or []) if str(p).strip()}
    dev = _connect(device)
    try:
        dev.disable_device()
        try:
            fp_ver = _fp_version(dev, conn, device)
            try:
                sn = dev.get_serialnumber()
            except Exception:
                sn = None
            users = dev.get_users() or []
            temps = dev.get_templates() or []
        finally:
            try:
                dev.enable_device()
            except Exception:
                pass
    finally:
        try:
            dev.disconnect()
        except Exception:
            pass
    by_uid = {u.uid: u for u in users}
    known = {str(r[0]).strip() for r in conn.execute('SELECT employee_number FROM employees').fetchall()}
    saved, people, unknown = 0, set(), set()
    for f in temps:
        u = by_uid.get(getattr(f, 'uid', None))
        tpl = getattr(f, 'template', None)
        if u is None or not tpl:
            continue
        pin = str(u.user_id).strip()
        if pins and pin not in pins:
            continue
        if pin not in known:
            unknown.add(pin)
            continue
        # نسختُه بإصداره: لا تمحو نسخةَ الإصبع نفسِه من جهازٍ بإصدارٍ آخر (SpeedFace).
        from utils import biometric_templates as bio
        bio.save(conn, pin, int(f.fid), base64.b64encode(bytes(tpl)).decode('ascii'), template_type=1,
                 major_ver=fp_ver, fmt='pyzk', valid=int(f.valid or 1), device_sn=sn or device['device_ip'])
        saved += 1
        people.add(pin)
    conn.commit()
    on_device = {str(u.user_id).strip() for u in users}
    return {'templates': saved, 'employees': len(people), 'fp_version': fp_ver,
            'unknown': sorted(unknown), 'missing': sorted(pins - on_device) if pins else []}


def _free_uid(pin, used):
    if pin.isdigit() and 0 < int(pin) < 65535 and int(pin) not in used:
        return int(pin)
    uid = (max(used) + 1) if used else 1
    while uid in used:
        uid += 1
    return uid


def push_direct(conn, device, employees, templates='all'):
    """يرفع موظّفين لجهازٍ مباشر **ببصماتهم** المحفوظة عندنا.

    - المكانُ (uid) على الجهاز: مكانُه إن كان عليه، وإلّا مكانٌ فارغ — كان يُرفع
      برقمه في الجدول فيكتب فوق موظّفٍ آخر يشغل الرقمَ نفسه على الجهاز.
    - البصماتُ التي بإصدار خوارزميةٍ غير إصدار الجهاز لا تُرسَل (لا تنفع)، ويُقال ذلك.
    - `templates='missing'`: البصمات بس للموظف اللي ماكانش على الجهاز (تعديل بيانات) — اللي
      عليه بصماته باقية على الجهاز.
    يُرجع (details, fp_version) — details: [{user, status, message}].
    """
    from zk.finger import Finger
    from zk.user import User
    details = []
    dev = _connect(device)
    try:
        dev.disable_device()
        try:
            fp_ver = _fp_version(dev, conn, device)
            users = dev.get_users() or []
            by_id = {str(u.user_id).strip(): u for u in users}
            restr = restriction_map(conn)
            used = {u.uid for u in users}
            for emp in employees:
                pin = str(emp['employee_number']).strip()
                name = str(emp['name'] or '')[:24]
                try:
                    priv = 14 if int(emp['privilege'] or 0) == 14 else 0
                except (TypeError, ValueError, IndexError, KeyError):
                    priv = 0
                try:
                    card = int(emp['card_number'] or 0)
                except (TypeError, ValueError, IndexError, KeyError):
                    card = 0
                try:
                    password = str(emp['password'] or '')
                except (IndexError, KeyError):
                    password = ''
                try:
                    old = by_id.get(pin)
                    uid = old.uid if old else _free_uid(pin, used)
                    dev.set_user(uid=uid, name=name, privilege=priv, password=password,
                                 group_id='', user_id=pin, card=card)
                    used.add(uid)
                    user = User(uid, name, priv, password, '', pin, card)
                    if denied_on(restr, pin, device['id']):
                        # محصورٌ في أجهزةٍ ليس هذا منها: يُرفع ببصماته لكن متوقّفًا عليه.
                        write_user(dev, user, priv | DISABLED_BIT)
                        conn.execute('INSERT OR REPLACE INTO device_removed_users (device_id, user_id) VALUES (?, ?)',
                                     (device['id'], pin))
                    # لكلّ إصبع نسختُه بإصدار هذا الجهاز (utils/biometric_templates).
                    from utils import biometric_templates as bio
                    if templates == 'missing' and old is not None:
                        details.append({'user': emp['name'], 'status': 'success', 'message': 'اتحدّثت بياناته'})
                        continue
                    rows, skipped = bio.pick(conn, pin, {1: bio.norm_ver(fp_ver)} if fp_ver else {}, types={1})
                    any_saved = conn.execute("SELECT 1 FROM fingerprint_templates WHERE pin = ? "
                                             "AND COALESCE(template_type, 1) = 1", (pin,)).fetchone()
                    fingers = []
                    for t in rows:
                        if not _template_compatible(t, fp_ver) or not (0 <= int(t['finger_id']) <= 9):
                            skipped += 1
                            continue
                        try:
                            fingers.append(Finger(uid, int(t['finger_id']), int(t['valid'] or 1),
                                                  base64.b64decode(t['template_data'])))
                        except Exception:
                            skipped += 1
                    msg = 'رُفع'
                    status = 'success'
                    if fingers:
                        try:
                            dev.save_user_template(user, fingers)
                            msg += f' ومعه {len(fingers)} بصمة'
                        except Exception as e:      # noqa: BLE001
                            status = 'warning'
                            msg += f' بدون بصمات — الجهاز رفضها ({str(e)[:60]})'
                    elif not any_saved:
                        status = 'warning'
                        msg += ' بدون بصمات — لا بصمات محفوظة له (اسحبها من جهازه أولًا)'
                    if skipped:
                        status = 'warning'
                        msg += (f' — {skipped} صباع محفوظ بإصدار غير إصدار الجهاز ({fp_ver or "؟"})'
                                f'، يبصم عليه مرة واحدة وتسحبها منه')
                    details.append({'user': emp['name'], 'status': status, 'message': msg})
                except Exception as e:              # noqa: BLE001
                    details.append({'user': emp['name'], 'status': 'error',
                                    'message': f'فشل الرفع: {str(e)[:120]}'})
        finally:
            try:
                dev.enable_device()
            except Exception:
                pass
    finally:
        try:
            dev.disconnect()
        except Exception:
            pass
    return details, fp_ver


def apply_now():
    """بعد إيقاف موظّف أو تفعيله: الأجهزةُ المباشرة تُعالَج الآن في الخلفيّة —
    والجهازُ المقفول تُعالجه المزامنةُ التالية."""
    threading.Thread(target=enforce_all, daemon=True).start()


def push_employee(employee_number, conn=None, templates='all'):
    """تعديل موظّف أو إضافته: يتبعت لكل الأجهزة المباشرة (IP) دلوقتي — زي ما بيتبعت لأجهزة ADMS.
    كان مابيوصلهاش غير برفعٍ يدوي، فالاسم أو الكارت الجديد مايظهرش على الجهاز.
    الممنوعُ على جهاز بيترفع عليه متوقّف (push_direct)، والموقوفُ مابيترفعش. جهازٌ مقفول بيتساب."""
    owned = conn is None
    if owned:
        from utils.db import get_db_connection
        conn = get_db_connection()
    out = {'pushed': 0, 'errors': []}
    try:
        ensure_schema(conn)
        emp = conn.execute('SELECT * FROM employees WHERE CAST(employee_number AS TEXT) = ?',
                           (str(employee_number).strip(),)).fetchone()
        if not emp or str(emp['employee_number']).strip() in blocked_map(conn):
            return out
        for d in _direct_devices(conn):
            try:
                details, _ver = push_direct(conn, d, [emp], templates=templates)
                if details and details[0]['status'] != 'error':
                    out['pushed'] += 1
                else:
                    out['errors'].append(f"{d['device_ip']}: {details[0]['message'] if details else '?'}")
            except Exception as e:              # noqa: BLE001 — جهازٌ مقفول لا يوقف البقيّة
                out['errors'].append(f"{d['device_ip']}: {str(e)[:120]}")
        conn.commit()
    finally:
        if owned:
            try:
                conn.close()
            except Exception:
                pass
    if out['errors']:
        logger.warning(f'push employee {employee_number}: {out["errors"]}')
    return out


def push_employee_now(employee_number, templates='all'):
    threading.Thread(target=push_employee, args=(str(employee_number),),
                     kwargs={'templates': templates}, daemon=True).start()
