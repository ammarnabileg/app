# -*- coding: utf-8 -*-
"""ما تفعله الشاشاتُ والمزامنة بجهازٍ من الماركات الجديدة (Hikvision / Dahua / Suprema).

أجهزةُ ZKTeco **لا تمرّ من هنا** — مساراتُها القديمة كما هي (registry.is_legacy).
وهنا نفسُ القواعد التي تحكمها، بدالّةٍ واحدة لكلّ حضور (`record_punch`):

- الموقوف: بصمتُه بعد إيقافه لا تدخل الحضور، وتُقيَّد «متجاهَلة» (utils/device_access)؛
- رقمٌ ليس موظّفًا بعد: تُحفظ بصمتُه حتى يُضاف (utils/pending_punches)؛
- التكرار: البصمةُ نفسُها للموظّف نفسِه في الوقت نفسِه لا تُحفظ مرّتين.

وكلُّ عمليّةٍ لا ترمي على المزامنة: جهازٌ مقفول لا يوقف البقيّة، والخطأ يُعاد ملخّصًا.
"""
import base64
import json
import logging
import secrets
import threading
from datetime import datetime, timedelta

from . import base, registry

logger = logging.getLogger(__name__)


def _conn(conn):
    if conn is not None:
        return conn, False
    from utils.db import get_db_connection
    return get_db_connection(), True


def _close(conn, owned):
    if owned:
        try:
            conn.close()
        except Exception:
            pass


def new_devices(conn, active_only=True):
    sql = f"SELECT * FROM fingerprint_devices WHERE NOT ({registry.LEGACY_SQL})"
    if active_only:
        sql += " AND is_active = 1"
    return conn.execute(sql + " ORDER BY id").fetchall()


def _blocked(conn):
    try:
        from utils import device_access as da
        da.ensure_schema(conn)
        return da, da.blocked_map(conn)
    except Exception:
        return None, {}


# ------------------------------------------------------------ الحضور

def record_punch(conn, device_id, punch, blocked=None, da=None, source='device'):
    """'saved' | 'duplicate' | 'ignored' (موقوف) | 'pending' (ليس موظّفًا بعد)."""
    pin = str(punch.pin).strip()
    when = punch.time.strftime('%Y-%m-%d %H:%M:%S')
    emp = conn.execute('SELECT id FROM employees WHERE employee_number = ?', (pin,)).fetchone()
    if not emp:
        from utils import pending_punches
        pending_punches.record(conn, device_id, pin, when, punch.check_type, punch.verify, source=source)
        return 'pending'
    if blocked and da is not None and da.is_blocked_punch(blocked, pin, when):
        da.record_ignored(conn, emp[0], device_id, when)
        return 'ignored'
    if conn.execute('SELECT 1 FROM attendance_records WHERE employee_id = ? AND check_time = ?',
                    (emp[0], when)).fetchone():
        return 'duplicate'
    conn.execute('''INSERT INTO attendance_records (employee_id, device_id, check_time, check_type, verify_code)
                    VALUES (?, ?, ?, ?, ?)''', (emp[0], device_id, when, punch.check_type, punch.verify))
    return 'saved'


def record_many(conn, device_id, punches, source='device'):
    da, blocked = _blocked(conn)
    out = {'saved': 0, 'duplicate': 0, 'ignored': 0, 'pending': 0}
    for p in punches:
        out[record_punch(conn, device_id, p, blocked, da, source)] += 1
    conn.commit()
    return out


def _parse_dt(s):
    if not s:
        return None
    try:
        return datetime.strptime(str(s)[:19], '%Y-%m-%d %H:%M:%S')
    except ValueError:
        return None


