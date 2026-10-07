# -*- coding: utf-8 -*-
"""بصمةُ حضورٍ لرقمٍ ليس موظّفًا عندنا بعد — تُحفظ، وتدخل الحضورَ حين يُضاف.

## ما كان يحدث

موظّفٌ جديد يُسجَّل على الجهاز ويبصم قبل أن يُضاف للبرنامج:
- المزامنةُ المباشرة (K40) تتجاهل بصمتَه («موظف غير موجود»)، ثمّ تبدأ المرّةَ التالية
  من بعد آخر بصمةٍ حُفظت لغيره — فلا تعود إليها أبدًا؛
- وADMS يرمي السطر.
والمزامنةُ كلَّ ساعة تسحب البصماتِ **ثمّ** المستخدمين، فأوّلُ ساعةٍ من بصمات كلِّ موظّفٍ
جديد تضيع، ولو أُضيف بعدها بدقيقة.

## الآن

تُحفظ في `pending_punches`، و`adopt` يُدخلها الحضورَ متى صار الرقمُ موظّفًا — من
مزامنة المستخدمين، أو الإضافة اليدويّة، أو الاستيراد، أو الاسترجاع من سلّة المحذوفات.
وما يخصّ موظّفًا موقوفًا يُقيَّد «مُتجاهَلًا» كبقيّة بصماته بعد إيقافه. وتُنسى بعد
`KEEP_DAYS` (مستخدمو الجهاز الإداريّون لا يصيرون موظّفين أبدًا).
"""
import logging

logger = logging.getLogger(__name__)

KEEP_DAYS = 180


def ensure_schema(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS pending_punches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        device_id INTEGER,
        pin TEXT NOT NULL,
        check_time TEXT NOT NULL,
        check_type TEXT,
        verify_code TEXT,
        sensor_id INTEGER,
        work_code TEXT,
        source TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (pin, check_time, device_id)
    )''')
    conn.execute('CREATE INDEX IF NOT EXISTS ix_pending_punches_pin ON pending_punches (pin)')


def record(conn, device_id, pin, check_time, check_type=0, verify_code=0, sensor_id=1, work_code=0,
           source='device'):
    """بصمةٌ لرقمٍ غير معروف: تُحفظ بانتظار صاحبها."""
    pin = str(pin or '').strip()
    if not pin or not check_time:
        return 0
    ensure_schema(conn)
    cur = conn.execute('''INSERT OR IGNORE INTO pending_punches
        (device_id, pin, check_time, check_type, verify_code, sensor_id, work_code, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                       (device_id, pin, str(check_time)[:19], check_type, verify_code, sensor_id, work_code, source))
    return cur.rowcount


def adopt(conn=None, pins=None):
    """ما صار له صاحب يدخل الحضور. يُرجع عددَ ما دخل. لا يرمي."""
    owned = conn is None
    if owned:
        from utils.db import get_db_connection
        conn = get_db_connection()
    added = 0
    try:
        ensure_schema(conn)
        conn.execute(f"DELETE FROM pending_punches WHERE created_at < datetime('now', '-{int(KEEP_DAYS)} days')")
        sql = '''SELECT p.*, e.id AS employee_id FROM pending_punches p
                 JOIN employees e ON CAST(e.employee_number AS TEXT) = p.pin'''
        args = []
        if pins:
            pins = [str(p).strip() for p in pins if str(p).strip()]
            sql += f" WHERE p.pin IN ({','.join('?' * len(pins))})"
            args = pins
        rows = conn.execute(sql + ' ORDER BY p.check_time', args).fetchall()
        if not rows:
            conn.commit()
            return 0
        try:
            from utils import device_access as da
            da.ensure_schema(conn)
            blocked = da.blocked_map(conn)
        except Exception:
            da, blocked = None, {}
        for r in rows:
            if blocked and da.is_blocked_punch(blocked, r['pin'], r['check_time']):
                da.record_ignored(conn, r['employee_id'], r['device_id'], r['check_time'])
            elif not conn.execute('SELECT 1 FROM attendance_records WHERE employee_id = ? AND check_time = ?',
                                  (r['employee_id'], r['check_time'])).fetchone():
                conn.execute('''INSERT INTO attendance_records
                    (employee_id, device_id, check_time, check_type, verify_code, sensor_id, work_code)
                    VALUES (?, ?, ?, ?, ?, ?, ?)''',
                             (r['employee_id'], r['device_id'] or 0, r['check_time'], r['check_type'] or 0,
                              r['verify_code'] or 0, r['sensor_id'] or 1, r['work_code'] or 0))
                added += 1
            conn.execute('DELETE FROM pending_punches WHERE id = ?', (r['id'],))
        conn.commit()
        if added:
            logger.info(f'pending punches adopted: {added}')
    except Exception as e:                      # noqa: BLE001
        logger.warning(f'adopt pending punches: {e}')
    finally:
        if owned:
            try:
                conn.close()
            except Exception:
                pass
    return added


def waiting(conn):
    """[(الرقم، عدد البصمات، أوّلها، آخرها)] — للعرض."""
    ensure_schema(conn)
    return conn.execute('''SELECT pin, COUNT(*) n, MIN(check_time) first, MAX(check_time) last
                           FROM pending_punches GROUP BY pin ORDER BY last DESC''').fetchall()
