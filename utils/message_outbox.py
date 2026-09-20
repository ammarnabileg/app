"""صندوقُ الأحداث الصادرة — ما يستحقّ أن يصل إلى واتساب.

## الفرق بينه وبين صندوق الإشعارات

`utils/notifications.py` سجلٌّ **يُقرأ**: يفتح الموظّف البوّابة فيرى
ما جدّ. وهو لا يصل من لم يفتح شيئًا — وهو بالضبط من يحتاج أن يصله.
فهذا الصندوق للنوع الثاني: ما يُدفَع إلى الهاتف.

وهو دفترٌ لا قناة. لا يكلّم واتساب، ولا يعرف رقمًا واحدًا. يقيّد
**وقوعَ حدث**، ويرفعه إلى بوّابة اللوحة، واللوحةُ تقرّر: أيُرسَل؟
ولمن؟ وبأيّ قناة؟ وضمن أيّ حصّة؟

## ولماذا لا نرسل من هنا مباشرةً

ثلاثةُ أسباب، وكلُّها كان سيقع:

1. **الرسائل تخرج من رقمنا.** فلو كتب التركيبُ نصَّها لكان ما يصل
   الناسَ باسمنا مكتوبًا في مكانٍ لا نراه. ولهذا لا يحمل الحدثُ
   نصًّا أصلًا — لا حقلَ `text` ولا `message` — بل اسمَ حدثٍ
   وحقولَه، والصياغةُ قالبٌ عندنا.

2. **ولا يحمل رقمًا.** `to` فيه `employee_id` لا هاتف. فلو حمل
   الهاتفَ لصار كلُّ تركيبٍ — أو كلُّ مفتاحٍ يُسرَق منه — قادرًا
   على إرسال رسالةٍ من رقمنا إلى أيّ رقمٍ في الدنيا. واللوحةُ تحلّ
   الموظّفَ إلى رقمه من البيانات المرفوعة إليها هي.

3. **ومفتاحُ Evolution لا ينزل إلى مقرّ العميل.** من يملكه يملك
   الواتساب المرتبط به: إرسالًا باسمنا، وقراءةً للمحادثات.

## التفرّد — ولماذا في العمود لا في الكود

`idempotency_key` فريدٌ في المخطَّط. فمسحُ التواجد يُنادى من كل
طلبٍ يفتحه أيّ مستخدم، و«بصمتك مستحقّة» قد يُطلَق عشرين مرّةً في
الدقيقة. والفحصُ قبل الإدراج سباقٌ بين عمّال الخادم؛ أمّا القيدُ
في القاعدة فلا يُسابَق.

وهو نفسُه مفتاحُ التفرّد في اللوحة، فالإرسال مرّتين بعد انقطاعٍ
في الشبكة يُرفَض هناك أيضًا. الحمايةُ في الطرفين لأن الشبكة تقطع
بين النجاح والردّ عليه.

## والعمر

«بصمتك مستحقّة الآن» بعد يومين إزعاجٌ لا خدمة. فلكل حدثٍ عمرٌ
يسقط بعده، ويُقاس من **وقوعه** لا من وصوله — وتركيبٌ كان مفصولًا
أسبوعًا يُفرِغ صندوقَه حين يعود، فلولا ذلك لانهالت عليه رسائلُ
الأسبوع الماضي دفعةً واحدة.
"""

import json
import logging
import os
import time
from datetime import datetime, timedelta

log = logging.getLogger(__name__)

OUTBOX_TABLE = 'message_outbox'

# مفاتيح الإعداد في app_settings.
SETTING_ENABLED = 'message_gateway_enabled'
SETTING_URL = 'message_gateway_url'
SETTING_LAST_OK = 'message_gateway_last_ok'
SETTING_LAST_ERROR = 'message_gateway_last_error'
SETTING_REFUSED = 'message_gateway_last_refusal'

# سقفُ الدفعة. اللوحةُ ترفض ما زاد على خمسين في الطلب الواحد
# (`EventsController::MAX_BATCH`)، فالسقفُ هنا يساويه لا يتجاوزه.
BATCH_SIZE = 50

# سقفُ الصندوق. تركيبٌ يقيّد أحداثًا ولا يرفعها — لأن الرفع مُطفأ
# أو الشبكة مقطوعة شهرًا — يجب ألّا يُنمي جدولًا بلا حدّ في قاعدةٍ
# على قرصٍ محلّيّ. وما يُسقَط أقدمُه: الأحدث أولى بالإرسال.
MAX_OUTBOX = 5000