def sync_attendance(conn, device, drv=None):
    """يسحب الحضور منذ آخر مزامنة (بتداخل ١٠ دقائق — التكرارُ لا يُحفظ مرّتين)."""
    drv = drv or registry.get_driver(device)
    if not drv.supports(base.PULL_ATTENDANCE):
        return {'skipped': 'لا يدعم سحب الحضور'}
    last = _parse_dt(device['last_sync_time'])
    since = (last - timedelta(minutes=10)) if last else None
    now = datetime.now().replace(microsecond=0)
    punches = drv.pull_attendance(since, now)
    res = record_many(conn, device['id'], punches, source=drv.key)
    newest = max([p.time for p in punches] + ([last] if last else []), default=None)
    conn.execute('UPDATE fingerprint_devices SET last_sync_time = ?, last_activity = CURRENT_TIMESTAMP WHERE id = ?',
                 ((newest or now).strftime('%Y-%m-%d %H:%M:%S'), device['id']))
    conn.commit()
    res['pulled'] = len(punches)
    return res


def sync_all(conn=None):
    """كلُّ الأجهزة الجديدة النشطة. لا يرمي."""
    conn, owned = _conn(conn)
    out = {'devices': 0, 'saved': 0, 'pending': 0, 'ignored': 0, 'errors': []}
    try:
        for d in new_devices(conn):
            try:
                r = sync_attendance(conn, d)
                if 'skipped' in r:
                    continue
                out['devices'] += 1
                for k in ('saved', 'pending', 'ignored'):
                    out[k] += r.get(k, 0)
            except Exception as e:              # noqa: BLE001 — جهازٌ مقفول لا يوقف البقيّة
                out['errors'].append(f"{d['device_name']}: {str(e)[:150]}")
        if out['pending'] or out['saved']:
            try:
                from utils import pending_punches
                pending_punches.adopt(conn)
            except Exception:
                pass
    finally:
        _close(conn, owned)
    return out


# ------------------------------------------------------------ الموظّفون

def _photo_bytes(conn, pin):
    try:
        row = conn.execute('SELECT photo_data FROM user_photos WHERE pin = ?', (str(pin),)).fetchone()
    except Exception:
        return None
    if not row or not row[0]:
        return None
    data = str(row[0])
    if data.startswith('data:') and ',' in data:
        data = data.split(',', 1)[1]
    try:
        return base64.b64decode(data)
    except Exception:
        return None


def push_employees(conn, device, employees, drv=None):
    """يرفع موظّفين لجهاز — ووجوهَهم من صورهم إن كان الجهازُ يقبلها.
    يُرجع [{user, status, message}] كصفحة «إدارة مستخدمي الأجهزة»."""
    drv = drv or registry.get_driver(device)
    details = []
    for emp in employees:
        pin = str(emp['employee_number']).strip()
        try:
            card = emp['card_number']
        except (IndexError, KeyError):
            card = ''
        try:
            active = int(emp['is_active'] if 'is_active' in emp.keys() else 1) == 1
        except Exception:
            active = True
        try:
            drv.upsert_user(pin, emp['name'], card or '', enabled=active)
            msg, status = 'رُفع', 'success'
            if drv.supports(base.FACE_PHOTO):
                photo = _photo_bytes(conn, pin)
                if photo:
                    try:
                        drv.enroll_face_photo(pin, photo)
                        msg += ' ومعه الوش من صورته'
                    except base.DriverError as e:
                        status, msg = 'warning', msg + f' — الوش ما اتسجلش ({str(e)[:80]})'
                else:
                    status, msg = 'warning', msg + ' — مفيش صورة للموظف عشان يتسجل وشه، يسجّل على الجهاز'
            else:
                msg += ' — البصمة والوش بيتسجلوا على الجهاز نفسه'
                status = 'warning'
            details.append({'user': emp['name'], 'status': status, 'message': msg})
        except Exception as e:                  # noqa: BLE001
            details.append({'user': emp['name'], 'status': 'error', 'message': f'فشل الرفع: {str(e)[:150]}'})
    return details


