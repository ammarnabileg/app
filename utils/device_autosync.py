# -*- coding: utf-8 -*-
"""مزامنةُ أجهزة البصمة وحدها — كلَّ ساعة.

زرّا «مزامنة الآن» (البصمات) و«مزامنة المستخدمين» في صفحة البصمة كانا
يُضغطان باليد. فمن نسيهما يومًا رأى حضورًا ناقصًا في البرنامج وفي
البوّابة (onz.one/portal) حتى يتذكّر. الآن يعملان وحدهما كلَّ ساعة.

## قفلٌ واحد للزرّ وللخيط

`fingerprint_manager` كائنٌ واحدٌ مشترك، وسجلُّه (`sync_logs`) يُمسح أوّلَ
كلّ مزامنة — فمزامنتان معًا تتداخل سجلّاتهما، وجهازُ ZK يُعطَّل أثناء
السحب فالثانيةُ تجده مشغولًا. فيمرّ الاثنان بقفلٍ واحد: من وجده مشغولًا
يُقال له «جارية» ولا ينتظر.

## وبعد السحب يُوقَظ الرفعُ السحابيّ

فتصل البصماتُ الجديدة إلى البوّابة خلال دقائق، لا بعد دورة الرفع التالية.
"""
import threading
import time
from datetime import datetime

SETTING_ENABLED = 'device_autosync_enabled'     # '1' افتراضًا
SETTING_LAST = 'device_autosync_last'           # آخرُ تشغيلٍ تلقائيّ
SETTING_LAST_RESULT = 'device_autosync_result'  # ملخّصُه للشاشة
INTERVAL_SECONDS = 3600
CHECK_EVERY = 300                               # يُسأل كلَّ خمس دقائق: التفعيلُ يسري بلا انتظار ساعة
START_DELAY = 120                               # لا يُزاحم الإقلاع

LOCK = threading.Lock()


def _get(key, default=None):
    try:
        from utils.db import get_setting
        v = get_setting(key, default)
        return default if v is None else v
    except Exception:
        return default


def _set(key, value):
    try:
        from utils.db import set_setting
        set_setting(key, str(value))
    except Exception:
        pass


def enabled():
    return str(_get(SETTING_ENABLED, '1')) not in ('0', 'false', 'False', '')


def busy():
    return LOCK.locked()


def run_punches():
    """«مزامنة الآن»: سحبُ البصمات من الأجهزة. None إن كانت مزامنةٌ جارية."""
    if not LOCK.acquire(blocking=False):
        return None
    try:
        from fingerprint_sync import sync_all_fingerprint_devices
        return sync_all_fingerprint_devices()
    finally:
        LOCK.release()


def run_users():
    """«مزامنة المستخدمين»: موظّفو الأجهزة إلى جدول الموظّفين. None إن كانت جارية."""
    if not LOCK.acquire(blocking=False):
        return None
    try:
        from fingerprint_sync import sync_users_to_employees
        return sync_users_to_employees()
    finally:
        LOCK.release()


def _has_active_devices():
    try:
        import sqlite3
        from utils.db import DB_PATH
        conn = sqlite3.connect(DB_PATH)
        try:
            return conn.execute('SELECT COUNT(*) FROM fingerprint_devices WHERE is_active = 1').fetchone()[0] > 0
        finally:
            conn.close()
    except Exception:
        return False


def due(now=None, last=None):
    """هل حان التشغيل؟ أوّلُ مرّة، أو مرّت ساعةٌ منذ الأخير."""
    now = now or time.time()
    last = _get(SETTING_LAST, '') if last is None else last
    if not last:
        return True
    try:
        then = datetime.strptime(str(last)[:19], '%Y-%m-%d %H:%M:%S').timestamp()
    except ValueError:
        return True
    return now - then >= INTERVAL_SECONDS


def _punch_text(punches):
    if punches is None:
        return 'البصمات: تخطّي — مزامنةٌ يدويّة كانت جارية'
    if isinstance(punches, dict):
        return (f"البصمات: {int(punches.get('attendance_synced') or 0)} جديدة من "
                f"{int(punches.get('successful_devices') or 0)}/{int(punches.get('total_devices') or 0)} جهاز")
    return ''


def _users_text(users):
    if users is None:
        return 'المستخدمون: تخطّي'
    if isinstance(users, dict):
        return f"المستخدمون: أُضيف {int(users.get('employees_added') or 0)} موظّف"
    return ''


SKIP = object()                                 # «لم يُطلب» — غيرُ «طُلب فوُجد مشغولًا» (None)


def summary(punches, users):
    """سطرٌ يُعرض في صفحة البصمة: ما الذي جرى في آخر تشغيلٍ تلقائيّ."""
    parts = [_punch_text(punches) if punches is not SKIP else '',
             _users_text(users) if users is not SKIP else '']
    return ' · '.join(p for p in parts if p)


LOG_KEEP = 300                                  # سجلٌّ يُقرأ لا أرشيف: آخرُ ٣٠٠ تشغيل
SOURCES = {'auto': 'تلقائيّة', 'manual': 'من البرنامج', 'portal': 'من بوّابة الشركة'}


