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
# البصماتُ نفسُها (صوابع ووجه) من كلّ جهاز مرّةً في اليوم — '1' افتراضًا.
SETTING_TEMPLATES = 'device_autosync_templates'
TEMPLATES_EVERY = 24 * 3600
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
        result = sync_all_fingerprint_devices()
        # الماركاتُ الجديدة (Hikvision…): حضورُها، وحذفُها المؤجّل، وموقوفوها — بالمحرّك الموحّد.
        try:
            from utils.devices import engine as _eng
            brands = _eng.run_cycle()
            if isinstance(result, dict) and (brands.get('attendance') or {}).get('devices'):
                result['brands'] = brands
        except Exception:
            pass
        # من أُضيف يدويًّا منذ المزامنة السابقة: بصماتُه المنتظرة تدخل الآن.
        try:
            from utils import pending_punches
            pending_punches.adopt()
        except Exception:
            pass
        _fix_hire_dates()
        _enforce_blocked()
        return result
    finally:
        LOCK.release()


def _enforce_blocked():
    """الموقوفون على الأجهزة المباشرة يُحذفون، ومن فُعّل يُعاد — جهازٌ كان مقفولًا
    لحظةَ الإيقاف يُعالَج هنا (utils.device_access)."""
    try:
        from utils import device_access
        res = device_access.enforce_all()
        if res.get('removed') or res.get('restored'):
            print(f"[device-autosync] الموقوفون: حُذف {res['removed']}، وأُعيد {res['restored']}")
    except Exception as e:                       # noqa: BLE001
        print(f'[device-autosync] فرضُ الإيقاف على الأجهزة تعذّر: {e}')


def _fix_hire_dates():
    """بعد سحب البصمات: موظّفٌ أُضيف من الجهاز اليوم وله بصماتٌ قديمة يأخذ أوّلَها
    تاريخَ تعيين (انظر `utils.hire_dates`) — وإلّا غاب عن كشف الأشهر التي عمل فيها."""
    try:
        import sqlite3
        from utils.db import DB_PATH
        from utils.hire_dates import fix_device_placeholder_hire_dates
        # اتصالٌ خاصّ لا اتصالُ الطلب: الزرُّ يُنادي هذا داخل طلب Flask، وإغلاقُ
        # اتصاله (g.db) يُسقط ما بعده في الطلب نفسه.
        c = sqlite3.connect(DB_PATH, timeout=30)
        c.row_factory = sqlite3.Row
        try:
            fix_device_placeholder_hire_dates(c)
        finally:
            try:
                c.close()
            except Exception:
                pass
    except Exception as e:                       # noqa: BLE001
        print(f'[device-autosync] تصحيحُ تواريخ التعيين تعذّر: {e}')


def run_users():
    """«مزامنة المستخدمين»: موظّفو الأجهزة إلى جدول الموظّفين. None إن كانت جارية."""
    if not LOCK.acquire(blocking=False):
        return None
    try:
        from fingerprint_sync import sync_users_to_employees
        res = sync_users_to_employees()
        try:
            from utils.devices import engine as _eng
            b = _eng.sync_users_all()
            if isinstance(res, dict) and b.get('employees_added'):
                res['employees_added'] = int(res.get('employees_added') or 0) + b['employees_added']
        except Exception:
            pass
        # موظّفون أُضيفوا الآن من الأجهزة: بصماتُهم قبل إضافتهم تدخل الحضور.
        try:
            from utils import pending_punches
            n = pending_punches.adopt()
            if n and isinstance(res, dict):
                res['adopted_punches'] = n
        except Exception:
            pass
        return res
    finally:
        LOCK.release()


def templates_enabled():
    return str(_get(SETTING_TEMPLATES, '1')) not in ('0', 'false', 'False', '')


