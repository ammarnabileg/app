"""ما هذا التركيب، وبأي نسخة يعمل؟

اللوحة تدير عملاء من نوعين لا نوعٍ واحد، وطريق تحديثهما مختلف
تمامًا:

  * **سحابة** — حاوية على خادمنا وفّرتها اللوحة. لا تُحدِّث نفسها:
    تُعاد بناؤها من وسمٍ في git. فالتحديث فعلٌ تفعله اللوحة عبر
    Coolify، لا شيء يفعله النظام بنفسه.

  * **محلّي** — برنامج مُركَّب على جهاز العميل. يُحدِّث نفسه:
    ينزّل حزمة، ويتحقّق من بصمتها، ويبدّل ملفاته (`tools/updater.py`).

ولهذا يُبلَّغ النوع مع النسخة: لوحةٌ لا تعرف نوع التركيب لا تعرف
أيّ زرّ تعرض — «أعد النشر» أم «ادفع حزمة».

**ولا يُرسَل من هنا شيء يخصّ الخصوصية.** النسخة والنوع ومعرّف
البناء — لا أسماء موظفين ولا بيانات. وهذا مقصود: القناة تُشخِّص لا
تُراقب.
"""

import os


KIND_CLOUD = 'cloud'
KIND_LOCAL = 'local'


def in_container():
    """أداخل حاوية نحن؟

    `/.dockerenv` يضعه Docker في كل حاوية. وليس دليلًا قاطعًا على
    أن اللوحة وفّرتها — لذلك يُقرأ معه متغيّر اللوحة.
    """
    try:
        return os.path.exists('/.dockerenv')
    except Exception:
        return False


def provisioned_by_panel():
    """أوفّرت اللوحةُ هذا التركيب؟

    اللوحة تمرّر `HR_ADMIN_USERNAME` حين تُنشئ المستأجر. وغيابه يعني
    تركيبًا يدويًّا حتى لو كان في حاوية — ومن ركّب بنفسه لا تُعيد
    اللوحة نشره.
    """
    return bool((os.environ.get('HR_ADMIN_USERNAME') or '').strip())


def kind():
    """`cloud` أو `local`.

    الحاوية التي وفّرتها اللوحة سحابة. وما عداها محلّي — ومن ركّب
    حاويةً بنفسه يُعامَل معاملة المحلّي، وهو الصواب: اللوحة لا تملك
    إعادة نشره.
    """
    return KIND_CLOUD if (in_container() and provisioned_by_panel()) else KIND_LOCAL


def report():
    """ما يُبلَّغ إلى اللوحة مع فحص الترخيص.

    يُبقى صغيرًا ومسطَّحًا عمدًا: يُرسَل مع كل فحص دوري، ويُخزَّن
    في صفّ العميل.
    """
    from utils.version_info import CURRENT_VERSION, BUILD_DATE

    out = {
        'version': CURRENT_VERSION,
        'build_date': BUILD_DATE,
        'deployment': kind(),
    }

    # نسخة المخطَّط: عميلٌ نسخته حديثة ومخطَّطه قديم يعني ترحيلًا لم
    # يُشتغَّل — وهي حالٌ لا تظهر في رقم النسخة وحده.
    try:
        from utils.db import SCHEMA_VERSION
        out['schema'] = SCHEMA_VERSION
    except Exception:
        pass

    try:
        out.update(health())
    except Exception:
        pass

    return out


# مفاتيحُ «صحّة الشركة» — أرقامٌ وأوقات فقط، لا اسمَ ولا بيانَ موظّف.
HEALTH_KEYS = ('employees_active', 'devices_active', 'devices_silent', 'last_punch_at', 'db_mb', 'disk_free_mb',
               'last_backup_at', 'offsite_backup_at', 'offsite_backup_ok', 'connectors_silent',
               'connector_pending', 'last_sync_error')


def health(now=None):
    """ما تعرضه اللوحة في «صحّة الشركات» (خارطة الطريق، المرحلة ١).

    جهازٌ «ساكت» = نشطٌ ولم يتّصل/يُسحب منه شيءٌ منذ ٢٤ ساعة. وكلُّ بندٍ مستقلّ:
    تعثُّر واحد لا يُسقط الباقي ولا فحصَ الترخيص.
    """
    import os
    import shutil
    from datetime import datetime, timedelta
    from utils.db import DB_PATH, get_db_connection, get_setting

    now = now or datetime.now()
    day_ago = (now - timedelta(hours=24)).strftime('%Y-%m-%d %H:%M:%S')
    out = {}
    conn = get_db_connection()

    def _try(fn):
        try:
            fn()
        except Exception:
            pass

    _try(lambda: out.__setitem__('employees_active', conn.execute(
        'SELECT COUNT(*) FROM employees WHERE COALESCE(is_active, 1) = 1').fetchone()[0]))

    def _devices():
        rows = conn.execute('SELECT last_activity, last_sync_time FROM fingerprint_devices WHERE is_active = 1').fetchall()
        out['devices_active'] = len(rows)
        out['devices_silent'] = sum(1 for r in rows if max(str(r[0] or ''), str(r[1] or '')) < day_ago)
    _try(_devices)
    _try(lambda: out.__setitem__('last_punch_at', conn.execute(
        # آخر ١٠٠٠ صفّ بالترتيب (rowid) لا الجدول كلّه: check_time بلا فهرس، والتقرير مع كل فحص ترخيص.
        'SELECT MAX(check_time) FROM (SELECT check_time FROM attendance_records ORDER BY id DESC LIMIT 1000)'
    ).fetchone()[0]))
    _try(lambda: out.__setitem__('db_mb', round(os.path.getsize(DB_PATH) / 1048576, 1)))
    _try(lambda: out.__setitem__('disk_free_mb', int(shutil.disk_usage(os.path.dirname(DB_PATH)).free / 1048576)))

    def _backup():
        from utils import backup
        latest = backup.list_auto_backups()
        out['last_backup_at'] = latest[0]['when'] if latest else None
        out['offsite_backup_at'] = get_setting('backup_offsite_last_at', '') or None
        out['offsite_backup_ok'] = (get_setting('backup_offsite_last_ok', '') or None) == '1' \
            if get_setting('backup_offsite_last_at', '') else None
    _try(_backup)

    def _connectors():
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'connectors'").fetchone():
            return
        out['connectors_silent'] = conn.execute(
            "SELECT COUNT(*) FROM connectors WHERE is_active = 1 AND COALESCE(last_seen, '') < ?",
            ((now - timedelta(minutes=30)).strftime('%Y-%m-%d %H:%M:%S'),)).fetchone()[0]
        out['connector_pending'] = conn.execute(
            "SELECT COUNT(*) FROM connector_commands WHERE status IN ('pending', 'sent')").fetchone()[0]
    _try(_connectors)

    def _sync():
        # آخر مزامنة أجهزة: لو فشلت، رسالتها (أعطال أجهزة لا بيانات موظفين).
        r = conn.execute('SELECT ok, message FROM device_sync_log ORDER BY id DESC LIMIT 1').fetchone()
        out['last_sync_error'] = (str(r[1] or 'فشلت')[:200]) if r and not r[0] else None
    _try(_sync)
    return out
