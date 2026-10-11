# -*- coding: utf-8 -*-
"""تصحيح وقت بصماتٍ اتسجّلت بساعة جهازٍ غلط.

جهازٌ ساعتُه كانت متقدّمة ساعة (فيرموير SpeedFace 1.1.9 مع منطقة UTC+3) سجّل كلَّ
بصماته بساعة زيادة. الأداة دي بتزيح بصمات أجهزة مختارة في فترة محدّدة بعدد دقائق
(−60 افتراضيًّا)، وكلّ تصحيح بيتسجّل عشان يترجع لو اتعمل غلط.

- البصمات اللي جت من الأجهزة بس، مش اللي المسؤول دخّلها بإيده.
- الفترة اللي كشف رواتبها اتقفل مابتتلمسش (الراتب اتصرف).
- بصمة وقتها الجديد موجود أصلًا لنفس الموظف بتتساب (مايبقاش فيه تكرار).
"""
from datetime import datetime, timedelta

DEVICE_SOURCES = ('device', 'adms', '')
FMT = '%Y-%m-%d %H:%M:%S'


def ensure_schema(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS punch_shift_batches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        device_ids TEXT NOT NULL,
        date_from TEXT NOT NULL,
        date_to TEXT NOT NULL,
        minutes INTEGER NOT NULL,
        moved INTEGER DEFAULT 0,
        created_by INTEGER,
        created_at TEXT DEFAULT (datetime('now', 'localtime')),
        undone_at TEXT
    )''')
    conn.execute('''CREATE TABLE IF NOT EXISTS punch_shift_items (
        batch_id INTEGER NOT NULL,
        record_id INTEGER NOT NULL,
        old_time TEXT NOT NULL,
        new_time TEXT NOT NULL,
        PRIMARY KEY (batch_id, record_id)
    )''')


def _norm(ts):
    s = str(ts or '').strip().replace('T', ' ')
    if len(s) == 10:
        s += ' 00:00:00'
    elif len(s) == 16:
        s += ':00'
    return datetime.strptime(s[:19], FMT)


def _plan(conn, device_ids, date_from, date_to, minutes):
    """(قائمة التعديل [(id, emp, old, new)], ممنوعة لقفل الفترة, متكرّرة)."""
    ids = [int(x) for x in device_ids]
    if not ids:
        return [], 0, 0
    start, end = _norm(date_from), _norm(date_to)
    if len(str(date_to).strip()) == 10:
        end = end + timedelta(days=1) - timedelta(seconds=1)
    rows = conn.execute(f'''SELECT id, employee_id, check_time FROM attendance_records
                            WHERE device_id IN ({','.join('?' * len(ids))})
                              AND check_time >= ? AND check_time <= ?
                              AND COALESCE(source, 'device') IN ({','.join('?' * len(DEVICE_SOURCES))})
                            ORDER BY check_time''',
                        ids + [start.strftime(FMT), end.strftime(FMT)] + list(DEVICE_SOURCES)).fetchall()
    try:
        from utils.payroll_engine import date_period_locked
    except Exception:
        date_period_locked = None
    locked_days = {}
    plan, locked, dup = [], 0, 0
    moving = {r['id'] for r in rows}
    taken = set()
    for r in rows:
        old = _norm(r['check_time'])
        new = old + timedelta(minutes=int(minutes))
        for day in {old.date(), new.date()}:
            if day not in locked_days:
                try:
                    locked_days[day] = bool(date_period_locked and date_period_locked(conn, day))
                except Exception:
                    locked_days[day] = False
        if locked_days[old.date()] or locked_days[new.date()]:
            locked += 1
            continue
        new_s = new.strftime(FMT)
        clash = conn.execute('SELECT id FROM attendance_records WHERE employee_id = ? AND check_time = ?',
                             (r['employee_id'], new_s)).fetchall()
        if any(c['id'] not in moving for c in clash) or (r['employee_id'], new_s) in taken:
            dup += 1
            continue
        taken.add((r['employee_id'], new_s))
        plan.append((r['id'], r['employee_id'], old.strftime(FMT), new_s))
    return plan, locked, dup


def preview(conn, device_ids, date_from, date_to, minutes):
    plan, locked, dup = _plan(conn, device_ids, date_from, date_to, minutes)
    sample = []
    if plan:
        names = {}
        for pid, emp, old, new in plan[:15]:
            if emp not in names:
                r = conn.execute('SELECT name, employee_number FROM employees WHERE id = ?', (emp,)).fetchone()
                names[emp] = f"{r['employee_number']} — {r['name']}" if r else str(emp)
            sample.append({'employee': names[emp], 'old': old, 'new': new})
    return {'count': len(plan), 'locked': locked, 'duplicates': dup, 'sample': sample,
            'employees': len({p[1] for p in plan})}


def apply(conn, device_ids, date_from, date_to, minutes, user_id=None):
    ensure_schema(conn)
    plan, locked, dup = _plan(conn, device_ids, date_from, date_to, minutes)
    cur = conn.execute('INSERT INTO punch_shift_batches (device_ids, date_from, date_to, minutes, moved, created_by) '
                       'VALUES (?, ?, ?, ?, ?, ?)',
                       (','.join(str(int(x)) for x in device_ids), str(date_from), str(date_to), int(minutes),
                        len(plan), user_id))
    batch = cur.lastrowid
    for pid, _emp, old, new in plan:
        conn.execute('INSERT INTO punch_shift_items (batch_id, record_id, old_time, new_time) VALUES (?, ?, ?, ?)',
                     (batch, pid, old, new))
        conn.execute('UPDATE attendance_records SET check_time = ? WHERE id = ?', (new, pid))
    conn.commit()
    return {'batch': batch, 'moved': len(plan), 'locked': locked, 'duplicates': dup}


def undo(conn, batch_id):
    ensure_schema(conn)
    b = conn.execute('SELECT * FROM punch_shift_batches WHERE id = ?', (int(batch_id),)).fetchone()
    if not b or b['undone_at']:
        return 0
    n = 0
    for it in conn.execute('SELECT record_id, old_time, new_time FROM punch_shift_items WHERE batch_id = ?',
                           (int(batch_id),)).fetchall():
        cur = conn.execute('UPDATE attendance_records SET check_time = ? WHERE id = ? AND check_time = ?',
                           (it['old_time'], it['record_id'], it['new_time']))
        n += cur.rowcount
    conn.execute("UPDATE punch_shift_batches SET undone_at = datetime('now', 'localtime') WHERE id = ?",
                 (int(batch_id),))
    conn.commit()
    return n


def history(conn, limit=20):
    ensure_schema(conn)
    return conn.execute('SELECT * FROM punch_shift_batches ORDER BY id DESC LIMIT ?', (limit,)).fetchall()
