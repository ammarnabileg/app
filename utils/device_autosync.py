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


def summary(punches, users):
    """سطرٌ يُعرض في صفحة البصمة: ما الذي جرى في آخر تشغيلٍ تلقائيّ."""
    parts = []
    if punches is None:
        parts.append('البصمات: تخطّي — مزامنةٌ يدويّة كانت جارية')
    elif isinstance(punches, dict):
        parts.append(f"البصمات: {int(punches.get('attendance_synced') or 0)} جديدة من "
                     f"{int(punches.get('successful_devices') or 0)}/{int(punches.get('total_devices') or 0)} جهاز")
    if users is None:
        parts.append('المستخدمون: تخطّي')
    elif isinstance(users, dict):
        parts.append(f"المستخدمون: أُضيف {int(users.get('employees_added') or 0)} موظّف")
    return ' · '.join(parts)


def run_once():
    """دورةٌ واحدة: البصماتُ ثم المستخدمون، ثم يُوقَظ الرفعُ السحابيّ."""
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
    try:
        from utils import cloud_sync
        cloud_sync.wake()
    except Exception:
        pass
    return punches, users


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
            'next': nxt, 'busy': busy()}