# ----------------------------------------------------------------
# الأحداث المعروفة
# ----------------------------------------------------------------
#
# نسخةٌ من `EventRegistry` في اللوحة — لا تُقرأ منها لأنها على خادمٍ
# آخر وقد يكون مقطوعًا. والإطلاقُ هنا لحدثٍ لا تعرفه اللوحة يُرفَض
# هناك بـ`unknown_event`؛ فالأسوأ المحتمل عند اختلافهما رفضٌ مكتوب،
# لا رسالةٌ خاطئة.
#
# و`system` منها لا يُطلَق من هنا أبدًا: `otp_code` من تركيبٍ يعني
# أن كلّ من يملك مفتاحَ عميلٍ يستطيع إرسال «رمزُ دخولك هو…» من
# رقمنا. اللوحةُ ترفضه، وهذا الجدولُ لا يعرفه أصلًا.
EVENTS = {
    'leave_requested':  {'ttl': 86400},
    'leave_decided':    {'ttl': 86400},
    'excuse_requested': {'ttl': 43200},
    'presence_due':     {'ttl': 7200},
    'presence_missed':  {'ttl': 86400},
}

SCHEMA = '''CREATE TABLE IF NOT EXISTS message_outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event TEXT NOT NULL,
    -- فريدٌ في المخطَّط: انظر شرح الوحدة. الفحصُ قبل الإدراج سباق.
    idempotency_key TEXT NOT NULL UNIQUE,
    data_json TEXT NOT NULL DEFAULT '{}',
    to_json TEXT NOT NULL DEFAULT '{}',
    -- وقتُ الوقوع لا وقتُ الرفع: العمر يُقاس منه.
    occurred_at TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT
)'''

INDEXES = (
    'CREATE INDEX IF NOT EXISTS idx_msgout_id ON message_outbox (id)',
)


def init_schema(conn):
    conn.execute(SCHEMA)
    for sql in INDEXES:
        conn.execute(sql)
    conn.commit()


def _now():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


# ------------------------------------------------------------ القيد

def emit(conn, event, idempotency_key, data=None, to=None, occurred_at=None):
    """يقيّد حدثًا للرفع. يعيد رقم الصفّ، أو None إن لم يُقيَّد.

    **لا يرمي.** يُنادى بعد نجاح العملية الأصلية — بعد حفظ الطلب،
    وبعد الاعتماد — فإخفاقُ القيد لا يجوز أن يُبطل ما نجح. وهي
    القاعدةُ نفسُها في `utils/notifications.py`.

    و`None` لا تعني عطبًا: أشيعُ أسبابها أن الحدث مقيَّدٌ من قبل
    بالمفتاح نفسه، وهو المقصود.
    """
    if event not in EVENTS:
        log.warning('حدثٌ غير معروف لم يُقيَّد: %s', event)
        return None
    key = (idempotency_key or '').strip()
    # ثمانون محرفًا: سقفُ اللوحة. والأطولُ يُرفض هناك بعد رحلةٍ
    # كاملة، فيُقصّ هنا حيث لا تكلفة.
    if not key or len(key) > 80:
        log.warning('مفتاحُ تفرّدٍ غير صالح للحدث %s', event)
        return None

    try:
        init_schema(conn)
        cur = conn.cursor()
        cur.execute(
            'INSERT OR IGNORE INTO message_outbox'
            ' (event, idempotency_key, data_json, to_json, occurred_at)'
            ' VALUES (?, ?, ?, ?, ?)',
            (event, key,
             json.dumps(data or {}, ensure_ascii=False),
             json.dumps(to or {}, ensure_ascii=False),
             occurred_at or _now()))
        conn.commit()
        if not cur.rowcount:
            return None          # مقيَّدٌ من قبل — وهذا هو المطلوب
        _trim(conn)
        return cur.lastrowid
    except Exception:
        log.exception('تعذّر قيد الحدث %s', event)
        return None


