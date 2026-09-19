"""دفترُ التغييرات المحلّي — ما الذي تغيّر منذ آخر رفع.

## لماذا دفتر، ولمَ لا يكفي الطابع الزمني

الوكيل القديم كان يسأل: «أعطني ما `created_at` له أحدثُ من آخر مرّة».
وهذا يفوته ثلاثة أشياء، وكلُّها يقع يوميًّا:

1. **التعديل.** موظّفٌ يُرفَع راتبه: `created_at` لا يتغيّر، فالسحابة
   تبقى على الراتب القديم إلى الأبد.
2. **الحذف.** صفٌّ يُحذف محليًّا: لا شيء يقول للسحابة أن تحذفه، فيبقى
   ظاهرًا للعميل بعد أن محاه.
3. **البصمة المتأخّرة.** جهازٌ كان مفصولًا يُفرِغ بصماتِ أمس، و
   `check_time` لها أقدم من العلامة المائيّة — فتُتخطَّى صامتةً ولا
   تصل السحابة أبدًا.

فالدفتر يسجّل **واقعة التغيير** لا زمنَ الصفّ: مُشغِّلات SQLite تكتب
سطرًا عند كل إدراجٍ وتحديثٍ وحذف. والترتيب ترتيبُ الوقوع (`seq`)، لا
ترتيبُ تواريخ العمل — فالبصمة المتأخّرة تأخذ دورها كأيّ تغييرٍ آخر.

## والحِمل الأوّل

تثبيتُ الدفتر على قاعدةٍ فيها مليون بصمةٍ سابقة لا يعني نسخ المليون
إلى الدفتر. الصفوف السابقة تُمشى مرّةً واحدة بمؤشّرٍ متدرّج
(`baseline`)، ثم يتولّى الدفتر وحده ما بعدها.

## وما لا يخرج من مقرّ العميل

`employees.password` و`privilege` حقلا الجهاز لا حقلا الموظف —
كلمةُ تسجيله على البصمة وصلاحيّته عليها. ولا شأن للعرض السحابي بهما،
فيُستبعدان. وجداول `users` و`license_settings` و`password_reset_tokens`
و`fingerprint_templates` خارج المزامنة أصلًا: الأولى فيها كلمات
المرور، والثانية مفتاح الترخيص، والأخيرة قوالبُ حيويّة لا داعي
لخروجها من المبنى.
"""
import logging

logger = logging.getLogger(__name__)


# الجداول التي تُرفع، وما يُحجب من أعمدتها.
# المفتاح اسم الجدول، والقيمة مجموعة الأعمدة التي لا تغادر المقرّ.
SYNC_TABLES = {
    'departments_master': set(),
    'positions_master': set(),
    'branches': set(),
    'shift_types': set(),
    'leave_types': set(),
    'official_holidays': set(),
    'employees': {'password', 'privilege'},
    'fingerprint_devices': set(),
    'attendance_records': set(),
    'leave_requests': set(),
    'salaries': set(),
}

# اسم الجدول الذي يحمل الدفتر، ومؤشّرات المشي الأوّل.
OUTBOX_TABLE = 'cloud_sync_outbox'
STATE_TABLE = 'cloud_sync_state'


def _table_exists(conn, name):
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _columns(conn, table):
    return [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]


def install(conn):
    """يُنشئ الدفتر ومُشغِّلاته. يُستدعى في كل إقلاع، ولا يضرّ تَكرارُه.

    ويُعاد استدعاؤه بعد الاستعادة لأنّ `DROP TABLE` يُسقط مُشغِّلات
    الجدول معه — فلو لم يُعَد التثبيت لصمت الدفتر عن كل تغييرٍ بعدها
    ولا شيء يُنبّه.
    """
    conn.execute(f'''CREATE TABLE IF NOT EXISTS {OUTBOX_TABLE} (
        seq        INTEGER PRIMARY KEY AUTOINCREMENT,
        table_name TEXT NOT NULL,
        row_id     INTEGER NOT NULL,
        op         TEXT NOT NULL,
        queued_at  TEXT NOT NULL DEFAULT (datetime('now'))
    )''')
    conn.execute(f'''CREATE TABLE IF NOT EXISTS {STATE_TABLE} (
        k TEXT PRIMARY KEY,
        v TEXT
    )''')

    installed = []
    for table in SYNC_TABLES:
        # جدولٌ غائب عن هذه النسخة ليس خطأً: المخطَّط ينمو بين الإصدارات،
        # ومُشغِّلٌ على جدولٍ غير موجود يُفشل الإقلاع كلَّه.
        if not _table_exists(conn, table):
            continue
        if 'id' not in _columns(conn, table):
            # لا مفتاح صفٍّ نشير به — فلا سبيل إلى مزامنةٍ سطريّة.
            logger.warning(f'cloud outbox: {table} has no id column — skipped')
            continue
        for op, when, ref in (('upsert', 'INSERT', 'NEW'),
                              ('upsert', 'UPDATE', 'NEW'),
                              ('delete', 'DELETE', 'OLD')):
            name = f'cloud_out_{table}_{when.lower()}'
            conn.execute(f'''CREATE TRIGGER IF NOT EXISTS "{name}"
                AFTER {when} ON "{table}"
                BEGIN
                    INSERT INTO {OUTBOX_TABLE} (table_name, row_id, op)
                    VALUES ('{table}', {ref}.id, '{op}');
                END''')
        installed.append(table)

    conn.commit()
    return installed


