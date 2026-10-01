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
    # كشفُ الرواتب كما حسبه المحرّك — لا حسابٌ ثانٍ له في السحابة.
    #
    # `*_by` أرقامُ مستخدمين، وجدولُ `users` لا يُرفع أصلًا (فيه
    # كلماتُ المرور)، فالأرقامُ هناك بلا معنى. و`unlock_reason`
    # ملاحظةٌ إداريّةٌ داخليّة لا شأن للعرض بها.
    'payroll_runs': {'created_by', 'approved_by', 'unlocked_by',
                     'unlock_reason'},
    'payroll_run_lines': set(),
    # الكشفُ يومًا بيوم — ما عدّه المحرّك، بلا مال (انظر db.py).
    'payroll_run_days': set(),
    # تاريخُ الشفتات: به تعرف البوّابةُ أنّ بصمةَ السادسة صباحًا خروجُ
    # ليلةِ أمس لا دخولُ اليوم. `created_by` رقمُ مستخدمٍ لا يُرفع جدولُه.
    'employee_shift_history': {'created_by'},
    # سجلُّ مزامنة أجهزة البصمة (٢.٢٩): يرى صاحبُ الشركة من البوّابة متى
    # سُحبت البصماتُ آخرَ مرّة وكم — بلا أن يصل الجهاز.
    'device_sync_log': set(),
}

# جداولُ أُضيفت إلى الرفع بعد أن صار للعملاء خوادمُ لا تعرفها. خادمٌ
# قديم يتخطّاها بلا خطأ فيُقَرّ الدفتر ولا يصل شيء — فتُعاد مرّةً واحدة
# حين يُعلن الخادمُ قبولها (انظر `resend_when_accepted`).
LATE_TABLES = {
    'employee_shift_history': 'resend:employee_shift_history:1',
    'device_sync_log': 'resend:device_sync_log:1',
}

# ------------------------------------------------------------ الترشيح
#
# جداولُ لا يخرج منها كلُّ صفّ.
#
# **والمسودّةُ لا تغادر المقرّ — إلّا إلى خادمٍ يعرض أنّها مسودّة.**
# كشفٌ لم يُعتمد بعدُ شغلٌ جارٍ: أرقامُه تتبدّل، وقد يُلغى. وعرضُه
# للعميل على أنه راتبُ شهره يجعله يبني عليه ثم يجده تغيّر — وذلك أسوأ
# من ألّا يراه. وخادمُ v103 وما قبله يعرض كلَّ كشفٍ يصله معتمَدًا.
#
# ومنذ ٢.٢٩ يُعلن الخادمُ الجديد (`features` في ردّ الأوامر) أنّه يعرض
# المسودّةَ مسودّةً ويعتمدها صاحبُ الشركة من البوّابة. حينها تُسجَّل
# `server:payroll_drafts` في حالة الدفتر، والترشيحُ يقرؤها هو نفسُه —
# فلا تخرج مسودّةٌ إلى خادمٍ قديم ولو لم يمرّ بها شيءٌ غيرُ هذا السطر.
#
# والصفُّ الذي يسقط من الترشيح **يُرسَل حذفًا**: `build_batch` تعامل
# ما لا يعود مقروءًا معاملةَ المحذوف. فكشفٌ اعتُمد ثم فُكّ قفلُه
# يختفي من السحابة بدل أن يبقى معتمَدًا فيها وحدها (أو يعود مسودّةً
# عند خادمٍ يعرض المسودّات).
DRAFTS_STATE = 'server:payroll_drafts'
_RUN_VISIBLE = ("status = 'approved' OR (status = 'saved' AND EXISTS ("
                "SELECT 1 FROM cloud_sync_state WHERE k = '" + DRAFTS_STATE + "' AND v = '1'))")