def _trim(conn):
    """يُبقي الصندوق تحت سقفه بإسقاط أقدمه.

    يُنادى بعد القيد لا في خيطٍ دوريّ: الصندوق لا ينمو إلا بالقيد،
    ومهمّةٌ دوريّةٌ لحراسته تركيبٌ زائدٌ على تركيبٍ لا مجدولَ فيه.
    """
    try:
        n = pending_count(conn)
        if n <= MAX_OUTBOX:
            return
        conn.execute(
            'DELETE FROM message_outbox WHERE id IN ('
            '  SELECT id FROM message_outbox ORDER BY id ASC LIMIT ?)',
            (n - MAX_OUTBOX,))
        conn.commit()
        log.warning('صندوق الأحداث تجاوز %s — أُسقط %s من أقدمه',
                    MAX_OUTBOX, n - MAX_OUTBOX)
    except Exception:
        log.exception('تعذّر تقليم صندوق الأحداث')


# ---------------------------------------------------------- القراءة

def pending_count(conn):
    try:
        init_schema(conn)
        row = conn.execute('SELECT COUNT(*) FROM message_outbox').fetchone()
        return row[0] if row else 0
    except Exception:
        return 0


def _expired(event, occurred_at, now):
    """أمضى الحدثُ عمرَه؟ يُقاس من الوقوع — انظر شرح الوحدة."""
    ttl = EVENTS.get(event, {}).get('ttl')
    if not ttl:
        return False
    try:
        when = datetime.strptime(str(occurred_at)[:19], '%Y-%m-%d %H:%M:%S')
    except (TypeError, ValueError):
        # تاريخٌ لا يُقرأ: لا يُسقَط الحدث بسببه. إسقاطُ ما لا نفهمه
        # يُخفي عطبًا، وإرسالُه يُظهره.
        return False
    return when + timedelta(seconds=ttl) < now


def peek(conn, limit=BATCH_SIZE, now=None):
    """أقدمُ ما ينتظر، بعد إسقاط ما انتهى عمرُه.

    تعيد `(events, expired_ids)`: الأولى تُرفع، والثانية تُمحى بلا
    رفع. والإسقاطُ هنا لا في الخادم لأن الخادم يرى وقتَ **الوصول**،
    فحدثُ أمس يصله اليوم فيبدو طازجًا.
    """
    now = now or datetime.now()
    try:
        init_schema(conn)
        rows = conn.execute(
            'SELECT id, event, idempotency_key, data_json, to_json, occurred_at'
            ' FROM message_outbox ORDER BY id ASC LIMIT ?',
            (limit,)).fetchall()
    except Exception:
        log.exception('تعذّرت قراءة صندوق الأحداث')
        return [], []

    events, expired = [], []
    for r in rows:
        rid, event, key, data_json, to_json, occurred = (
            r[0], r[1], r[2], r[3], r[4], r[5])
        if _expired(event, occurred, now):
            expired.append(rid)
            continue
        try:
            data = json.loads(data_json or '{}')
            to = json.loads(to_json or '{}')
        except ValueError:
            # صفٌّ تالف: يُسقَط مع المنتهي. إبقاؤه يوقف الصندوق أبدًا
            # عند الصفّ نفسه، فلا يصل ما بعده.
            log.warning('صفٌّ تالف في صندوق الأحداث: %s', rid)
            expired.append(rid)
            continue
        events.append({
            'id': rid,
            'event': event,
            'idempotency_key': key,
            'data': data,
            'to': to,
            'occurred_at': occurred,
        })
    return events, expired


def drop(conn, ids):
    """يمحو صفوفًا بأرقامها. يعيد عدد ما مُحي."""
    ids = [int(i) for i in (ids or [])]
    if not ids:
        return 0
    try:
        marks = ','.join('?' * len(ids))
        cur = conn.cursor()
        cur.execute(f'DELETE FROM message_outbox WHERE id IN ({marks})', ids)
        conn.commit()
        return cur.rowcount or 0
    except Exception:
        log.exception('تعذّر محو أحداثٍ من الصندوق')
        return 0


def mark_failed(conn, ids, error):
    """يزيد عدّادَ المحاولات ويسجّل السبب — ولا يمحو.

    الصفّ يبقى ليُعاد في الدورة القادمة. والعدّادُ ليس للإسقاط بل
    للعرض: «حاولنا سبعًا» يقول لصاحب اللوحة إن العطب مستمرّ.
    """
    ids = [int(i) for i in (ids or [])]
    if not ids:
        return 0
    try:
        marks = ','.join('?' * len(ids))
        cur = conn.cursor()
        cur.execute(
            f'UPDATE message_outbox SET attempts = attempts + 1, last_error = ?'
            f' WHERE id IN ({marks})', [str(error)[:200]] + ids)
        conn.commit()
        return cur.rowcount or 0
    except Exception:
        log.exception('تعذّر تسجيل إخفاق أحداث')
        return 0


