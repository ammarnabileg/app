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

import hashlib
import logging
from datetime import datetime

log = logging.getLogger(__name__)

# أنواع الإشعارات. القيمة تُخزَّن، فلا تُغيَّر بعد أن تُكتب في قاعدة
# عميل — الصفوف القديمة لا تُترجم نفسها.
KIND_LEAVE_REQUESTED = 'leave_requested'
KIND_LEAVE_DECIDED = 'leave_decided'
KIND_EXCUSE_REQUESTED = 'excuse_requested'
KIND_PRESENCE_DUE = 'presence_due'
KIND_PRESENCE_MISSED = 'presence_missed'

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


# ------------------------------------------------- الدفع إلى واتساب

def _push(conn, event, key, data=None, to=None):
    """يقيّد الحدثَ في صندوق البوّابة — بلا نصٍّ وبلا رقم.

    ## لماذا هنا لا في `notify`

    `notify` تقيّد لكل **حساب**، و`notify_employee` تكرّرها لحسابات
    الموظّف. فمن له ثلاثةُ حسابات كان سيأخذ ثلاثَ رسائل واتساب عن
    حدثٍ واحد. والحدثُ واحد، فيُطلق مرّةً عند الصياغة حيث يُعرف
    الموظّفُ المقصود.

    ## وما لا يُرسل من هنا

    لا نصّ: الصياغةُ قالبٌ في اللوحة لأن الرسائل تخرج من رقمنا.
    ولا رقم: `to` فيه `employee_id` واللوحةُ تحلّه من بياناتها.
    انظر `utils/message_outbox.py`.

    ولا يرمي: تعثّرُ الدفع لا يجوز أن يمنع قيدَ الإشعار الذي نجح.
    """
    try:
        from utils import message_outbox as _mo
        return _mo.emit(conn, event, key, data=data, to=to)
    except Exception:
        log.exception('تعذّر دفع الحدث %s', event)
        return None


def _employee_name(conn, employee_id):
    """اسمُ الموظّف للقالب. فراغٌ إن لم يوجد — واللوحة تضع «—».

    وبالشرطة السفليّة عمدًا: `leave_requested` لها وسيطٌ بالاسم
    نفسه، واسمان متطابقان في ملفٍّ واحد يجعلان القارئَ يخمّن أيُّهما
    يُنادى.
    """
    if not employee_id:
        return ''
    try:
        row = conn.execute('SELECT name FROM employees WHERE id = ?',
                           (employee_id,)).fetchone()
        return (row[0] or '') if row else ''
    except Exception:
        return ''


# ------------------------------------------------- صياغات جاهزة

def leave_requested(conn, manager_employee_id, employee_name, leave_type,
                    start_date, end_date, days, request_id):
    """إلى المدير: طلبٌ ينتظرك."""
    n = notify_employee(
        conn, manager_employee_id, KIND_LEAVE_REQUESTED,
        f'طلب إجازة من {employee_name}',
        f'{leave_type} · {days} يوم · من {start_date} إلى {end_date}',
        target='team', ref_type='leave_request', ref_id=request_id)
    # إلى صاحب الحساب — لا إلى المدير بوصفه موظّفًا: سياسةُ اللوحة
    # هي من يقرّر الوجهة، والتركيبُ لا يسمّي مستقبِلًا.
    if request_id:
        _push(conn, 'leave_requested', f'lv{request_id}-req',
              data={'employee_name': employee_name, 'start': start_date,
                    'end': end_date, 'days': days})
    return n


def leave_decided(conn, employee_id, approved, leave_type, start_date,
                  end_date, decided_by_user_id=None, request_id=None):
    """إلى الموظف: قُرِّر في طلبك."""
    n = notify_employee(
        conn, employee_id, KIND_LEAVE_DECIDED,
        'اعتُمد طلب إجازتك' if approved else 'رُفض طلب إجازتك',
        f'{leave_type} · من {start_date} إلى {end_date}',
        target='home', ref_type='leave_request', ref_id=request_id,
        exclude_user_id=decided_by_user_id)
    # القرار يُبدَّل: يُعتمد ثم يُرفض. فالمفتاح يحمل القرارَ نفسه،
    # وإلّا ابتلع التفرُّدُ الرسالةَ الثانية وبقي الموظّف على أن
    # إجازته معتمَدة.
    if request_id and employee_id:
        _push(conn, 'leave_decided',
              f"lv{request_id}-{'ok' if approved else 'no'}",
              data={'employee_name': _employee_name(conn, employee_id),
                    'status': 'approved' if approved else 'rejected',
                    'start': start_date, 'days': ''},
              to={'employee_id': employee_id})
    return n


