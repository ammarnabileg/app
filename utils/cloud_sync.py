"""رفعُ بيانات المقرّ إلى `client.onz.one` — دورةٌ واحدة قابلة للقياس.

## ما هذا

العميل الذي يُركّب النظام في مقرّه لا يملك شيئًا يراه من خارجه: لا من
هاتفه، ولا محاسبُه من بيته، ولا هو نفسه في سفر. فهذا الوكيل يرفع
بياناته إلى مساحةٍ سحابيّة خاصّة به، يدخلها بالاسم وكلمة المرور
نفسيهما اللذين له على onz.one.

والاتجاه **واحد**: من المقرّ إلى السحابة. المقرّ هو المرجع، والسحابة
مرآةٌ للقراءة. ولا نكتب من السحابة إلى المقرّ — لأن مزامنةً في
اتجاهين تحتاج حسمَ التعارض، وحسمُ التعارض على رواتبَ وحضورٍ بـ«الأحدث
يفوز» يُتلف بيانات صامتًا. فإن أُريد الاتجاه الثاني يومًا، فبقرارٍ
مستقلّ وتصميمٍ للتعارض لا بإضافة سطرٍ هنا.

## بنيتها

`utils.cloud_outbox` يقول **ما** تغيّر. وهذه الوحدة تقول **كيف**
يُرسَل: تقرأ دفعةً، تبني الحمولة، ترفع، ثم لا تمحو من الدفتر إلا بعد
أن يؤكّد الخادم. فانقطاعُ الشبكة يترك الدفعة مكانها فتُعاد — والإعادة
لا تُكرِّر شيئًا لأن الطرف الآخر يكتب بالمفتاح.

## ولماذا تُرسَل الأنواع

الخادم القديم كان يستنتج نوع العمود من **أوّل صفٍّ يصله**: راتبٌ
قيمته `0` في أوّل صفّ يجعل العمود `INT`، فـ`1500.75` بعده تُبتر إلى
`1500`. ونحن هنا نعرف النوع المُعلَن فعلًا، فنرسله ولا نتركه يخمّن.

يُشغَّل مرّةً:  python -m utils.cloud_sync --once
"""
import logging
import os
import threading
import time

logger = logging.getLogger(__name__)

# حجم الدفعة. صغيرةٌ عمدًا: الهدف أن تمرّ الدفعة على وصلةٍ رديئة، لا
# أن تُنجز الرفع بأقلّ عدد طلبات.
BATCH_SIZE = 200
DEFAULT_INTERVAL = 120

# مفاتيح الإعداد في app_settings.
SETTING_ENABLED = 'cloud_sync_enabled'
SETTING_URL = 'cloud_sync_url'
SETTING_CLIENT = 'cloud_sync_client_id'
SETTING_KEY = 'cloud_sync_api_key'
SETTING_LAST_OK = 'cloud_sync_last_ok'
SETTING_LAST_ERROR = 'cloud_sync_last_error'
# آخرُ محاولةٍ **تلقائيّة** (من الخيط الخلفيّ لا من الزرّ): بها تعرف الشاشةُ
# أنّ الرفعَ التلقائيّ يعمل فعلًا، لا أنّ الزرَّ نجح وحده.
SETTING_LAST_AUTO = 'cloud_sync_last_auto'
AUTO_STALE_MINUTES = 15

# يُوقظ الخيطَ الخلفيّ فورًا (عند تفعيل الرفع من الإعدادات) بدل أن ينتظر
# نومتَه — وكانت ١٠ دقائق حين يكون الرفعُ مُطفأً.
WAKE = threading.Event()


def wake():
    WAKE.set()


def sleep_or_wake(seconds):
    """ينام حتى تنقضي المدّة أو يُوقَظ. يعيد True إن أُوقظ."""
    woke = WAKE.wait(seconds)
    WAKE.clear()
    return woke


def mark_auto_attempt(res):
    """تسجّل الخيطُ الخلفيّ محاولتَه — إلا وهو مُطفأ (لا محاولةَ إذن)."""
    if res and res.get('skipped') == 'disabled':
        return
    _note(SETTING_LAST_AUTO, time.strftime('%Y-%m-%d %H:%M:%S'))