# ------------------------------------------------------------ الرفع

def _settings():
    from utils.db import get_setting
    from utils import cloud_sync

    url = (get_setting(SETTING_URL, '') or '').strip()
    if not url:
        url = os.environ.get('HR_MESSAGE_GATEWAY_URL', '')
    if not url:
        url = _derive_url((get_setting(cloud_sync.SETTING_URL, '') or '').strip())

    return {
        'enabled': str(get_setting(SETTING_ENABLED, '0') or '0')
                   in ('1', 'true', 'True'),
        'url': url,
        'client_id': (get_setting(cloud_sync.SETTING_CLIENT, '') or '').strip(),
        'api_key': (get_setting(cloud_sync.SETTING_KEY, '') or '').strip(),
    }


def _derive_url(sync_url):
    """يستنبط عنوان البوّابة من عنوان الرفع — من أصله لا بقصّ ذيله.

    الاثنان على الخادم نفسه بمسارين مختلفين (`/api/sync` و
    `/api/v1/events`)، فقصُّ آخر جزءٍ من المسار يُنتج عنوانًا خاطئًا
    ولا يقول ذلك. أمّا الأصل (المخطَّط والمضيف) فثابتٌ بينهما.

    ويبقى `message_gateway_url` فوق هذا لمن فصلهما.
    """
    if not sync_url:
        return ''
    try:
        from urllib.parse import urlsplit
        parts = urlsplit(sync_url)
        if not parts.scheme or not parts.netloc:
            return ''
        return f'{parts.scheme}://{parts.netloc}/api/v1/events'
    except Exception:
        return ''


def _note(key, value):
    try:
        from utils.db import set_setting
        set_setting(key, str(value)[:400])
    except Exception:
        pass


def payload_for(events, client_id):
    """جسمُ الطلب — بلا نصٍّ وبلا رقم. انظر شرح الوحدة."""
    return {
        'client_id': client_id,
        'events': [{
            'event': e['event'],
            'idempotency_key': e['idempotency_key'],
            'data': e['data'],
            'to': e['to'],
            'occurred_at': e['occurred_at'],
        } for e in events],
    }


def run_once(conn=None, session=None, now=None):
    """دورةٌ واحدة. تُرجع dict يصف ما جرى — ولا تُطلق استثناءً للشبكة.

    الوكيل يعمل بلا مُراقب، فسقوطُه على انقطاعٍ عابر يعني توقّفَ
    الرسائل إلى أن ينتبه أحد. والانتباه لا يقع.
    """
    cfg = _settings()
    if not cfg['enabled']:
        return {'ok': True, 'skipped': 'disabled', 'sent': 0}
    if not (cfg['url'] and cfg['client_id'] and cfg['api_key']):
        return {'ok': False, 'skipped': 'unconfigured', 'sent': 0,
                'error': 'لم تُضبط وجهة البوّابة أو مفتاحها'}

    owned = conn is None
    if owned:
        from utils.db import get_db_connection
        conn = get_db_connection()
    try:
        return _run_once(conn, cfg, session, now)
    finally:
        if owned:
            try:
                conn.close()
            except Exception:
                pass


