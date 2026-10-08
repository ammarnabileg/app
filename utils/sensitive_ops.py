# -*- coding: utf-8 -*-
"""العمليات الحساسة: مسحُ الموظّفين والأجهزة — بسجلٍّ وسلّة محذوفات.

## لا شيء يُمسح نهائيًّا

كلُّ مسحٍ يحفظ الصفَّ كما كان (JSON) في `recycle_bin` قبل حذفه، ويُسترجع منها
بنفس رقمه في الجدول — فتعود معه بياناتُه المرتبطة (حضور، رواتب، إجازات…)، لأنها
لا تُحذف أصلًا مع الموظّف (القاعدةُ بلا مفاتيح أجنبيّة متتالية). والجداولُ
بـAUTOINCREMENT، فلا يأخذ موظّفٌ جديد رقمَ المحذوف.

## السجلّ

كلُّ مسحٍ واسترجاعٍ يُكتب في `sensitive_audit_log`: مَن، ماذا، على مَن، متى،
ومن أيّ جهاز كمبيوتر (IP) — من زرّ الحذف في «إدارة الموظفين» أو من صفحة
السوبر أدمن.

## المسح من الأجهزة

- «من النظام فقط»: يبقى على أجهزة البصمة. ولا يُعاد إنشاؤه تلقائيًّا من الجهاز في
  المزامنة (`deleted_pins`) — وإلّا رجع موظّفًا جديدًا بعد ساعة. وبصماتُه لا تجد
  صاحبًا فلا تُحسب.
- «من النظام والأجهزة»: من الأجهزة المختارة وحدها. ADMS بأمرٍ في الطابور، والمباشرُ
  (K40) في الخلفيّة — وبصماتُه تُحفظ عندنا قبل حذفه، وجهازٌ مقفول يُعاد عليه الحذفُ
  في المزامنة التالية.
"""
import json
import logging

logger = logging.getLogger(__name__)

KIND_LABELS = {'employee': 'موظف', 'device': 'جهاز بصمة'}
ACTION_LABELS = {
    'employee.delete': 'مسح موظف',
    'employee.delete_all': 'مسح كل الموظفين',
    'employee.restore': 'استرجاع موظف',
    'device.delete': 'مسح جهاز',
    'device.restore': 'استرجاع جهاز',
}