def auto_status(enabled, last_auto, now=None):
    """حالُ الرفع التلقائيّ للشاشة: 'off' | 'waiting' | 'running' | 'stale'.

    `stale`: مُفعَّلٌ ولم يحاول الخيطُ منذ أكثر من ربع ساعة — أي أنّه
    لا يعمل (البرنامج شُغِّل بطريقةٍ لا تُطلق الخيوط، أو سقط).
    """
    from datetime import datetime, timedelta
    if not enabled:
        return 'off'
    if not last_auto:
        return 'waiting'
    try:
        t = datetime.strptime(str(last_auto)[:19], '%Y-%m-%d %H:%M:%S')
    except ValueError:
        return 'waiting'
    now = now or datetime.now()
    return 'stale' if now - t > timedelta(minutes=AUTO_STALE_MINUTES) else 'running'


def _settings():
    from utils.db import get_setting
    url = (get_setting(SETTING_URL, '') or '').strip().rstrip('/')
    return {
        'enabled': str(get_setting(SETTING_ENABLED, '0') or '0') in ('1', 'true', 'True'),
        'url': url or os.environ.get('HR_CLOUD_SYNC_URL', ''),
        'client_id': (get_setting(SETTING_CLIENT, '') or '').strip(),
        'api_key': (get_setting(SETTING_KEY, '') or '').strip(),
    }


def _note(key, value):
    try:
        from utils.db import set_setting
        set_setting(key, str(value)[:400])
    except Exception:
        pass


def declared_types(conn, table):
    """الأنواع كما أعلنها المخطَّط المحلّي — لا كما تبدو من قيمةٍ واحدة."""
    out = {}
    for row in conn.execute(f'PRAGMA table_info("{table}")'):
        out[row[1]] = (row[2] or 'TEXT').upper()
    return out


def build_batch(conn, limit=BATCH_SIZE):
    """دفعةٌ واحدة: ما يُكتب، وما يُحذف، وإلى أيّ `seq` تصل.

    تُقدَّم صفوفُ المشي الأوّل على الدفتر، لأن السحابة الفارغة تُظهر
    «لا بيانات» للعميل إلى أن يكتمل — فكلّما أسرع الامتلاء كان أهون.
    """
    from utils import cloud_outbox as ob

    data, deletes, schema = {}, {}, {}

    # (١) المشي الأوّل على ما سبق تثبيتَ الدفتر.
    remaining = limit
    baseline_marks = []
    for table in ob.SYNC_TABLES:
        if remaining <= 0:
            break
        ids = ob.baseline_batch(conn, table, size=remaining)
        if not ids:
            continue
        rows = ob.read_rows(conn, table, ids)
        if rows:
            data.setdefault(table, []).extend(rows)
            schema[table] = declared_types(conn, table)
        # المؤشّر يتقدّم إلى آخر id قرأناه لا إلى آخر صفٍّ أعاده القارئ:
        # صفٌّ حُذف بين الاستعلامين لا يجوز أن يوقف المشي عنده للأبد.
        baseline_marks.append((table, max(ids)))
        remaining -= len(ids)

    # (٢) ما سجّله الدفتر بعد ذلك.
    pending = ob.peek(conn, limit=limit) if remaining > 0 else []
    up_to = 0
    by_table = {}
    for item in pending[:remaining]:
        up_to = max(up_to, item['seq'])
        if item['op'] == 'delete':
            deletes.setdefault(item['table'], []).append(item['row_id'])
        else:
            by_table.setdefault(item['table'], []).append(item['row_id'])

    for table, ids in by_table.items():
        rows = ob.read_rows(conn, table, ids)
        if rows:
            data.setdefault(table, []).extend(rows)
            schema[table] = declared_types(conn, table)
        # صفٌّ في الدفتر لا وجود له الآن — أُدرج ثم حُذف قبل أن نصل —
        # يُرسَل حذفًا كي لا يبقى في السحابة أثرٌ لصفٍّ لم يستقرّ قطّ.
        got = {r['id'] for r in rows}
        for missing in set(ids) - got:
            deletes.setdefault(table, []).append(missing)

    return {'data': data, 'deletes': deletes, 'schema': schema,
            'up_to_seq': up_to, 'baseline_marks': baseline_marks}