def _int(d, k):
    try:
        return int((d or {}).get(k) or 0)
    except (TypeError, ValueError):
        return 0


def log_run(source, started_at, punches, users):
    """سطرٌ في `device_sync_log` — يُرفع إلى السحابة فيراه صاحبُ الشركة."""
    try:
        import sqlite3
        from utils.db import DB_PATH
        failed = [x for x in (punches, users) if isinstance(x, dict) and x.get('success') is False]
        _p = None if punches is SKIP else punches
        _u = None if users is SKIP else users
        ok = 0 if failed else 1
        msg = summary(punches, users)
        if failed:
            err = '; '.join(str(x.get('error') or x.get('message') or '') for x in failed if
                            (x.get('error') or x.get('message')))
            if err:
                msg = (msg + ' — ' if msg else '') + err[:200]
        conn = sqlite3.connect(DB_PATH)
        try:
            conn.execute(
                'INSERT INTO device_sync_log (started_at, finished_at, source, punches_new,'
                ' devices_ok, devices_total, users_added, ok, message)'
                ' VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (started_at, time.strftime('%Y-%m-%d %H:%M:%S'), source,
                 _int(_p, 'attendance_synced'), _int(_p, 'successful_devices'),
                 _int(_p, 'total_devices'), _int(_u, 'employees_added'), ok, msg))
            conn.execute('DELETE FROM device_sync_log WHERE id <= '
                         '(SELECT MAX(id) FROM device_sync_log) - ?', (LOG_KEEP,))
            conn.commit()
        finally:
            conn.close()
    except Exception as e:                       # noqa: BLE001 — السجلُّ لا يُفشل المزامنة
        print(f'[device-autosync] تعذّر تسجيلُ التشغيل: {e}')


def history(limit=20):
    """آخرُ التشغيلات للشاشة، الأحدثُ أوّلًا."""
    try:
        import sqlite3
        from utils.db import DB_PATH
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute('SELECT * FROM device_sync_log ORDER BY id DESC LIMIT ?',
                                (int(limit),)).fetchall()
        finally:
            conn.close()
        return [dict(r, source_label=SOURCES.get(r['source'], r['source'] or '')) for r in rows]
    except Exception:
        return []


def run_once(source='auto'):
    """دورةٌ واحدة: البصماتُ ثم المستخدمون، ثم يُوقَظ الرفعُ السحابيّ.

    `source`: 'auto' (كلَّ ساعة) أو 'manual' (زرُّ «مزامنة الآن») أو
    'portal' (طلبٌ من onz.one/portal). والموعدُ التالي يُحسب من أيّها كان:
    مزامنةٌ يدويّة الآن تُغني عن تلقائيّةٍ بعد دقيقة.
    """
    started = time.strftime('%Y-%m-%d %H:%M:%S')
    punches = users = None
    try:
        punches = run_punches()
    except Exception as e:                       # noqa: BLE001
        punches = {'success': False, 'error': str(e)[:200]}
    try:
        users = run_users()
    except Exception as e:                       # noqa: BLE001
        users = {'success': False, 'error': str(e)[:200]}
    _set(SETTING_LAST, time.strftime('%Y-%m-%d %H:%M:%S'))
    _set(SETTING_LAST_RESULT, summary(punches, users))
    if punches is not None or users is not None:
        log_run(source, started, punches, users)
    try:
        from utils import cloud_sync
        cloud_sync.wake()
    except Exception:
        pass
    return punches, users


def start_in_background(source='manual'):
    """«مزامنة الآن» بلا انتظار: False إن كانت مزامنةٌ جارية."""
    if busy():
        return False
    threading.Thread(target=run_once, kwargs={'source': source}, daemon=True).start()
    return True


def background_worker():
    """خيطُ البرنامج: يسأل كلَّ خمس دقائق، ويُشغّل إن حان الموعد."""
    time.sleep(START_DELAY)
    while True:
        try:
            if enabled() and _has_active_devices() and due():
                run_once()
        except Exception as e:                   # noqa: BLE001
            # لا يُسمح لخطأ بقتل الخيط: خيطٌ مات يعني مزامنةً توقّفت بصمت.
            print(f'[device-autosync] خطأ: {e}')
        time.sleep(CHECK_EVERY)


def status():
    """للشاشة: مفعّلة؟ وآخرُ تشغيل وملخّصُه، والموعدُ التالي تقريبًا."""
    last = _get(SETTING_LAST, '') or ''
    nxt = ''
    if last:
        try:
            t = datetime.strptime(str(last)[:19], '%Y-%m-%d %H:%M:%S').timestamp() + INTERVAL_SECONDS
            nxt = time.strftime('%H:%M', time.localtime(t))
        except ValueError:
            nxt = ''
    return {'enabled': enabled(), 'last': str(last)[:16], 'result': _get(SETTING_LAST_RESULT, '') or '',
            'next': nxt, 'busy': busy(), 'history': history()}
