# -*- coding: utf-8 -*-
"""أوامرُ بوّابة الشركة (onz.one/portal) إلى البرنامج في المقرّ.

## لماذا

صاحبُ الشركة لا يصل جهازَ البرنامج دائمًا: هو في سفر، أو الجهازُ في
الموقع. وكان اعتمادُ كشف الرواتب لا يتمّ إلا من الجهاز — فيبقى الكشفُ
مسودّةً، والبوّابةُ تقول «لم يصل كشفٌ بعد».

## ليست مزامنةً في اتجاهين

`cloud_sync` يرفع البيانات في اتجاهٍ واحد، والمقرُّ هو المرجع. وهذا لا
يغيّر ذلك: لا يكتب من السحابة بيانًا. يأخذ **طلبًا** («اعتمد كشفَ سبتمبر
كما رأيتُه»، «زامِن الأجهزة الآن») ويُنفّذه البرنامجُ بقواعده هو، ثم
يرفع النتيجةَ كأيّ تغييرٍ آخر. فلا تعارضَ يُحسم.

## والاعتمادُ لما رآه صاحبُه بالضبط

الطلبُ يحمل توقيعَ الكشف كما عُرض في البوّابة (`run_signature`). فإن
أعاد المحاسبُ حفظَ الكشف في المقرّ بعدها، تغيّر التوقيع ورُفض الطلب:
«الكشف تغيّر بعد ما اتعرض عليك». لا يُعتمد رقمٌ لم يره أحد.

## والقناة

يسأل البرنامجُ الخادمَ مع كلّ دورة رفعٍ (كلَّ دقيقتين): `POST <وجهة
الرفع>/commands` بالمفتاح نفسه، ومعه نتائجُ ما نُفّذ. لا منفذَ يُفتح في
المقرّ، ولا طلبَ يصله من الخارج.
"""
import json
import logging
import threading
import time

logger = logging.getLogger(__name__)

SETTING_REMOTE_APPROVAL = 'remote_payroll_approval_enabled'   # '1' افتراضًا
STATE_UNSUPPORTED_UNTIL = 'commands:unsupported_until'
STATE_LAST_POLL = 'commands:last_ok'
RETRY_UNSUPPORTED = 3600        # خادمٌ قديم بلا قناة: يُسأل كلَّ ساعة لا كلَّ دقيقتين
MIN_GAP = 30                    # الرفعُ يُعيد الدورةَ كلَّ ٥ ثوانٍ وفيه تراكم؛ السؤالُ لا يتبعه
_last_poll = [0.0]
TYPES = ('device_sync', 'approve_payroll', 'prepare_payroll')


def _now():
    return time.strftime('%Y-%m-%d %H:%M:%S')


def run_signature(run):
    """توقيعُ الكشف كما يراه الطرفان: المعرّف، ووقتُ آخر حفظ، وعددُ الموظّفين.

    إعادةُ الحفظ تُغيّر `created_at` (انظر `save_monthly_payroll`) — وهو ما
    يعني «أرقامٌ جديدة». والصافي لا يدخل: تنسيقُ الكسور يختلف بين PHP
    وبايثون عند الحدّ، فيُرفض اعتمادٌ صحيح لفرق تقريب."""
    return f"{int(run['id'])}|{str(run['created_at'] or '')[:19]}|{int(run['employees_count'] or 0)}"


def remote_approval_enabled():
    try:
        from utils.db import get_setting
        return str(get_setting(SETTING_REMOTE_APPROVAL, '1') or '1') not in ('0', 'false', 'False')
    except Exception:
        return True


# ------------------------------------------------------------ التنفيذ

