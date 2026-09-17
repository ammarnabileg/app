"""صندوق الإشعارات: ما يجب أن يعرفه أحدهم ولم يطلبه.

المدير لا يفتح «فريقي» كل ساعة ليرى إن كان أحدٌ طلب إجازة. فالطلب
ينتظر يومًا أو يومين لا لأنه مرفوض، بل لأن أحدًا لم يره. وحين
يُعتمد أو يُرفض، يبقى صاحبه يسأل.

وهذا الملف لا يرسل شيئًا إلى الهاتف. هو **السجلّ**: يُقيَّد فيه ما
حدث ولمن يعني، ويقرؤه من يفتح البوابة أو التطبيق. والدفع (Push) —
حين يُضاف — ناقلٌ لهذا السجلّ لا بديلٌ عنه: يقرأ منه ويوصله، فتبقى
الرسالة واحدة في المكانين ولا يُبنى مساران يفترقان.

**عن الهدف.** الإشعار يُوجَّه إلى **حساب** لا إلى موظف: من يقرأ هو
من سجّل دخوله. والموظف قد يكون له حساب أو لا يكون، وقد يكون له
أكثر من حساب. فـ`notify_employee` تحلّ الموظف إلى حساباته وتقيّد
لكلٍّ منها.

**ولا يُرمى من هنا شيء.** إنشاء الإشعار يقع بعد نجاح العملية
الأصلية — بعد حفظ الطلب، وبعد الاعتماد. فلو فشل القيد لسببٍ ما،
لا يجوز أن يُلغى الطلب الذي نجح. ولذا كل كتابةٍ هنا مغلَّفة،
والإخفاق يُبتلع ويُسجَّل في السجلّ لا في وجه المستخدم.
"""

import logging
from datetime import datetime

log = logging.getLogger(__name__)

# أنواع الإشعارات. القيمة تُخزَّن، فلا تُغيَّر بعد أن تُكتب في قاعدة
# عميل — الصفوف القديمة لا تُترجم نفسها.
KIND_LEAVE_REQUESTED = 'leave_requested'
KIND_LEAVE_DECIDED = 'leave_decided'
KIND_EXCUSE_REQUESTED = 'excuse_requested'

# سقفٌ لما يُعاد في الطلب الواحد: صندوقٌ بلا سقف يصير صفحةً بطيئة.
MAX_LIST = 50

SCHEMA = '''CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT,
    -- إلى أين يذهب من ضغط: اسم قسمٍ في الواجهة لا مسار خادم، فيُفهَم
    -- في البوابة والتطبيق معًا.
    target TEXT,
    ref_type TEXT,
    ref_id INTEGER,
    is_read INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
)'''

INDEXES = (
    'CREATE INDEX IF NOT EXISTS idx_notif_user ON notifications (user_id, is_read, id DESC)',
)


def init_schema(conn):
    conn.execute(SCHEMA)
    for sql in INDEXES:
        conn.execute(sql)
    conn.commit()


def _now():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


# ------------------------------------------------------------ القيد