def payload_for(batch, client_id):
    return {
        'client_id': client_id,
        'protocol': 2,
        'batch_seq': batch['up_to_seq'],
        'data': batch['data'],
        'deletes': batch['deletes'],
        'schema': batch['schema'],
    }


def commit_batch(conn, batch):
    """يُثبِّت تقدّم الدفعة — بعد نجاح الرفع وحده."""
    from utils import cloud_outbox as ob
    for table, last_id in batch['baseline_marks']:
        ob.baseline_advance(conn, table, last_id)
    if batch['up_to_seq']:
        ob.ack(conn, batch['up_to_seq'])


def run_once(conn=None, session=None, force=False):
    """دورةٌ واحدة. تُرجع dict يصف ما جرى — لا تُطلق استثناءً للشبكة.

    الوكيل يعمل بلا مُراقب، فسقوطُه على خطأ شبكةٍ عابر يعني توقّفَ
    الرفع إلى أن ينتبه أحد. والانتباه لا يقع.

    و`force` لزرّ «ارفع الآن»: الترتيبُ الطبيعيّ عند الضبط أن
    يُجرَّب قبل أن يُفعَّل، وزرٌّ يشترط التفعيلَ أوّلًا يجعل أوّلَ
    تجربةٍ على بياناتٍ تُرفع فعلًا. ولا يمسّ الخيطَ الخلفيّ: هو
    يُنادى بلا `force` فيبقى المفتاحُ في الشاشة هو ما يحكمه.
    """
    cfg = _settings()
    if not cfg['enabled'] and not force:
        return {'ok': True, 'skipped': 'disabled', 'sent': 0}
    if not (cfg['url'] and cfg['client_id'] and cfg['api_key']):
        return {'ok': False, 'skipped': 'unconfigured', 'sent': 0,
                'error': 'لم تُضبط وجهة الرفع أو مفتاحه'}

    owned = conn is None
    if owned:
        # خيطٌ خلفيّ بلا سياق Flask يفتح اتصالًا خاصًّا به، ويُغلقه.
        # وتركُه مفتوحًا كلَّ دورة يُراكم واصفاتِ ملفّات على تركيبٍ
        # يعمل شهورًا بلا إعادة تشغيل.
        from utils.db import get_db_connection
        conn = get_db_connection()

    try:
        return _run_once(conn, session)
    finally:
        if owned:
            try:
                conn.close()
            except Exception:
                pass


def _run_once(conn, session=None):
    import requests

    cfg = _settings()
    batch = build_batch(conn)
    sent = sum(len(v) for v in batch['data'].values())
    removed = sum(len(v) for v in batch['deletes'].values())
    if not sent and not removed:
        return {'ok': True, 'sent': 0, 'deleted': 0, 'idle': True}

    http = session or requests
    try:
        resp = http.post(cfg['url'],
                         json=payload_for(batch, cfg['client_id']),
                         headers={'X-API-KEY': cfg['api_key'],
                                  'Content-Type': 'application/json'},
                         timeout=60)
    except Exception as e:
        msg = f'تعذّر الاتصال بالسحابة: {str(e)[:160]}'
        _note(SETTING_LAST_ERROR, msg)
        return {'ok': False, 'sent': 0, 'deleted': 0, 'error': msg}

    if resp.status_code != 200:
        # لا نمحو من الدفتر: الدفعة تُعاد في الدورة القادمة.
        msg = server_error_message(resp)
        _note(SETTING_LAST_ERROR, msg)
        return {'ok': False, 'sent': 0, 'deleted': 0,
                'status': resp.status_code, 'error': msg}

    try:
        body = resp.json()
    except Exception:
        body = {}
    if body.get('status') != 'success':
        msg = f"الخادم رفض الدفعة: {str(body.get('error') or body)[:160]}"
        _note(SETTING_LAST_ERROR, msg)
        return {'ok': False, 'sent': 0, 'deleted': 0, 'error': msg}

    commit_batch(conn, batch)
    _note(SETTING_LAST_OK, time.strftime('%Y-%m-%d %H:%M:%S'))
    # القبولُ يُقرأ من ردٍّ ناجح وحده. وما تُسجّله الإعادةُ يأخذ `seq`
    # بعد `up_to_seq`، فلا يمحوه الإقرار — ويخرج في الدورة التالية.
    try:
        from utils import cloud_outbox as ob
        ob.resend_when_accepted(conn, body.get('accepts'))
        ob.resend_late_tables(conn, body.get('accepts'))
        conn.commit()
    except Exception as e:
        logger.warning(f'cloud sync: payroll resend skipped: {e}')
    _note(SETTING_LAST_ERROR, '')

    from utils import cloud_outbox as ob
    return {'ok': True, 'sent': sent, 'deleted': removed,
            'pending': ob.pending_count(conn),
            'baseline_done': ob.baseline_done(conn),
            'stats': body.get('stats', {})}