def approve_payroll(conn, payload):
    """(ok, message). يعتمد الكشفَ كما عُرض — أو يقول لماذا لم يفعل."""
    from utils.payroll_engine import lock_run, post_to_ledger, RunLockedError
    if not remote_approval_enabled():
        return False, 'الاعتماد من البوّابة مقفول في إعدادات البرنامج بالمقرّ.'
    try:
        month, year = int(payload.get('month')), int(payload.get('year'))
        run_id = int(payload.get('run_id'))
    except (TypeError, ValueError):
        return False, 'طلبٌ ناقص.'
    run = conn.execute('SELECT * FROM payroll_runs WHERE id = ?', (run_id,)).fetchone()
    if not run or int(run['month']) != month or int(run['year']) != year:
        return False, 'الكشف ده مش موجود في البرنامج بالمقرّ (اتلغى أو اتحفظ من جديد).'
    if run['status'] == 'approved':
        return True, 'الكشف معتمد بالفعل.'
    if run_signature(run) != str(payload.get('signature') or ''):
        return False, ('الكشف اتعدّل في المقرّ بعد ما اتعرض عليك — راجع الأرقام الجديدة '
                       'في البوابة (بتظهر خلال دقايق) واعتمد تاني.')
    by = str(payload.get('by') or 'صاحب الشركة')[:80]
    try:
        lock_run(conn, month, year, None)
        prev_n = conn.execute('SELECT COUNT(*) FROM payroll_ledger_postings '
                              'WHERE month = ? AND year = ?', (month, year)).fetchone()[0]
        note = f'اعتُمد من بوّابة الشركة (onz.one) — {by}'
        if prev_n and run['unlock_reason']:
            note += f' · إعادة اعتماد بعد فكّ القفل: {run["unlock_reason"]}'
        post_to_ledger(conn, month, year, None, note)
        conn.commit()
    except RunLockedError:
        conn.rollback()
        return True, 'الكشف معتمد بالفعل.'
    except Exception as e:                      # noqa: BLE001
        conn.rollback()
        return False, f'تعذّر الاعتماد: {str(e)[:160]}'
    return True, f'اتعتمد كشف {month}/{year} في البرنامج بالمقرّ.'


def prepare_payroll(conn, payload):
    """(ok, message). «جهّز كشف الشهر» من البوّابة: ما كان يفعله المحاسبُ على الجهاز.

    ١) يُثبّت الساعاتِ كما حسبها المحرّك لكلّ موظّفٍ لم تُثبَّت ساعاتُه (أو تغيّرت
       بعد تثبيتها) — والمثبَّتُ في المقرّ لا يُمسّ. ويُقيَّد أنّه من البوّابة.
    ٢) يحفظ الكشفَ **مسودّةً**، فتُرفع وتُراجَع في البوّابة ثم تُعتمد.

    لا تصحيحَ هنا: العذرُ والإجازةُ والبصمةُ اليدويّة تبقى في المقرّ. ما لم يُصحَّح
    يُحسب كما هو — ويراه صاحبُ الشركة في المسودّة يومًا بيوم قبل أن يعتمد.
    """
    from utils.payroll_engine import (attest_hours, compute_monthly_payroll, is_period_locked,
                                      save_monthly_payroll, RunLockedError, HoursNotApprovedError)
    if not remote_approval_enabled():
        return False, 'تجهيز الكشف واعتماده من البوّابة مقفول في إعدادات البرنامج بالمقرّ.'
    try:
        month, year = int(payload.get('month')), int(payload.get('year'))
        if not (1 <= month <= 12 and 2000 <= year <= 2100):
            raise ValueError
    except (TypeError, ValueError):
        return False, 'طلبٌ ناقص.'
    by = str(payload.get('by') or 'صاحب الشركة')[:80]
    run = conn.execute('SELECT status FROM payroll_runs WHERE month = ? AND year = ?',
                       (month, year)).fetchone()
    if run and run['status'] == 'approved':
        return False, f'كشف {month}/{year} معتمد بالفعل — لتعديله لازم يتفكّ قفله من البرنامج في المقرّ.'
    if is_period_locked(conn, month, year):
        return False, f'فترة {month}/{year} مقفولة في البرنامج بالمقرّ.'
    try:
        data = compute_monthly_payroll(conn, month, year)
        todo = [{'employee_id': r['employee_id'], 'notes': f'ثُبّتت من بوّابة الشركة — {by}'}
                for r in data['rows'] if r['hours_state'] != 'approved']
        if todo:
            attest_hours(conn, month, year, todo, None)
        result = save_monthly_payroll(conn, month, year, None)
        conn.commit()
    except RunLockedError:
        conn.rollback()
        return False, f'كشف {month}/{year} معتمد بالفعل.'
    except HoursNotApprovedError as e:
        conn.rollback()
        return False, f'تعذّر تثبيت ساعات {len(e.missing)} موظّف — راجعهم في البرنامج بالمقرّ.'
    except Exception as e:                      # noqa: BLE001
        conn.rollback()
        return False, f'تعذّر تجهيز الكشف: {str(e)[:160]}'
    row = conn.execute('SELECT employees_count, total_net FROM payroll_runs WHERE month = ? AND year = ?',
                       (month, year)).fetchone()
    n = int(row['employees_count'] or 0) if row else len(data['rows'])
    net = float(row['total_net'] or 0) if row else 0.0
    kept = n - len(todo)
    msg = (f'اتجهّز كشف {month}/{year} مسودّة — {n} موظّف، الصافي {net:,.3f}. '
           f'راجعه في البوابة واعتمده.')
    if kept > 0:
        msg += f' ({kept} موظّف ساعاتهم كانت متثبّتة في المقرّ واتسابت زي ما هي.)'
    return True, msg