def run_templates(force=False):
    """البصماتُ نفسُها من كلّ جهازٍ نشط — مرّةً في اليوم لكلّ جهاز.

    - المباشر (K40): تُسحب الآن وتُحفظ بإصدارها (device_access.pull_direct) — كانت
      لا تُسحب إلّا من «إدارة مستخدمي الأجهزة» يدويًّا.
    - ADMS: أمرا «DATA QUERY BIODATA» (صوابع Type=1 ووجه Type=9) للجهاز كلِّه، فيردّ
      حين يتّصل (على /cdata أو /querydata).
    يُرجع ملخّصًا، ولا يرمي.
    """
    out = {'templates': 0, 'devices': 0, 'queued': 0, 'errors': []}
    if not force and not templates_enabled():
        return out
    import json
    from utils.db import get_db_connection
    conn = get_db_connection()
    try:
        conn.execute('''CREATE TABLE IF NOT EXISTS device_template_pulls (
            device_id INTEGER PRIMARY KEY, last_at REAL)''')
        now = time.time()
        from utils.devices.registry import LEGACY_SQL
        for d in conn.execute(f"SELECT * FROM fingerprint_devices WHERE is_active = 1 AND {LEGACY_SQL}").fetchall():
            last = conn.execute('SELECT last_at FROM device_template_pulls WHERE device_id = ?', (d['id'],)).fetchone()
            if not force and last and now - (last[0] or 0) < TEMPLATES_EVERY:
                continue
            try:
                if d['is_adms']:
                    for bt in (1, 9):
                        payload = json.dumps({'Type': bt})
                        if not conn.execute("SELECT 1 FROM adms_commands WHERE device_id = ? AND status = 'PENDING' "
                                            "AND command_type = 'DATA QUERY BIODATA' AND payload = ?",
                                            (d['id'], payload)).fetchone():
                            conn.execute("INSERT INTO adms_commands (device_id, command_type, payload, status) "
                                         "VALUES (?, 'DATA QUERY BIODATA', ?, 'PENDING')", (d['id'], payload))
                            out['queued'] += 1
                elif (d['device_ip'] or '').strip():
                    from utils import device_access
                    r = device_access.pull_direct(conn, d)
                    out['templates'] += r.get('templates', 0)
                out['devices'] += 1
                conn.execute('INSERT OR REPLACE INTO device_template_pulls (device_id, last_at) VALUES (?, ?)',
                             (d['id'], now))
                conn.commit()
            except Exception as e:              # noqa: BLE001 — جهازٌ مقفول لا يوقف البقيّة
                out['errors'].append(f"{d['device_name']}: {str(e)[:100]}")
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return out


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
        txt = f"المستخدمون: أُضيف {int(users.get('employees_added') or 0)} موظّف"
        if users.get('adopted_punches'):
            txt += f"، ودخلت {int(users['adopted_punches'])} بصمة حضور كانت بانتظارهم"
        t = users.get('templates')
        if isinstance(t, dict) and (t.get('templates') or t.get('queued')):
            txt += f" — البصمات نفسها: {int(t.get('templates') or 0)} من أجهزة IP"
            if t.get('queued'):
                txt += f"، وطُلبت من {int(t['queued']) // 2 or 1} جهاز ADMS"
        return txt
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
    # والبصماتُ نفسُها مرّةً في اليوم (لا كلَّ ساعة: سحبُها يوقف الجهازَ ثوانيَ).
    if LOCK.acquire(blocking=False):
        try:
            t = run_templates()
            if isinstance(users, dict) and (t['templates'] or t['queued'] or t['errors']):
                users['templates'] = t
        except Exception:
            pass
        finally:
            LOCK.release()
    _set(SETTING_LAST, time.strftime('%Y-%m-%d %H:%M:%S'))
    _set(SETTING_LAST_RESULT, summary(punches, users))
    if punches is not None or users is not None:
        log_run(source, started, punches, users)
    try:
        from utils import cloud_sync
        cloud_sync.wake()
    except Exception:
        pass
    # تنبيهاتُ الوثائق قبل انتهائها — مرّةً في اليوم (utils/employee_lifecycle).
    try:
        from utils import employee_lifecycle
        employee_lifecycle.run_daily()
    except Exception:
        pass
    # ومع كلّ مزامنةٍ يُسأل عن طلبات البوّابة (تجهيزُ كشف، اعتمادُه) — لا من
    # داخل أمرٍ من البوّابة نفسها: ذاك يُبلغ نتيجتَه بنفسه.
    if source != 'portal':
        try:
            from utils import remote_commands
            remote_commands.poll_soon()
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
    return {'enabled': enabled(), 'templates': templates_enabled(),
            'last': str(last)[:16], 'result': _get(SETTING_LAST_RESULT, '') or '',
            'next': nxt, 'busy': busy(), 'history': history()}