def ensure_schema(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS recycle_bin (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT NOT NULL,
        original_id INTEGER,
        label TEXT,
        payload TEXT NOT NULL,
        scope TEXT,
        deleted_by TEXT,
        deleted_by_id INTEGER,
        deleted_at TEXT DEFAULT CURRENT_TIMESTAMP,
        restored_at TEXT,
        restored_by TEXT
    )''')
    conn.execute('''CREATE TABLE IF NOT EXISTS sensitive_audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        action TEXT NOT NULL,
        target_kind TEXT,
        target_id INTEGER,
        target_label TEXT,
        details TEXT,
        user_id INTEGER,
        username TEXT,
        ip TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')


def actor():
    """مَن يعمل الآن (من الجلسة) — أو «النظام» خارج طلب ويب."""
    try:
        from flask import has_request_context, request, session
        if has_request_context():
            name = session.get('full_name') or session.get('username') or '؟'
            if session.get('username') and session.get('full_name') and session['full_name'] != session['username']:
                name = f"{session['full_name']} ({session['username']})"
            return {'id': session.get('user_id'), 'name': name, 'ip': request.remote_addr}
    except Exception:
        pass
    return {'id': None, 'name': 'النظام', 'ip': None}


def log(conn, action, kind, target_id, label, details='', who=None):
    ensure_schema(conn)
    who = who or actor()
    conn.execute('''INSERT INTO sensitive_audit_log
        (action, target_kind, target_id, target_label, details, user_id, username, ip)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                 (action, kind, target_id, label, details, who['id'], who['name'], who['ip']))


def deleted_pins(conn):
    """أرقامُ موظّفين في السلّة (لم يُسترجعوا): لا تُنشئهم مزامنةُ الأجهزة من جديد."""
    try:
        ensure_schema(conn)
        rows = conn.execute("SELECT payload FROM recycle_bin "
                            "WHERE kind = 'employee' AND restored_at IS NULL").fetchall()
    except Exception:
        return set()
    out = set()
    for r in rows:
        try:
            num = json.loads(r[0]).get('employee_number')
        except Exception:
            num = None
        if num not in (None, ''):
            out.add(str(num).strip())
    return out


def _devices(conn, device_ids):
    rows = conn.execute('SELECT * FROM fingerprint_devices WHERE is_active = 1').fetchall()
    if device_ids is None:
        return rows
    wanted = {int(x) for x in device_ids}
    return [d for d in rows if int(d['id']) in wanted]


def _scope_text(mode, devices):
    if mode != 'devices':
        return 'من النظام فقط (باقٍ على أجهزة البصمة)'
    if not devices:
        return 'من النظام والأجهزة (لا أجهزة مختارة)'
    return 'من النظام والأجهزة: ' + '، '.join(str(d['device_name']) for d in devices)


def delete_employee(conn, emp_id, mode='devices', device_ids=None, note='', who=None,
                    action='employee.delete', direct_queue=None):
    """يمسح موظّفًا إلى السلّة. mode: 'system' أو 'devices'. يُرجع رقمَه في السلّة أو None.

    direct_queue: قائمةٌ يُضاف إليها (الرقم، الأجهزة المباشرة) بدل الحذف منها الآن —
    لمسح عددٍ كبير باتّصالٍ واحد لكلّ جهاز (`flush_direct`).
    """
    ensure_schema(conn)
    row = conn.execute('SELECT * FROM employees WHERE id = ?', (emp_id,)).fetchone()
    if not row:
        return None
    who = who or actor()
    emp = dict(row)
    num = str(emp.get('employee_number') or '').strip()
    label = f"{emp.get('name') or ''} ({num})"
    devices = _devices(conn, device_ids) if mode == 'devices' else []
    scope = {'mode': 'devices' if mode == 'devices' else 'system',
             'devices': [{'id': d['id'], 'name': d['device_name'], 'adms': int(d['is_adms'] or 0)}
                         for d in devices]}
    cur = conn.execute('''INSERT INTO recycle_bin (kind, original_id, label, payload, scope, deleted_by, deleted_by_id)
                          VALUES ('employee', ?, ?, ?, ?, ?, ?)''',
                       (emp_id, label, json.dumps(emp, ensure_ascii=False, default=str),
                        json.dumps(scope, ensure_ascii=False), who['name'], who['id']))
    bin_id = cur.lastrowid
    direct_ids, brand_ids = [], []
    from utils.devices import registry as _reg
    for d in devices:
        if not _reg.is_legacy(d):
            brand_ids.append(int(d['id']))           # Hikvision وأخواتها — المحرّك الموحّد
        elif d['is_adms']:
            conn.execute("INSERT INTO adms_commands (device_id, command_type, payload, status) "
                         "VALUES (?, 'DATA DELETE USERINFO', ?, 'PENDING')",
                         (d['id'], json.dumps({'PIN': num})))
        else:
            direct_ids.append(int(d['id']))
    conn.execute('DELETE FROM employees WHERE id = ?', (emp_id,))
    details = _scope_text(mode, devices)
    if note:
        details += f' — السبب: {note}'
    log(conn, action, 'employee', emp_id, label, details, who)
    conn.commit()
    if brand_ids and num:
        try:
            from utils.devices import engine as _eng
            _eng.delete_now([num], brand_ids)
        except Exception as e:                  # noqa: BLE001
            logger.warning(f'brand device delete {num}: {e}')
    if direct_ids and num and direct_queue is not None:
        direct_queue.append((num, tuple(sorted(direct_ids))))
    elif direct_ids and num:
        try:
            from utils import device_access
            device_access.delete_from_direct_now(num, direct_ids)
        except Exception as e:                  # noqa: BLE001
            logger.warning(f'direct delete {num}: {e}')
    return bin_id


def flush_direct(direct_queue):
    """ما جمعه `direct_queue`: كلُّ مجموعة أجهزةٍ في خيطٍ واحد باتّصالٍ واحد لكلّ جهاز."""
    if not direct_queue:
        return
    from utils import device_access
    groups = {}
    for num, ids in direct_queue:
        groups.setdefault(ids, []).append(num)
    for ids, nums in groups.items():
        try:
            device_access.delete_from_direct_now(nums, list(ids))
        except Exception as e:                  # noqa: BLE001
            logger.warning(f'direct delete batch: {e}')


def delete_device(conn, device_id, note='', who=None):
    """يمسح جهازَ بصمة إلى السلّة (بياناتُه وبصماتُه المسحوبة تبقى)."""
    ensure_schema(conn)
    row = conn.execute('SELECT * FROM fingerprint_devices WHERE id = ?', (device_id,)).fetchone()
    if not row:
        return None
    who = who or actor()
    dev = dict(row)
    label = f"{dev.get('device_name') or ''} ({dev.get('device_ip') or dev.get('serial_number') or ''})"
    cur = conn.execute('''INSERT INTO recycle_bin (kind, original_id, label, payload, deleted_by, deleted_by_id)
                          VALUES ('device', ?, ?, ?, ?, ?)''',
                       (device_id, label, json.dumps(dev, ensure_ascii=False, default=str),
                        who['name'], who['id']))
    conn.execute('DELETE FROM fingerprint_devices WHERE id = ?', (device_id,))
    log(conn, 'device.delete', 'device', device_id, label, note or '', who)
    conn.commit()
    return cur.lastrowid


def _insert_back(conn, table, data):
    cols = [c[1] for c in conn.execute(f'PRAGMA table_info({table})').fetchall()]
    keys = [k for k in data.keys() if k in cols]
    conn.execute(f"INSERT INTO {table} ({', '.join(keys)}) VALUES ({', '.join('?' * len(keys))})",
                 [data[k] for k in keys])


def restore(conn, bin_id, reupload=False, who=None):
    """يُرجع عنصرًا من السلّة بنفس رقمه. يُرجع (ok, رسالة).

    reupload: للموظّف الممسوح من الأجهزة — يُرفع عليها من جديد ببصماته المحفوظة.
    """
    ensure_schema(conn)
    item = conn.execute('SELECT * FROM recycle_bin WHERE id = ?', (bin_id,)).fetchone()
    if not item:
        return False, 'العنصر غير موجود في السلّة'
    if item['restored_at']:
        return False, 'اتسترجع قبل كده'
    who = who or actor()
    data = json.loads(item['payload'])
    if item['kind'] == 'employee':
        if conn.execute('SELECT 1 FROM employees WHERE id = ?', (data.get('id'),)).fetchone():
            return False, 'رقم الموظف في الجدول مستخدم حاليًا — مينفعش يرجع بنفس الرقم'
        other = conn.execute('SELECT name FROM employees WHERE employee_number = ?',
                             (data.get('employee_number'),)).fetchone()
        if other:
            return False, (f"الرقم الوظيفي {data.get('employee_number')} مستخدم حاليًا للموظف "
                           f"«{other['name']}» — غيّر رقمه الأول وبعدين استرجع")
        _insert_back(conn, 'employees', data)
        try:
            from utils import device_access
            device_access.cancel_pending_deletes(conn, data.get('employee_number'))
        except Exception:
            pass
        details = ''
        scope = json.loads(item['scope'] or '{}')
        if reupload and scope.get('mode') == 'devices' and scope.get('devices'):
            details = 'ورُفع على الأجهزة: ' + '، '.join(d['name'] for d in scope['devices'])
        conn.execute("UPDATE recycle_bin SET restored_at = CURRENT_TIMESTAMP, restored_by = ? WHERE id = ?",
                     (who['name'], bin_id))
        log(conn, 'employee.restore', 'employee', data.get('id'), item['label'], details, who)
        conn.commit()
        # ما بصمه على الأجهزة وهو في السلّة يدخل حضورَه.
        try:
            from utils import pending_punches
            pending_punches.adopt(conn, [data.get('employee_number')])
        except Exception:
            pass
        if details:
            reupload_employee(data.get('id'), [d['id'] for d in scope['devices']])
        return True, f"اتسترجع {item['label']}" + (f' — {details}' if details else '')
    if item['kind'] == 'device':
        if conn.execute('SELECT 1 FROM fingerprint_devices WHERE id = ?', (data.get('id'),)).fetchone():
            return False, 'رقم الجهاز في الجدول مستخدم حاليًا'
        if data.get('device_ip') and conn.execute('SELECT 1 FROM fingerprint_devices WHERE device_ip = ?',
                                                  (data.get('device_ip'),)).fetchone():
            return False, f"فيه جهاز تاني بنفس العنوان {data.get('device_ip')} — امسحه أو غيّر عنوانه الأول"
        _insert_back(conn, 'fingerprint_devices', data)
        conn.execute("UPDATE recycle_bin SET restored_at = CURRENT_TIMESTAMP, restored_by = ? WHERE id = ?",
                     (who['name'], bin_id))
        log(conn, 'device.restore', 'device', data.get('id'), item['label'], '', who)
        conn.commit()
        return True, f"اتسترجع {item['label']}"
    return False, 'نوع غير معروف'


def _is_legacy(d):
    from utils.devices import registry
    return registry.is_legacy(d)


def reupload_employee(emp_id, device_ids):
    """يرفع موظّفًا مُسترجَعًا ببصماته للأجهزة التي مُسح منها — في الخلفيّة."""
    import threading

    def run():
        from utils.db import get_db_connection
        from utils import device_access
        conn = get_db_connection()
        try:
            emp = conn.execute('SELECT * FROM employees WHERE id = ?', (emp_id,)).fetchone()
            if not emp:
                return
            for d in _devices(conn, device_ids):
                try:
                    if d['is_adms']:
                        from utils.fingerprint_utils import adms_template_commands
                        payload = {'PIN': str(emp['employee_number']), 'Name': emp['name'],
                                   'Pri': int(emp['privilege'] or 0), 'Passwd': str(emp['password'] or ''),
                                   'Card': int(emp['card_number'] or 0) if str(emp['card_number'] or '').isdigit() else 0,
                                   'Grp': str(emp['group_id'] or '1'), 'Enabled': 1}
                        conn.execute("INSERT INTO adms_commands (device_id, command_type, payload, status) "
                                     "VALUES (?, 'DATA UPDATE USERINFO', ?, 'PENDING')", (d['id'], json.dumps(payload)))
                        for ctype, cp in adms_template_commands(conn, emp['employee_number'], d):
                            conn.execute("INSERT INTO adms_commands (device_id, command_type, payload, status) "
                                         "VALUES (?, ?, ?, 'PENDING')", (d['id'], ctype, cp))
                        conn.commit()
                    elif _is_legacy(d):
                        device_access.push_direct(conn, d, [emp])
                    else:
                        from utils.devices import engine as _eng
                        _eng.push_employees(conn, d, [emp])
                except Exception as e:          # noqa: BLE001
                    logger.warning(f'reupload {emp_id} to {d["id"]}: {e}')
        finally:
            try:
                conn.close()
            except Exception:
                pass

    threading.Thread(target=run, daemon=True).start()


def bin_items(conn, show_restored=False, limit=500):
    ensure_schema(conn)
    # الأوقاتُ محفوظةٌ UTC — وتُعرض بتوقيت الجهاز (الكويت عند العملاء).
    sql = ("SELECT *, datetime(deleted_at, 'localtime') AS deleted_local, "
           "datetime(restored_at, 'localtime') AS restored_local FROM recycle_bin")
    if not show_restored:
        sql += " WHERE restored_at IS NULL"
    sql += " ORDER BY id DESC LIMIT ?"
    out = []
    for r in conn.execute(sql, (limit,)).fetchall():
        d = dict(r)
        try:
            d['scope_obj'] = json.loads(d.get('scope') or '{}')
        except Exception:
            d['scope_obj'] = {}
        d['kind_label'] = KIND_LABELS.get(d['kind'], d['kind'])
        out.append(d)
    return out


def audit_rows(conn, limit=300):
    ensure_schema(conn)
    out = []
    for r in conn.execute("SELECT *, datetime(created_at, 'localtime') AS created_local "
                            "FROM sensitive_audit_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall():
        d = dict(r)
        d['action_label'] = ACTION_LABELS.get(d['action'], d['action'])
        out.append(d)
    return out