def _prepare(command_id, payload):
    """في خيطٍ مستقلّ: البصماتُ من الأجهزة أوّلًا (ليُحسب الشهرُ بآخرها)، ثم التجهيز."""
    from utils import device_autosync
    try:
        if device_autosync._has_active_devices():
            device_autosync.run_once(source='portal')
    except Exception as e:                      # noqa: BLE001 — جهازٌ لا يردّ لا يمنع الكشف
        logger.warning(f'prepare: device sync failed: {e}')
    from utils.db import get_db_connection
    c = get_db_connection()                     # خيطٌ بلا سياق Flask: اتصالٌ خاصّ به
    try:
        ok, msg = prepare_payroll(c, payload)
    except Exception as e:                      # noqa: BLE001
        ok, msg = False, f'تعذّر تجهيز الكشف: {str(e)[:160]}'
    finally:
        c.close()
    _finish(command_id, ok, msg)
    try:
        from utils import cloud_sync
        cloud_sync.wake()
    except Exception:
        pass


def _finish(command_id, ok, message):
    import sqlite3
    from utils.db import DB_PATH
    c = sqlite3.connect(DB_PATH)
    try:
        c.execute('UPDATE remote_commands SET done_at = ?, ok = ?, message = ?, reported = 0 '
                  'WHERE command_id = ?', (_now(), 1 if ok else 0, str(message)[:400], command_id))
        c.commit()
    finally:
        c.close()


def _device_sync(command_id):
    from utils import device_autosync
    try:
        punches, users = device_autosync.run_once(source='portal')
        if punches is None and users is None:
            _finish(command_id, True, 'كانت مزامنةٌ جارية بالفعل — نتيجتُها في السجلّ.')
            return
        bad = [x for x in (punches, users) if isinstance(x, dict) and x.get('success') is False]
        _finish(command_id, not bad, device_autosync.summary(punches, users))
    except Exception as e:                      # noqa: BLE001
        _finish(command_id, False, f'تعذّرت المزامنة: {str(e)[:160]}')
    try:
        from utils import cloud_sync
        cloud_sync.wake()
    except Exception:
        pass


def execute(conn, cmd, just_reported=()):
    """يُسجّل الأمر ويُنفّذه مرّةً واحدة. أمرٌ سبق استلامُه لا يُعاد (الخادم
    يُعيد إرسالَ ما لم تصله نتيجتُه)."""
    cid = str(cmd.get('id') or '')[:64]
    ctype = str(cmd.get('type') or '')
    payload = cmd.get('payload') if isinstance(cmd.get('payload'), dict) else {}
    if not cid:
        return
    if conn.execute('SELECT 1 FROM remote_commands WHERE command_id = ?', (cid,)).fetchone():
        # استُلم من قبل: لا يُنفَّذ ثانيةً. وتُعاد نتيجتُه في الإبلاغ القادم إن
        # كانت قد تمّت — إلّا ما أُبلغ في هذا الطلب نفسه (ردُّه سبق استلامَها).
        if cid in just_reported:
            return
        conn.execute('UPDATE remote_commands SET reported = 0 WHERE command_id = ? AND done_at IS NOT NULL',
                     (cid,))
        conn.commit()
        return
    conn.execute('INSERT INTO remote_commands (command_id, type, payload, received_at) VALUES (?, ?, ?, ?)',
                 (cid, ctype, json.dumps(payload, ensure_ascii=False)[:2000], _now()))
    conn.commit()

    if ctype == 'approve_payroll':
        ok, msg = approve_payroll(conn, payload)
        _finish(cid, ok, msg)
    elif ctype == 'prepare_payroll':
        threading.Thread(target=_prepare, args=(cid, payload), daemon=True).start()
    elif ctype == 'device_sync':
        # دقائق مع الأجهزة: في خيطٍ مستقلّ، فلا يتوقّف الرفعُ أثناءها.
        threading.Thread(target=_device_sync, args=(cid,), daemon=True).start()
    else:
        _finish(cid, False, 'أمرٌ لا يعرفه هذا الإصدار — حدّث البرنامج.')