def excuse_requested(conn, manager_employee_id, employee_name, kind_label,
                     day, employee_id=None):
    """إلى المدير: استئذانٌ سُجِّل."""
    n = notify_employee(
        conn, manager_employee_id, KIND_EXCUSE_REQUESTED,
        f'{kind_label} — {employee_name}',
        f'بتاريخ {day}',
        target='team', ref_type='excuse', ref_id=None)
    # لا رقمَ للاستئذان في المخطَّط، فالمفتاح يُبنى من الموظّف واليوم
    # والنوع — وهي ثلاثةٌ تكفي لتمييزه، وتجعل إعادةَ تسجيل اليوم
    # نفسه رسالةً واحدة.
    #
    # و`md5` لا `hash()`: الثانيةُ مملوحةٌ لكل عمليّة (PYTHONHASHSEED)،
    # فالنوعُ نفسُه يعطي رقمًا مختلفًا بعد كل إعادة تشغيل — ومفتاحُ
    # تفرّدٍ يتبدّل ليس مفتاحَ تفرّد. و`md5` هنا اختصارٌ لا حمايةٌ.
    if employee_id:
        digest = hashlib.md5(str(kind_label).encode('utf-8')).hexdigest()[:6]
        _push(conn, 'excuse_requested', f'ex{employee_id}-{day}-{digest}',
              data={'employee_name': employee_name, 'date': day,
                    'reason': kind_label})
    return n


def _already_sent(conn, user_id, kind, day_key):
    """أقُيّد هذا النوع لهذا الحساب في هذا اليوم؟

    التذكير يُنادى من كل طلبٍ يفتحه الموظف. فبلا هذا الفحص يمتلئ
    الصندوق بعشرين نسخةً من رسالةٍ واحدة، ويصير الصندوق نفسه ضجيجًا
    يُتجاهَل — وهو أسوأ من لا تذكير.
    """
    try:
        init_schema(conn)
        row = conn.execute(
            'SELECT 1 FROM notifications WHERE user_id = ? AND kind = ?'
            ' AND ref_type = ? AND DATE(created_at) = ? LIMIT 1',
            (user_id, kind, 'presence_day', day_key)).fetchone()
        return row is not None
    except Exception:
        # الشكّ يمنع الإرسال: تكرارٌ فائت أهون من صندوقٍ يفيض.
        return True


def presence_reminder(conn, employee_id, state, start, end, now=None):
    """يذكّر ببصمة التواجد. يعيد عدد ما قُيّد.

    مرّةً واحدة لكل حالة في اليوم: تذكيرٌ داخل النافذة، وإخبارٌ بعد
    فواتها — والثاني ليس لومًا، بل ليطلب تصحيح بصمة قبل أن يُخصم.
    """
    from utils import presence as _p

    now = now or datetime.now()
    day_key = now.strftime('%Y-%m-%d')

    if state == _p.STATE_DUE:
        kind = KIND_PRESENCE_DUE
        title = 'بصمة التواجد مطلوبة الآن'
        body = f'نافذتها اليوم من {start} إلى {end}. اذهب إلى الجهاز أو سجّلها من التطبيق.'
    elif state == _p.STATE_MISSED:
        kind = KIND_PRESENCE_MISSED
        title = 'فاتتك بصمة التواجد اليوم'
        body = (f'نافذتها كانت من {start} إلى {end}، ولم تُسجَّل بصمة فيها. '
                'إن كان لديك عذر فقدّم «تصحيح بصمة» قبل إقفال الشهر.')
    else:
        return 0

    n = 0
    for uid in users_of_employee(conn, employee_id):
        if _already_sent(conn, uid, kind, day_key):
            continue
        if notify(conn, uid, kind, title, body, target='punch',
                  ref_type='presence_day', ref_id=None):
            n += 1

    # **خارج الحلقة.** المسحُ يُنادى من كلّ طلبٍ يفتحه أيُّ مستخدم،
    # فهذا أكثرُ حدثٍ إطلاقًا في النظام. والحلقةُ لحسابات الموظّف —
    # وقيدُه داخلها كان يُطلق حدثًا لكلّ حساب.
    #
    # ومفتاحُ اليوم والحال: تذكيرٌ واحد لكلّ موظّفٍ في اليوم مهما
    # نُودي. وهو العمودُ الفريد من يفرضه، لا هذا الشرط — الفحصُ قبل
    # الإدراج سباقٌ بين عمّال الخادم.
    _push(conn, 'presence_due' if state == _p.STATE_DUE else 'presence_missed',
          f'pr{employee_id}-{day_key}-{"d" if state == _p.STATE_DUE else "m"}',
          data={'employee_name': _employee_name(conn, employee_id),
                'due_at': f'{start} — {end}', 'date': day_key},
          to={'employee_id': employee_id})
    return n