def sync_users(conn, device, drv=None):
    """موظّفو الجهاز → fingerprint_users، ومن ليس موظّفًا يُضاف (كأجهزة ZKTeco) بإشعار
    «موظف جديد على جهاز كذا» وبصماتُه المنتظرة تدخل حضورَه."""
    drv = drv or registry.get_driver(device)
    if not drv.supports(base.USERS):
        return {'skipped': 'لا يدعم قراءة الموظفين'}
    users = drv.list_users()
    from utils.sensitive_ops import deleted_pins
    gone = deleted_pins(conn)
    added = 0
    for u in users:
        if not u.pin:
            continue
        conn.execute('''INSERT OR REPLACE INTO fingerprint_users (user_id, device_id, name, privilege, password,
                        group_id, card_number, created_at) VALUES (?, ?, ?, 0, '', '', ?, CURRENT_TIMESTAMP)''',
                     (u.pin, device['id'], u.name or '', u.card or ''))
        if u.pin in gone or conn.execute('SELECT 1 FROM employees WHERE employee_number = ?', (u.pin,)).fetchone():
            continue
        try:
            from utils.plan_limits import can_activate
            active = 1 if can_activate(conn)[0] else 0
        except Exception:
            active = 1
        cur = conn.execute('''INSERT INTO employees (employee_number, name, department, position, hire_date, salary,
                              default_start_time, default_end_time, shift_type, is_active, created_at)
                              VALUES (?, ?, 'غير محدد', 'موظف', ?, 0, '09:00', '17:00', 'صباحي', ?, CURRENT_TIMESTAMP)''',
                           (u.pin, u.name or u.pin, datetime.now().strftime('%Y-%m-%d'), active))
        new_id = cur.lastrowid
        if not active:
            try:
                from utils.device_access import mark_plan_cap
                mark_plan_cap(conn, new_id)
            except Exception:
                pass
        from utils import device_new_employees
        device_new_employees.record(conn, new_id, device['id'])
        added += 1
    conn.commit()
    if added:
        from utils import pending_punches
        pending_punches.adopt(conn)
    return {'users': len(users), 'employees_added': added}


def sync_users_all(conn=None):
    conn, owned = _conn(conn)
    out = {'devices': 0, 'employees_added': 0, 'errors': []}
    try:
        for d in new_devices(conn):
            try:
                r = sync_users(conn, d)
                if 'skipped' in r:
                    continue
                out['devices'] += 1
                out['employees_added'] += r['employees_added']
            except Exception as e:              # noqa: BLE001
                out['errors'].append(f"{d['device_name']}: {str(e)[:150]}")
    finally:
        _close(conn, owned)
    return out


# ------------------------------------------------------------ الموقوفون والحذف

def enforce_device(conn, device, blocked, drv=None):
    """الموقوفُ يُوقَف على الجهاز (لا يُحذف)، ومن عاد نشطًا يُفعَّل. يُرجع (أُوقف، فُعّل)."""
    drv = drv or registry.get_driver(device)
    if not drv.supports(base.DISABLE):
        return 0, 0
    from utils import device_access as da
    da.ensure_schema(conn)
    done = {r[0] for r in conn.execute('SELECT user_id FROM device_removed_users WHERE device_id = ?',
                                       (device['id'],)).fetchall()}
    on_device = None
    if drv.supports(base.USERS):
        on_device = {u.pin for u in drv.list_users()}
    stopped = 0
    for pin in blocked:
        if pin in done or (on_device is not None and pin not in on_device):
            continue
        drv.set_enabled(pin, False)
        conn.execute('INSERT OR REPLACE INTO device_removed_users (device_id, user_id) VALUES (?, ?)',
                     (device['id'], pin))
        stopped += 1
    restored = 0
    for pin in done - set(blocked):
        if conn.execute('SELECT 1 FROM employees WHERE employee_number = ? AND COALESCE(is_active, 1) = 1',
                        (pin,)).fetchone():
            drv.set_enabled(pin, True)
            conn.execute('DELETE FROM device_removed_users WHERE device_id = ? AND user_id = ?', (device['id'], pin))
            restored += 1
    conn.commit()
    return stopped, restored


def enforce_all(conn=None):
    conn, owned = _conn(conn)
    out = {'removed': 0, 'restored': 0, 'errors': []}
    try:
        da, blocked = _blocked(conn)
        for d in new_devices(conn):
            try:
                s, r = enforce_device(conn, d, blocked)
                out['removed'] += s
                out['restored'] += r
            except Exception as e:              # noqa: BLE001
                out['errors'].append(f"{d['device_name']}: {str(e)[:150]}")
    finally:
        _close(conn, owned)
    return out