def _run_once(conn, cfg, session=None, now=None):
    import requests

    events, expired = peek(conn, now=now)
    dropped = drop(conn, expired)
    if not events:
        return {'ok': True, 'sent': 0, 'expired': dropped, 'idle': True}

    http = session or requests
    try:
        resp = http.post(cfg['url'],
                         json=payload_for(events, cfg['client_id']),
                         headers={'X-API-KEY': cfg['api_key'],
                                  'Content-Type': 'application/json'},
                         timeout=30)
    except Exception as e:
        msg = f'تعذّر الاتصال ببوّابة الرسائل: {str(e)[:160]}'
        _note(SETTING_LAST_ERROR, msg)
        mark_failed(conn, [e['id'] for e in events], msg)
        return {'ok': False, 'sent': 0, 'expired': dropped, 'error': msg}

    # `202` هي الردُّ الصحيح: القبولُ ليس إرسالًا. و`200` تُقبل أيضًا
    # لأن الرفضَ على رقمٍ بعينه يجعل الترقية في طرفٍ واحدٍ كسرًا.
    if resp.status_code not in (200, 202):
        msg = f'بوّابة الرسائل ردّت {resp.status_code}'
        _note(SETTING_LAST_ERROR, msg)
        mark_failed(conn, [e['id'] for e in events], msg)
        return {'ok': False, 'sent': 0, 'expired': dropped,
                'status': resp.status_code, 'error': msg}

    try:
        body = resp.json()
    except Exception:
        body = {}

    decided, refusals = _reconcile(events, body)
    if not decided:
        # ردٌّ لا يُطابق ما أُرسل: لا يُمحى شيء. والإعادةُ آمنة —
        # مفتاحُ التفرّد في اللوحة يردّ المكرّر بـ`duplicate`.
        msg = 'ردٌّ غير مفهوم من البوّابة'
        _note(SETTING_LAST_ERROR, msg)
        mark_failed(conn, [e['id'] for e in events], msg)
        return {'ok': False, 'sent': 0, 'expired': dropped, 'error': msg}

    sent = drop(conn, decided)
    _note(SETTING_LAST_OK, time.strftime('%Y-%m-%d %H:%M:%S'))
    _note(SETTING_LAST_ERROR, '')
    if refusals:
        _note(SETTING_REFUSED, ' · '.join(refusals[:5]))

    # الأسبابُ لا عددُها. المطابقةُ بالموضع تُنتج العددَ نفسَه حين
    # يُرفض واحدٌ من اثنين — ولا يفترق الاثنان إلا في **أيّ** حدثٍ
    # نُسب إليه الرفض. فعددٌ وحده لا يكشف الخلط.
    return {'ok': True, 'sent': sent, 'expired': dropped,
            'refused': len(refusals), 'refusals': refusals,
            'pending': pending_count(conn)}


def _reconcile(events, body):
    """أيُّ الأحداث حُسم أمرُها؟ يعيد `(ids, refusals)`.

    ## لماذا بالمفتاح لا بالترتيب

    مطابقةُ النتيجة بموضعها في القائمة تصحّ ما دام الخادمُ يردّ
    واحدةً لكلٍّ وبالترتيب نفسه. وهو يفعل اليوم — وذلك عقدٌ لم
    يكتبه أحد. فتُطابَق بـ`idempotency_key`، وهو في الطلب والردّ.

    ## ولماذا يُمحى المرفوض

    اللوحة تردّ `202` على الرفض أيضًا: «قرأتُ وقرّرتُ ألّا أرسل» —
    حدثٌ مُطفأ في السياسة، أو حصّةٌ نفدت، أو موظّفٌ بلا رقم. وكلُّها
    لا يُغيّرها التركيب، فإعادةُ المحاولة تكرارٌ أبديّ على قرارٍ لن
    يتبدّل إلا في اللوحة. أمّا ما لم يُحسم فيبقى.
    """
    results = body.get('results')
    if not isinstance(results, list):
        return [], []

    by_key = {}
    for r in results:
        if isinstance(r, dict) and r.get('idempotency_key'):
            by_key[r['idempotency_key']] = r

    ids, refusals = [], []
    for i, e in enumerate(events):
        r = by_key.get(e['idempotency_key'])
        if r is None:
            # خادمٌ أقدم لا يعيد المفتاح: يُرجَع إلى الموضع، وهو
            # صحيحٌ ما دام العدد مطابقًا. وإلّا فلا يُمحى شيء.
            if len(results) != len(events) or by_key:
                continue
            r = results[i] if isinstance(results[i], dict) else None
            if r is None:
                continue
        ids.append(e['id'])
        if not r.get('queued'):
            refusals.append(f"{e['event']}: {_reason_of(r)}")
    return ids, refusals


def _reason_of(result):
    """السببُ من حيث يضعه الخادم فعلًا.

    اللوحة تضع `reason` للرفض **قبل** القنوات (حدثٌ مجهول، أو
    `system_only`)، وتضع `refused: {"whatsapp": "duplicate"}` للرفض
    **داخل** قناة. والقراءةُ من `reason` وحده كانت تُرجع «مرفوض»
    لكلّ رفضٍ قناويّ — فيقرأ صاحبُ اللوحة سطرًا لا يقول شيئًا، وهو
    السطرُ الذي يُشخَّص منه العطب.
    """
    if result.get('reason'):
        return str(result['reason'])
    refused = result.get('refused')
    if isinstance(refused, dict) and refused:
        return ' · '.join(f'{k}={v}' for k, v in sorted(refused.items()))
    return 'مرفوض'
