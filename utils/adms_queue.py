# -*- coding: utf-8 -*-
"""طابور أوامر ADMS من غير تكرار ومن غير ضغط على الأجهزة.

## اللي كان بيحصل

- كل ساعة، كل جهاز ADMS كان بيتطلب منه **كل الموظفين وكل البصمات** (`DATA QUERY USERINFO`
  و`DATA QUERY FINGERTMP` من غير PIN)، ومعاهم **أمرين لكل موظف نشط** (٣٠٠٠ موظف = ٦٠٠٠ أمر في
  الساعة لكل جهاز). مع إن جهاز ADMS بيبعت الموظف وبصماته لوحده أول ما يتسجّل عليه.
- والأوامر الخلصانة كانت بتتمسح لو حالتها «OK» أو «FAILED» بالظبط بس، فـ«SKIPPED» و«FAILED (Code…)»
  و«FAILED (no reply)» فضلت تتراكم.

## دلوقتي

- `queue_every`: أمر سحب بيتبعت **مرة في اليوم بالكتير** لكل جهاز، احتياطي لو حاجة فاتت.
- `cleanup`: كل أمر مش معلّق وعدّى عليه أسبوع بيتمسح، والتنظيف بيحصل مرة كل ١٠ دقايق مش مع كل طلب.
"""
import time
from datetime import datetime, timedelta

QUERY_EVERY_HOURS = 24
KEEP_DAYS = 7
CLEANUP_EVERY_SECONDS = 600
_last_cleanup = [0.0]


def ensure_schema(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS adms_query_log (
        device_id INTEGER NOT NULL,
        command_type TEXT NOT NULL,
        payload TEXT NOT NULL,
        last_at TEXT NOT NULL,
        PRIMARY KEY (device_id, command_type, payload)
    )''')


def queue_every(conn, device_id, command_type, payload='{}', hours=QUERY_EVERY_HOURS, now=None):
    """يحط الأمر في طابور الجهاز لو ماتبعتش من `hours` ساعة ومفيش زيه مستني. يُرجع True لو اتحط."""
    ensure_schema(conn)
    now = now or datetime.now()
    row = conn.execute('SELECT last_at FROM adms_query_log WHERE device_id = ? AND command_type = ? AND payload = ?',
                       (device_id, command_type, payload)).fetchone()
    if row:
        try:
            if datetime.strptime(str(row[0])[:19], '%Y-%m-%d %H:%M:%S') > now - timedelta(hours=hours):
                return False
        except ValueError:
            pass
    if conn.execute("SELECT 1 FROM adms_commands WHERE device_id = ? AND command_type = ? AND payload = ? "
                    "AND status = 'PENDING'", (device_id, command_type, payload)).fetchone():
        return False
    conn.execute("INSERT INTO adms_commands (device_id, command_type, payload, status) VALUES (?, ?, ?, 'PENDING')",
                 (device_id, command_type, payload))
    conn.execute('INSERT OR REPLACE INTO adms_query_log (device_id, command_type, payload, last_at) VALUES (?, ?, ?, ?)',
                 (device_id, command_type, payload, now.strftime('%Y-%m-%d %H:%M:%S')))
    return True


def queue_device_refresh(conn, device):
    """سحب احتياطي يومي لجهاز ADMS: الموظفين، وبصماتهم بالصيغة القديمة لو الجهاز مابيبعتش BIODATA.
    (بصمات الأجهزة الحديثة بتتسحب يوميًا من device_autosync.run_templates.)"""
    from utils import biometric_templates as bio
    n = int(queue_every(conn, device['id'], 'DATA QUERY USERINFO'))
    if not bio.device_versions(device):
        n += int(queue_every(conn, device['id'], 'DATA QUERY FINGERTMP'))
    return n


def cleanup(conn, force=False):
    """الأوامر الخلصانة (أي حالة غير PENDING) اللي عدّى عليها أسبوع تتمسح — مرة كل ١٠ دقايق."""
    now = time.time()
    if not force and now - _last_cleanup[0] < CLEANUP_EVERY_SECONDS:
        return 0
    _last_cleanup[0] = now
    cur = conn.execute(f"DELETE FROM adms_commands WHERE status != 'PENDING' "
                       f"AND created_at < datetime('now', '-{KEEP_DAYS} days')")
    return cur.rowcount