def delete_pins(conn, device, pins, drv=None):
    """يحذف من جهاز — وما فشل يُقيَّد ليُعاد في المزامنة التالية."""
    from utils import device_access as da
    da.ensure_schema(conn)
    drv = drv or registry.get_driver(device)
    done = 0
    for pin in pins:
        pin = str(pin).strip()
        try:
            drv.delete_user(pin)
            conn.execute('DELETE FROM device_pending_deletes WHERE device_id = ? AND user_id = ?', (device['id'], pin))
            done += 1
        except Exception as e:                  # noqa: BLE001
            conn.execute('''INSERT INTO device_pending_deletes (device_id, user_id, last_error) VALUES (?, ?, ?)
                            ON CONFLICT(device_id, user_id) DO UPDATE SET last_error = excluded.last_error''',
                         (device['id'], pin, str(e)[:200]))
    conn.commit()
    return done


def delete_now(pins, device_ids):
    """في الخلفيّة — من شاشة الحذف (utils/sensitive_ops)."""
    def run():
        from utils.db import get_db_connection
        conn = get_db_connection()
        try:
            for d in new_devices(conn):
                if int(d['id']) in {int(x) for x in device_ids}:
                    delete_pins(conn, d, pins)
        except Exception as e:                  # noqa: BLE001
            logger.warning(f'delete on new devices: {e}')
        finally:
            _close(conn, True)
    threading.Thread(target=run, daemon=True).start()


def run_pending_deletes(conn=None):
    conn, owned = _conn(conn)
    n = 0
    try:
        from utils import device_access as da
        da.ensure_schema(conn)
        for d in new_devices(conn):
            pins = [r[0] for r in conn.execute('SELECT user_id FROM device_pending_deletes WHERE device_id = ?',
                                               (d['id'],)).fetchall()]
            if pins:
                n += delete_pins(conn, d, pins)
    finally:
        _close(conn, owned)
    return n


def run_cycle():
    """من المزامنة كلَّ ساعة: الحضور، ثمّ الحذف المؤجّل، ثمّ الموقوفون. ملخّصٌ لا استثناء."""
    out = {}
    for name, fn in (('attendance', sync_all), ('deletes', run_pending_deletes), ('enforce', enforce_all)):
        try:
            out[name] = fn()
        except Exception as e:                  # noqa: BLE001
            out[name] = {'error': str(e)[:150]}
    return out


# ------------------------------------------------------------ الاستقبال اللحظيّ

def push_token(device):
    try:
        return (json.loads(device['driver_options'] or '{}') or {}).get('push_token') or ''
    except Exception:
        return ''


def ensure_push_token(conn, device_id):
    row = conn.execute('SELECT driver_options FROM fingerprint_devices WHERE id = ?', (device_id,)).fetchone()
    try:
        opts = json.loads((row[0] if row else '') or '{}') or {}
    except Exception:
        opts = {}
    if not opts.get('push_token'):
        opts['push_token'] = secrets.token_urlsafe(18)
        conn.execute('UPDATE fingerprint_devices SET driver_options = ? WHERE id = ?', (json.dumps(opts), device_id))
        conn.commit()
    return opts['push_token']


def receive_push(conn, driver_key, token, request):
    """حدثٌ يرسله الجهاز بنفسه. الرمزُ في العنوان يخصّ جهازًا واحدًا مسجّلًا ونشطًا —
    فمن يعرف عنوانَ البرنامج وحده لا يدفع بصماتٍ مزيّفة. يُرجع ملخّصًا."""
    if not token:
        return {'ok': False, 'reason': 'no_token'}
    device = None
    for d in new_devices(conn):
        if registry.driver_key(d) == driver_key and secrets.compare_digest(push_token(d), token):
            device = d
            break
    if device is None:
        return {'ok': False, 'reason': 'unknown_device'}
    cls = registry.driver_class(driver_key)
    _ident, punches = cls.parse_push(request)
    res = record_many(conn, device['id'], punches, source=driver_key) if punches else {'saved': 0}
    conn.execute('UPDATE fingerprint_devices SET last_activity = CURRENT_TIMESTAMP WHERE id = ?', (device['id'],))
    conn.commit()
    return {'ok': True, **res}