# كل كم دقيقة يُسمح بمسحٍ شامل. المسح رخيص (استعلامان لكل موظف
# ذي نافذة) لكنه ليس مجانيًّا، ولا فائدة من تكراره كل طلب.
SWEEP_EVERY_MINUTES = 10
SWEEP_SETTING = 'presence_sweep_last_at'


def sweep_presence(conn, now=None, force=False):
    """يفحص **كل** من له نافذة تواجد ويذكّر من يستحقّ.

    السبب: لا مجدول في هذا النظام، فالفحص الفرديّ لا يقع إلا حين
    يفتح الموظف البوابة أو التطبيق — ومن لم يفتح شيئًا لا يُذكَّر،
    وهو بالضبط من يحتاج التذكير.

    والمسح يُنادى من أي طلبٍ يفتحه **أيّ** مستخدم: فمكتبٌ فيه
    عشرون موظفًا يفتحه أحدُهم كل بضع دقائق، فيصل التذكير إلى صندوق
    الجميع ولو لم يفتحوا هم. ولا يزال الأصحّ أن يُنادى من cron —
    `tools/presence_sweep.py` لذلك — لكن هذا يعمل بلا إعداد.

    والخانق مُخزَّن في القاعدة لا في الذاكرة: الخادم قد يعمل بعدّة
    عمّال، ولكلٍّ ذاكرته. يعيد عدد ما قُيّد.
    """
    now = now or datetime.now()

    if not force and not _sweep_due(conn, now):
        return 0

    try:
        rows = conn.execute("""
            SELECT e.id FROM employees e
            JOIN shift_types st ON e.shift_type = st.name
            WHERE e.is_active = 1
              AND st.presence_start_time IS NOT NULL
              AND st.presence_end_time IS NOT NULL
        """).fetchall()
    except Exception:
        log.exception('تعذّر حصر من لهم نافذة تواجد')
        return 0

    total = 0
    for r in rows:
        emp_id = r[0] if not hasattr(r, 'keys') else r['id']
        _st, sent = check_presence_for(conn, emp_id, now=now)
        total += sent
    return total


def _sweep_due(conn, now):
    """أمضت المهلة منذ آخر مسح؟ ويُسجَّل الآن قبل المسح لا بعده.

    التسجيل أولًا مقصود: لو مسح طويلٌ تعثّر في وسطه، لا يُعاد من
    أول طلبٍ يلي — فالفشل لا يصير حلقة.
    """
    try:
        from utils.db import get_setting, set_setting
        last = get_setting(SWEEP_SETTING, '')
        if last:
            try:
                prev = datetime.strptime(str(last)[:19], '%Y-%m-%d %H:%M:%S')
                if (now - prev).total_seconds() < SWEEP_EVERY_MINUTES * 60:
                    return False
            except ValueError:
                pass
        set_setting(SWEEP_SETTING, now.strftime('%Y-%m-%d %H:%M:%S'))
        return True
    except Exception:
        # تعذّر معرفة آخر مسح: لا يُمسح: مسحٌ فائت أهون من مسحٍ
        # يتكرّر مع كل طلب.
        return False


def check_presence_for(conn, employee_id, now=None):
    """يفحص ويذكّر إن لزم. يعيد (الحال، عدد ما قُيّد).

    لا يرمي: يُنادى من مسارات عادية يفتحها الموظف، وتعثّر التذكير
    لا يجوز أن يمنعه من رؤية بوابته.
    """
    from utils import presence as _p
    try:
        st = _p.status(conn, employee_id, now=now)
        sent = presence_reminder(conn, employee_id, st['state'],
                                 st['start'], st['end'], now=now)
        return st, sent
    except Exception:
        log.exception('تعذّر فحص بصمة التواجد للموظف %s', employee_id)
        return {'state': 'not_required', 'start': None, 'end': None,
                'punched_at': None}, 0
