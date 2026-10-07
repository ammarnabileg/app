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

١) **من الجهاز يُحذف.** ADMS بأمر حذف، والمباشرُ بـpyzk فورًا (في الخلفيّة) — وقبل
   الحذف تُحفظ بصماتُه من الجهاز عندنا، فإذا أُعيد تفعيلُه رجع للجهاز **ببصماته**
   ولا يحتاج يسجّل من جديد. وجهازٌ مقفول وقتَها يُعالَج في المزامنة التالية
   (كلَّ ساعة): كلُّ موظّفٍ موقوفٍ على جهازٍ مباشر يُحذف منه.
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

def adms_block(conn, device_id, employee_number):
    """أمرُ حذفٍ لجهاز ADMS بصم عليه موقوف — مرّةً واحدة ما دام معلّقًا."""
    import json
    payload = json.dumps({'PIN': str(employee_number)})
    if conn.execute("SELECT 1 FROM adms_commands WHERE device_id = ? AND command_type = 'DATA DELETE USERINFO' "
                    "AND payload = ? AND status = 'PENDING'", (device_id, payload)).fetchone():
        return False
    conn.execute("INSERT INTO adms_commands (device_id, command_type, payload, status) "
                 "VALUES (?, 'DATA DELETE USERINFO', ?, 'PENDING')", (device_id, payload))
    return True


# ------------------------------------------------------------ الأجهزة المباشرة (pyzk)

def _connect(device):
    from zk import ZK
    # كما يتّصل fingerprint_sync تمامًا — الإعدادُ نفسُه الذي يعمل عند العملاء.
    zk = ZK(device['device_ip'], port=int(device['device_port'] or 4370), timeout=10)
    return zk.connect()


def _direct_devices(conn):
    return conn.execute("SELECT * FROM fingerprint_devices WHERE is_active = 1 "
                        "AND COALESCE(is_adms, 0) = 0 AND COALESCE(device_ip, '') != ''").fetchall()


def _fp_version(dev):
    try:
        return str(dev.get_fp_version())
    except Exception:
        return None


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
        cur = conn.execute('''INSERT OR IGNORE INTO fingerprint_templates
            (device_sn, pin, finger_id, valid, template_type, major_ver, format, template_data)
            VALUES (?, ?, ?, ?, 1, ?, 'pyzk', ?)''',
                           (sn or device['device_ip'], str(user.user_id), int(f.fid), int(f.valid or 1),
                            fp_ver, base64.b64encode(bytes(f.template)).decode('ascii')))
        saved += cur.rowcount
    return saved


def enforce_device(conn, device, blocked=None):
    """يحذف من جهازٍ مباشر كلَّ موظّفٍ موقوف — بعد حفظ بصماته. يُرجع عددَ من حُذف."""
    ensure_schema(conn)
    blocked = blocked_map(conn) if blocked is None else blocked
    if not blocked:
        return 0
    dev = _connect(device)
    removed = 0
    try:
        users = dev.get_users() or []
        targets = [u for u in users if str(u.user_id).strip() in blocked]
        if not targets:
            return 0
        dev.disable_device()
        fp_ver = _fp_version(dev)
        try:
            for u in targets:
                backup_templates(conn, dev, device, u, fp_ver)
                dev.delete_user(uid=u.uid)
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
    """يرجّع لجهازٍ مباشر من حُذف منه وصار نشطًا — بياناته وبصماته المحفوظة."""
    ensure_schema(conn)
    rows = conn.execute('''SELECT r.user_id, e.name, e.privilege, e.password, e.card_number, e.group_id
                           FROM device_removed_users r
                           JOIN employees e ON CAST(e.employee_number AS TEXT) = r.user_id
                           WHERE r.device_id = ? AND COALESCE(e.is_active, 1) = 1''',
                        (device['id'],)).fetchall()
    if not rows:
        return 0
    from zk.finger import Finger
    dev = _connect(device)
    restored = 0
    try:
        dev.disable_device()
        try:
            users = dev.get_users() or []
            used = {u.uid for u in users}
            by_id = {str(u.user_id): u for u in users}
            for r in rows:
                uid_s = str(r['user_id'])
                if uid_s in by_id:
                    user = by_id[uid_s]
                else:
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
                    temps = conn.execute("SELECT finger_id, valid, template_data FROM fingerprint_templates "
                                         "WHERE pin = ? AND COALESCE(template_type, 1) = 1", (uid_s,)).fetchall()
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


def delete_from_direct(employee_number):
    """موظّفٌ حُذف من البرنامج: يُحذف من كلّ جهازٍ مباشر (لا رجعة — لا تفعيلَ بعد الحذف)."""
    from utils.db import get_db_connection
    conn = get_db_connection()
    done = 0
    try:
        for d in _direct_devices(conn):
            try:
                dev = _connect(d)
                try:
                    for u in dev.get_users() or []:
                        if str(u.user_id).strip() == str(employee_number).strip():
                            dev.delete_user(uid=u.uid)
                            done += 1
                finally:
                    dev.disconnect()
            except Exception as e:              # noqa: BLE001
                logger.warning(f'delete {employee_number} from {d["device_ip"]}: {e}')
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return done


def delete_from_direct_now(employee_number):
    threading.Thread(target=delete_from_direct, args=(str(employee_number),), daemon=True).start()


def apply_now():
    """بعد إيقاف موظّف أو تفعيله: الأجهزةُ المباشرة تُعالَج الآن في الخلفيّة —
    والجهازُ المقفول تُعالجه المزامنةُ التالية."""
    threading.Thread(target=enforce_all, daemon=True).start()