def server_error_message(resp):
    """ما يُكتب في الشاشة حين لا يردّ الخادم 200 — بسببه إن قاله.

    كان «الخادم ردّ 500» وحده: الخادمُ يُرسل مرجعًا يُبحث به في سجلّه،
    وسببًا حين يكون معروفًا (قاعدةُ العميل لم تُنشأ)، وكلاهما كان يُرمى."""
    import re
    code = resp.status_code
    try:
        info = resp.json() or {}
    except Exception:
        info = {}
    if not isinstance(info, dict):
        info = {}
    clean = lambda v: re.sub(r'[^A-Za-z0-9_]', '', str(v or ''))[:64]
    ref = clean(info.get('ref'))
    if info.get('error') == 'client_db_unavailable':
        db = clean(info.get('database')) or 'client_…_db'
        return (f'الخادم ردّ {code}: لم يستطع فتح قاعدة بيانات العميل «{db}» على السيرفر. '
                f'أنشئها من لوحة الاستضافة (Plesk ← Databases) بنفس مستخدم قاعدة اللوحة، ثم يُعاد الرفع وحده.')
    if code in (401, 403):
        return f'الخادم ردّ {code}: مُعرِّف العميل أو مفتاح الرفع غير صحيح.'
    if ref:
        return f'الخادم ردّ {code} — المرجع {ref} (ابحث عنه في سجلّ أخطاء السيرفر).'
    return f'الخادم ردّ {code}'


def has_backlog(res):
    """بقي ما يُرفع بعد هذه الدورة؟ — فتُتابَع فورًا بدل انتظار الدورة القادمة.

    الدفترُ وحده لا يكفي: عند أوّل تفعيلٍ يكون **فارغًا** والقاعدةُ كلُّها
    لم تُرفع بعد (المشي الأوّل). فكان السؤالُ عن `pending` وحده يُبطئ الرفعَ
    الأوّل إلى ٢٠٠ صفٍّ كلَّ دقيقتين: ٢٥ ألف صفّ ≈ ٤ ساعات بدل دقائق.
    """
    if not res or not res.get('ok') or res.get('idle') or res.get('skipped'):
        return False
    return bool(res.get('pending')) or res.get('baseline_done') is False


CATCH_UP_DELAY = 5


def run_forever(interval=DEFAULT_INTERVAL):
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s [%(levelname)s] %(message)s')
    logger.info('cloud sync agent started')
    while True:
        try:
            res = run_once()
            mark_auto_attempt(res)
            if res.get('skipped'):
                logger.info(f"sync skipped: {res['skipped']}")
            elif res.get('ok'):
                if not res.get('idle'):
                    logger.info(f"sync ok | sent={res['sent']} "
                                f"deleted={res['deleted']} "
                                f"pending={res.get('pending')}")
            else:
                logger.warning(f"sync failed: {res.get('error')}")
        except Exception as e:                      # pragma: no cover - حارس
            logger.exception(f'sync cycle crashed: {e}')
            res = None
        sleep_or_wake(CATCH_UP_DELAY if has_backlog(res) else interval)


if __name__ == '__main__':
    import sys
    if '--once' in sys.argv:
        logging.basicConfig(level=logging.INFO)
        print(run_once())
    else:
        run_forever()