def notify(conn, user_id, kind, title, body='', target=None,
           ref_type=None, ref_id=None):
    """يقيّد إشعارًا لحساب. يعيد رقم الصفّ أو None.

    لا يرمي: يُنادى بعد نجاح العملية الأصلية، وإخفاقه لا يجوز أن
    يُبطلها.
    """
    if not user_id:
        return None
    try:
        init_schema(conn)
        cur = conn.cursor()
        cur.execute(
            'INSERT INTO notifications (user_id, kind, title, body, target,'
            ' ref_type, ref_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (user_id, kind, title, body or '', target, ref_type, ref_id, _now()))
        conn.commit()
        return cur.lastrowid
    except Exception:
        log.exception('تعذّر قيد إشعار لحساب %s', user_id)
        return None


def users_of_employee(conn, employee_id):
    """حسابات هذا الموظف. قد تكون صفرًا أو أكثر من واحد."""
    if not employee_id:
        return []
    try:
        return [r[0] for r in conn.execute(
            'SELECT id FROM users WHERE employee_id = ? AND is_active = 1',
            (employee_id,))]
    except Exception:
        log.exception('تعذّر إيجاد حسابات الموظف %s', employee_id)
        return []


def notify_employee(conn, employee_id, kind, title, body='', target=None,
                    ref_type=None, ref_id=None, exclude_user_id=None):
    """يقيّد لكل حسابات الموظف. يعيد عدد ما قُيّد.

    `exclude_user_id` لمن فعل الفعل بنفسه: من اعتمد طلبًا لا يُخطَر
    بأنه اعتمده.
    """
    n = 0
    for uid in users_of_employee(conn, employee_id):
        if exclude_user_id and uid == exclude_user_id:
            continue
        if notify(conn, uid, kind, title, body, target, ref_type, ref_id):
            n += 1
    return n


# ---------------------------------------------------------- القراءة

def unread_count(conn, user_id):
    if not user_id:
        return 0
    try:
        init_schema(conn)
        row = conn.execute(
            'SELECT COUNT(*) FROM notifications WHERE user_id = ? AND is_read = 0',
            (user_id,)).fetchone()
        return row[0] if row else 0
    except Exception:
        return 0


def listing(conn, user_id, limit=MAX_LIST, unread_only=False):
    if not user_id:
        return []
    try:
        init_schema(conn)
        limit = max(1, min(int(limit or MAX_LIST), MAX_LIST))
        sql = ('SELECT id, kind, title, body, target, ref_type, ref_id,'
               ' is_read, created_at FROM notifications WHERE user_id = ?')
        if unread_only:
            sql += ' AND is_read = 0'
        sql += ' ORDER BY id DESC LIMIT ?'
        return [dict(r) for r in conn.execute(sql, (user_id, limit))]
    except Exception:
        log.exception('تعذّرت قراءة إشعارات %s', user_id)
        return []


def mark_read(conn, user_id, ids=None):
    """يعلّم المقروء. `ids=None` تعني الكلّ. يعيد عدد ما تغيّر.

    والشرط على `user_id` في كل حال: رقم إشعارٍ يأتي من طلب، ولولا
    الشرط لعلّم أحدُهم إشعارات غيره — وهو كشفٌ لوجودها لا أكثر، لكنه
    كشفٌ لا داعي له.
    """
    if not user_id:
        return 0
    try:
        init_schema(conn)
        cur = conn.cursor()
        if ids is None:
            cur.execute('UPDATE notifications SET is_read = 1'
                        ' WHERE user_id = ? AND is_read = 0', (user_id,))
        else:
            clean = []
            for i in list(ids)[:200]:
                try:
                    clean.append(int(i))
                except (TypeError, ValueError):
                    continue
            if not clean:
                return 0
            marks = ','.join('?' * len(clean))
            cur.execute(
                f'UPDATE notifications SET is_read = 1'
                f' WHERE user_id = ? AND id IN ({marks})', [user_id] + clean)
        conn.commit()
        return cur.rowcount or 0
    except Exception:
        log.exception('تعذّر تعليم إشعارات %s مقروءةً', user_id)
        return 0


# ------------------------------------------------- صياغات جاهزة

def leave_requested(conn, manager_employee_id, employee_name, leave_type,
                    start_date, end_date, days, request_id):
    """إلى المدير: طلبٌ ينتظرك."""
    return notify_employee(
        conn, manager_employee_id, KIND_LEAVE_REQUESTED,
        f'طلب إجازة من {employee_name}',
        f'{leave_type} · {days} يوم · من {start_date} إلى {end_date}',
        target='team', ref_type='leave_request', ref_id=request_id)


def leave_decided(conn, employee_id, approved, leave_type, start_date,
                  end_date, decided_by_user_id=None, request_id=None):
    """إلى الموظف: قُرِّر في طلبك."""
    return notify_employee(
        conn, employee_id, KIND_LEAVE_DECIDED,
        'اعتُمد طلب إجازتك' if approved else 'رُفض طلب إجازتك',
        f'{leave_type} · من {start_date} إلى {end_date}',
        target='home', ref_type='leave_request', ref_id=request_id,
        exclude_user_id=decided_by_user_id)


def excuse_requested(conn, manager_employee_id, employee_name, kind_label,
                     day):
    """إلى المدير: استئذانٌ سُجِّل."""
    return notify_employee(
        conn, manager_employee_id, KIND_EXCUSE_REQUESTED,
        f'{kind_label} — {employee_name}',
        f'بتاريخ {day}',
        target='team', ref_type='excuse', ref_id=None)