def uninstall_triggers(conn):
    """يُزيل المُشغِّلات — للتعطيل الكامل، ولاختبارٍ يقيس أثرها."""
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger' "
        "AND name LIKE 'cloud_out_%'").fetchall()
    for r in rows:
        conn.execute(f'DROP TRIGGER IF EXISTS "{r[0]}"')
    conn.commit()
    return len(rows)


# ------------------------------------------------------------ الحالة

def get_state(conn, key, default=None):
    if not _table_exists(conn, STATE_TABLE):
        return default
    row = conn.execute(f'SELECT v FROM {STATE_TABLE} WHERE k = ?', (key,)).fetchone()
    return row[0] if row else default


def set_state(conn, key, value):
    conn.execute(
        f'INSERT INTO {STATE_TABLE} (k, v) VALUES (?, ?) '
        f'ON CONFLICT(k) DO UPDATE SET v = excluded.v', (key, str(value)))
    conn.commit()


def reset_baseline(conn):
    """يُعيد المشي الأوّل من أوّله — بعد استعادةِ نسخةٍ احتياطية.

    الاستعادة تستبدل محتوى الجداول كلَّه، فما في السحابة صار يخصّ
    قاعدةً أخرى. ولا يكفي متابعةُ الدفتر من حيث وقف: الصفوف المستعادة
    لم تمرّ بمُشغِّل، فلا أثر لها فيه.
    """
    if not _table_exists(conn, STATE_TABLE):
        return
    conn.execute(f"DELETE FROM {STATE_TABLE} WHERE k LIKE 'baseline:%'")
    conn.commit()


# ------------------------------------------------------------ القراءة

def pending_count(conn):
    if not _table_exists(conn, OUTBOX_TABLE):
        return 0
    return conn.execute(f'SELECT COUNT(*) FROM {OUTBOX_TABLE}').fetchone()[0]


def peek(conn, limit=200):
    """أقدمُ التغييرات المعلّقة، مطويّةً: صفٌّ تغيّر عشرًا يُرسَل مرّة.

    الطيُّ على آخر واقعةٍ للصفّ لا أوّلها: من أُدرج ثم حُذف يُرسَل
    حذفًا، ومن حُذف ثم أُعيد إدراجه يُرسَل صفًّا.
    """
    if not _table_exists(conn, OUTBOX_TABLE):
        return []
    rows = conn.execute(
        f'SELECT seq, table_name, row_id, op FROM {OUTBOX_TABLE} '
        f'ORDER BY seq ASC LIMIT ?', (limit,)).fetchall()

    latest = {}
    for seq, table, row_id, op in rows:
        latest[(table, row_id)] = (seq, table, row_id, op)
    folded = sorted(latest.values(), key=lambda r: r[0])
    return [{'seq': s, 'table': t, 'row_id': i, 'op': o} for s, t, i, o in folded]


def high_water(conn, limit=200):
    """أعلى `seq` ضمن الدفعة — ما يُمحى بعد أن يؤكّد الخادم استلامه."""
    if not _table_exists(conn, OUTBOX_TABLE):
        return 0
    row = conn.execute(
        f'SELECT MAX(seq) FROM (SELECT seq FROM {OUTBOX_TABLE} '
        f'ORDER BY seq ASC LIMIT ?)', (limit,)).fetchone()
    return row[0] or 0


def ack(conn, up_to_seq):
    """يمحو ما أكّد الخادمُ استلامه — ولا شيء بعده.

    المحو بعد التأكيد لا قبله: انقطاعُ الشبكة في منتصف الرفع يترك
    الدفعة في الدفتر فتُعاد، والإعادة لا تضرّ لأن الطرف الآخر
    يكتب بالمفتاح (`upsert`) لا بالإلحاق.
    """
    if not _table_exists(conn, OUTBOX_TABLE) or not up_to_seq:
        return 0
    cur = conn.execute(f'DELETE FROM {OUTBOX_TABLE} WHERE seq <= ?', (up_to_seq,))
    conn.commit()
    return cur.rowcount


# ------------------------------------------------------------ المشي الأوّل

def baseline_batch(conn, table, size=200):
    """دفعةٌ من الصفوف السابقة لتثبيت الدفتر — أو [] إن انتهى الجدول."""
    if table not in SYNC_TABLES or not _table_exists(conn, table):
        return []
    cursor = int(get_state(conn, f'baseline:{table}', 0) or 0)
    rows = conn.execute(
        f'SELECT id FROM "{table}" WHERE id > ? ORDER BY id ASC LIMIT ?',
        (cursor, size)).fetchall()
    return [r[0] for r in rows]


def baseline_advance(conn, table, last_id):
    set_state(conn, f'baseline:{table}', last_id)


def baseline_done(conn):
    """هل انتهى المشي الأوّل على كل الجداول؟"""
    for table in SYNC_TABLES:
        if not _table_exists(conn, table):
            continue
        if baseline_batch(conn, table, size=1):
            return False
    return True


# ------------------------------------------------------------ الحمولة

def read_rows(conn, table, row_ids):
    """صفوفٌ بأعمدتها المسموحة — وما حُجب لا يظهر في الحمولة أصلًا."""
    if not row_ids or table not in SYNC_TABLES or not _table_exists(conn, table):
        return []
    blocked = SYNC_TABLES[table]
    cols = [c for c in _columns(conn, table) if c not in blocked]
    if not cols:
        return []
    col_sql = ', '.join(f'"{c}"' for c in cols)
    marks = ', '.join('?' for _ in row_ids)
    conn.row_factory = None
    rows = conn.execute(
        f'SELECT {col_sql} FROM "{table}" WHERE id IN ({marks})',
        list(row_ids)).fetchall()
    return [dict(zip(cols, r)) for r in rows]