SYNC_FILTERS = {
    'payroll_runs': _RUN_VISIBLE,
    'payroll_run_lines':
        f"run_id IN (SELECT id FROM payroll_runs WHERE {_RUN_VISIBLE})",
    'payroll_run_days':
        f"run_id IN (SELECT id FROM payroll_runs WHERE {_RUN_VISIBLE})",
}

# ------------------------------------------------------------ التتابع
#
# ترشيحٌ يتبع صفًّا **آخر** لا يكفيه مشغّلُ صفّه: اعتمادُ الكشف يغيّر
# `payroll_runs` وحده، والسطورُ لم تتغيّر فلا يسجّلها مشغّلُها. فخرجت
# حذفًا وهي مسودّة، ولا شيء يُخرجها بعد الاعتماد.
#
# وكان المشيُ الأوّل يُخفي ذلك مصادفةً: مؤشّرُه لا يتقدّم إلّا على ما
# أرسل، فيلتقط الكشفَ المعتمَد لاحقًا ما دامت صفوفُه أحدث. حتى يُعتمد
# كشفٌ بعد كشفٍ أحدث منه — فتقع صفوفُه تحت المؤشّر وتبقى في المقرّ.
#
# فتغيُّرُ **الحالة** يُعيد تسجيلَ الأبناء في الدفتر، والترشيحُ يقرّر:
# معتمَدٌ فيُرسل، أو فُكّ قفلُه فيُحذف. والحالةُ وحدها — لمسةُ حقلٍ آخر
# في الكشف لا تُخرج ثلاثين صفًّا لكلّ موظّف.
#
# {الأب: (العمود, ((الابن, عمودُ الربط), ...))}
SYNC_CASCADES = {
    'payroll_runs': ('status', (('payroll_run_lines', 'run_id'),
                                ('payroll_run_days', 'run_id'))),
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

    for parent, (col, children) in SYNC_CASCADES.items():
        if not _table_exists(conn, parent):
            continue
        for child, fk in children:
            if not _table_exists(conn, child):
                continue
            conn.execute(f'''CREATE TRIGGER IF NOT EXISTS "cloud_out_{child}_via_{parent}"
                AFTER UPDATE OF "{col}" ON "{parent}"
                WHEN OLD."{col}" IS NOT NEW."{col}"
                BEGIN
                    INSERT INTO {OUTBOX_TABLE} (table_name, row_id, op)
                    SELECT '{child}', id, 'upsert' FROM "{child}"
                    WHERE "{fk}" = NEW.id;
                END''')

    conn.commit()
    return installed


def resend_when_accepted(conn, accepts):
    """إعادةُ إرسال الكشوف المعتمَدة وأبنائها — مرّةً واحدة، **حين يقبلها
    الخادم**. تُرجع True إن سُجّلت الإعادة الآن.

    ## لماذا

    عيبان قبل ٢.١٤ حبسا الكشوفَ في المقرّ ودفترُه يظنّها وصلت:

    - **الخادم (v86) كان يتخطّى جداولَ الرواتب بلا خطأ** — ليست في
      قائمته. فالرفعُ يُجاب بنجاح ويُقَرّ الدفتر، ولا شيء وصل.
    - **وبلا تتابع** لم يكن اعتمادُ كشفٍ يُخرج سطورَه (انظر
      `SYNC_CASCADES`).

    ## ولماذا «حين يقبلها» لا عند الإقلاع

    لو أُعيدت عند أوّل إقلاعٍ بـ٢.١٤ وخادمُ اللوحة ما زال v86 لرُميت
    ثانيةً — وضاعت الفرصةُ الوحيدة. وترتيبُ الترقيتين ليس بيدنا:
    العميلُ يُحدّث متى شاء. فالخادمُ الجديد يُعلن في ردّه ما يقبل
    (`accepts`)، والإعادةُ تنتظر أن ترى جداولَها فيه. والقديمُ لا يُعلن
    شيئًا، فتنتظر.
    """
    key = 'payroll_resend:1'
    if get_state(conn, key) is not None:
        return False
    if not isinstance(accepts, (list, tuple)):
        return False
    wanted = []
    for parent, (_col, children) in SYNC_CASCADES.items():
        wanted.append(parent)
        wanted.extend(child for child, _fk in children)
    if not all(t in accepts for t in wanted):
        return False
    for table in wanted:
        if not _table_exists(conn, table):
            continue
        where = SYNC_FILTERS.get(table)
        extra = f' WHERE ({where})' if where else ''
        conn.execute(f'''INSERT INTO {OUTBOX_TABLE} (table_name, row_id, op)
            SELECT '{table}', id, 'upsert' FROM "{table}"{extra}''')
    set_state(conn, key, 'done')
    return True


def enable_drafts(conn):
    """الخادمُ أعلن أنّه يعرض المسودّات: تُفتح، وتُرسَل المسودّاتُ القائمة
    وأبناؤها مرّةً واحدة — لم يمرّ بها مُشغِّلٌ منذ رُشّحت. تُرجع True إن فُتحت الآن."""
    if get_state(conn, DRAFTS_STATE) == '1':
        return False
    set_state(conn, DRAFTS_STATE, '1')
    if _table_exists(conn, 'payroll_runs'):
        conn.execute(f"""INSERT INTO {OUTBOX_TABLE} (table_name, row_id, op)
            SELECT 'payroll_runs', id, 'upsert' FROM payroll_runs WHERE status = 'saved'""")
        for child in ('payroll_run_lines', 'payroll_run_days'):
            if _table_exists(conn, child):
                conn.execute(f"""INSERT INTO {OUTBOX_TABLE} (table_name, row_id, op)
                    SELECT '{child}', id, 'upsert' FROM "{child}"
                    WHERE run_id IN (SELECT id FROM payroll_runs WHERE status = 'saved')""")
    conn.commit()
    return True


def resend_late_tables(conn, accepts):
    """إعادةُ `LATE_TABLES` كاملةً مرّةً واحدة حين يقبلها الخادم. تُرجع ما أُعيد."""
    if not isinstance(accepts, (list, tuple)):
        return []
    done = []
    for table, key in LATE_TABLES.items():
        if table not in accepts or get_state(conn, key) is not None:
            continue
        if _table_exists(conn, table):
            conn.execute(f'''INSERT INTO {OUTBOX_TABLE} (table_name, row_id, op)
                SELECT '{table}', id, 'upsert' FROM "{table}"''')
        set_state(conn, key, 'done')
        done.append(table)
    return done


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
    # المشيُ الأوّل يُرشَّح كما يُرشَّح الدفتر — وإلّا خرجت المسودّات
    # القديمة كلُّها في أوّل رفعٍ ولم يمنعها شيء.
    where = SYNC_FILTERS.get(table)
    extra = f' AND ({where})' if where else ''
    rows = conn.execute(
        f'SELECT id FROM "{table}" WHERE id > ?{extra} ORDER BY id ASC LIMIT ?',
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
    # صفوفٌ خامٌ على **هذا المؤشّر** وحده: كان `conn.row_factory = None`
    # يُغيّر اتصالَ المُنادي إلى الأبد، فما يقرأ بعده `row['col']` من
    # الاتصال نفسه (زرّ «ارفع الآن» يمرّر اتصالَ الطلب) يسقط بـTypeError.
    cur = conn.cursor()
    cur.row_factory = None
    # الصفُّ الذي يسقط من الترشيح لا يُقرأ، و`build_batch` ترسله
    # حذفًا — انظر شرح `SYNC_FILTERS`.
    where = SYNC_FILTERS.get(table)
    extra = f' AND ({where})' if where else ''
    rows = cur.execute(
        f'SELECT {col_sql} FROM "{table}" WHERE id IN ({marks}){extra}',
        list(row_ids)).fetchall()
    return [dict(zip(cols, r)) for r in rows]