def pending_results(conn, limit=50):
    rows = conn.execute('SELECT command_id, ok, message, done_at FROM remote_commands '
                        'WHERE reported = 0 AND done_at IS NOT NULL ORDER BY id LIMIT ?',
                        (limit,)).fetchall()
    return [{'id': r[0], 'ok': bool(r[1]), 'message': r[2] or '', 'done_at': r[3]} for r in rows]


# ------------------------------------------------------------ القناة

def commands_url(sync_url):
    return (sync_url or '').rstrip('/') + '/commands'


def poll(conn=None, session=None, force=False):
    """سؤالٌ واحد للخادم: نتائجُ ما نُفّذ، وأوامرُ جديدة. لا يُطلق استثناءً."""
    from utils import cloud_sync, cloud_outbox as ob
    cfg = cloud_sync._settings()
    if not cfg['enabled'] or not (cfg['url'] and cfg['client_id'] and cfg['api_key']):
        return {'skipped': 'disabled'}
    if not force and session is None and time.time() - _last_poll[0] < MIN_GAP:
        return {'skipped': 'recent'}
    _last_poll[0] = time.time()

    owned = conn is None
    if owned:
        from utils.db import get_db_connection
        conn = get_db_connection()
    try:
        until = float(ob.get_state(conn, STATE_UNSUPPORTED_UNTIL, 0) or 0)
        if until and time.time() < until:
            return {'skipped': 'unsupported'}

        results = pending_results(conn)
        import requests
        http = session or requests
        try:
            resp = http.post(commands_url(cfg['url']),
                             json={'client_id': cfg['client_id'], 'results': results,
                                   'agent': _agent_version()},
                             headers={'X-API-KEY': cfg['api_key'],
                                      'Content-Type': 'application/json'},
                             timeout=30)
        except Exception as e:                  # noqa: BLE001
            return {'ok': False, 'error': str(e)[:160]}
        if resp.status_code in (404, 405):
            # خادمٌ أقدم من القناة (v103): لا خطأ، ولا إلحاح.
            ob.set_state(conn, STATE_UNSUPPORTED_UNTIL, str(time.time() + RETRY_UNSUPPORTED))
            return {'skipped': 'unsupported'}
        if resp.status_code != 200:
            return {'ok': False, 'status': resp.status_code}
        try:
            body = resp.json() or {}
        except Exception:
            body = {}
        if body.get('status') != 'success':
            return {'ok': False, 'error': str(body.get('error') or '')[:160]}

        if results:
            marks = ', '.join('?' for _ in results)
            conn.execute(f'UPDATE remote_commands SET reported = 1 WHERE command_id IN ({marks})',
                         [r['id'] for r in results])
            conn.commit()
        ob.set_state(conn, STATE_LAST_POLL, _now())

        features = body.get('features') if isinstance(body.get('features'), list) else []
        if 'payroll_drafts' in features:
            ob.enable_drafts(conn)

        done = 0
        for cmd in (body.get('commands') or [])[:20]:
            if isinstance(cmd, dict):
                try:
                    execute(conn, cmd, {r['id'] for r in results})
                    done += 1
                except Exception as e:          # noqa: BLE001 — أمرٌ واحد لا يُسقط البقيّة
                    logger.warning(f'remote command failed: {e}')
        return {'ok': True, 'reported': len(results), 'received': done}
    finally:
        if owned:
            try:
                conn.close()
            except Exception:
                pass


def _agent_version():
    try:
        from utils.version_info import CURRENT_VERSION
        return CURRENT_VERSION
    except Exception:
        return ''


def recent(limit=10):
    """للشاشة: آخرُ ما طُلب من البوّابة."""
    try:
        import sqlite3
        from utils.db import DB_PATH
        c = sqlite3.connect(DB_PATH)
        c.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in c.execute(
                'SELECT * FROM remote_commands ORDER BY id DESC LIMIT ?', (int(limit),))]
        finally:
            c.close()
    except Exception:
        return []
