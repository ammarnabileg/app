# -*- coding: utf-8 -*-
"""ساعة أجهزة ADMS: إيه اللي بنبعته لها، وقد إيه متقدّمة أو متأخّرة.

## المشكلة (أكتوبر 2026، جهاز AJE1262000048)

أمر الضبط `SET OPTIONS DateTime=<ثواني>` كان بيبعت `datetime.now().timestamp()`، وده توقيت غرينتش
محسوب من منطقة ويندوز. والجهاز بيضيف عليه المنطقة الزمنية **اللي جوه الجهاز نفسه**. فلو الجهاز بيضيف
مقدار غير اللي متوقّع (+4، أو توقيت صيفي في الفيرموير)، ساعته بتطلع ساعة قدّام، وكل بصماته بتتسجّل
بساعة زيادة — حتى والسيرفر وويندوز متظبطين.

## الحل

إعداد «المنطقة الزمنية» (`device_tz_offset_minutes`، الافتراضي منطقة الشركة = +3 للكويت) — وهو توقيت
الشركة، مش حاجة بنلفّ بيها على جهاز.
الرقم المبعوث = **الساعة الظاهرة على السيرفر** − المنطقة − **تعويض الجهاز**. فالجهاز بيطلع بنفس ساعة
السيرفر، مستقلًّا عن إعداد ويندوز.

**تعويض كل جهاز لوحده (تلقائي):** جهاز فيرمويره بيضيف أكتر من المتوقّع (توقيت صيفي مثلًا) بيفضل ساعة
قدّام بعد كل ضبط. فلو جه منه حدث «في المستقبل» **بعد** ضبط وقت أكّده الجهاز (فمش ضبط يدوي)، الفرق
يتحفظ تعويضًا للجهاز ده بس (`clock_correction_seconds`) ويتبعتله ضبط جديد على طول. الإعداد يفضل +3،
والأجهزة السليمة ما تتأثرش.

ومع كل حدث لحظي بيبعته الجهاز (بصمة/عملية) بنقيس الفرق بين وقت الحدث ووقت وصوله. حدث «في المستقبل»
= ساعة الجهاز متقدّمة أكيد (مفيش حدث بيحصل قبل ما يحصل)، فبيتسجّل ويظهر في الإعدادات.
"""
import calendar
import re
from datetime import datetime

SETTING = 'device_tz_offset_minutes'
SKEW_MIN_SECONDS = 120          # أقل من دقيقتين = تأخير شبكة/تقريب، مش ساعة غلط
_DT = re.compile(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})')


def company_offset_minutes(conn=None):
    """منطقة الشركة (system_settings.timezone، الافتراضي Asia/Kuwait) بالدقائق في اللحظة دي."""
    tz = 'Asia/Kuwait'
    try:
        if conn is None:
            from utils.db import get_db_connection
            conn = get_db_connection()
        r = conn.execute('SELECT timezone FROM system_settings LIMIT 1').fetchone()
        if r and r[0]:
            tz = r[0]
    except Exception:
        pass
    try:
        from zoneinfo import ZoneInfo
        off = datetime.now(ZoneInfo(tz)).utcoffset()
        return int(off.total_seconds() // 60)
    except Exception:
        return 180


def offset_minutes(conn=None):
    """المنطقة الزمنية المتظبطة على الأجهزة (دقائق شرق غرينتش)."""
    try:
        from utils.db import get_setting
        raw = get_setting(SETTING, '')
        if raw not in (None, ''):
            v = int(float(raw))
            if -14 * 60 <= v <= 14 * 60:
                return v
    except Exception:
        pass
    return company_offset_minutes(conn)


def correction_seconds(conn, device_id):
    if not device_id:
        return 0
    try:
        ensure_schema(conn)
        r = conn.execute('SELECT clock_correction_seconds FROM fingerprint_devices WHERE id = ?',
                         (device_id,)).fetchone()
        return int(r[0] or 0) if r else 0
    except Exception:
        return 0


def device_epoch(now=None, offset=None, device_id=None, conn=None):
    """الرقم اللي بيتبعت في `SET OPTIONS DateTime=` — بحيث ساعة الجهاز = ساعة السيرفر الظاهرة."""
    now = now or datetime.now()
    offset = offset_minutes(conn) if offset is None else offset
    corr = 0
    if device_id:
        if conn is None:
            from utils.db import get_db_connection
            conn = get_db_connection()
        corr = correction_seconds(conn, device_id)
    return calendar.timegm(now.timetuple()) - offset * 60 - corr


def label(minutes):
    sign = '+' if minutes >= 0 else '-'
    m = abs(int(minutes))
    return f'GMT{sign}{m // 60}' + (f':{m % 60:02d}' if m % 60 else '')


# ------------------------------------------------------------ قياس فرق الساعة

def ensure_schema(conn):
    cols = {r[1] for r in conn.execute('PRAGMA table_info(fingerprint_devices)')}
    for c, t in (('clock_skew_seconds', 'INTEGER'), ('clock_checked_at', 'TEXT'),
                 ('clock_correction_seconds', 'INTEGER DEFAULT 0')):
        if c not in cols:
            conn.execute(f'ALTER TABLE fingerprint_devices ADD COLUMN {c} {t}')


def record_skew(conn, device_id, raw_body, now=None):
    """من سطور ATTLOG/OPERLOG اللي لسه واصلة: أحدث وقت فيها ناقص وقت الوصول.

    بيتسجّل بس لو الحدث «في المستقبل» بأكتر من دقيقتين (ساعة الجهاز متقدّمة أكيد)، أو بيتصفّر لو
    الحدث لحظي ومظبوط (آخر حدث خلال دقيقتين). البصمات القديمة المتأخرة في الرفع ما بتأثرش. لا يرمي.
    """
    if not device_id or not raw_body:
        return None
    try:
        times = [datetime.strptime(t, '%Y-%m-%d %H:%M:%S') for t in _DT.findall(raw_body)]
        if not times:
            return None
        now = now or datetime.now()
        skew = int((max(times) - now).total_seconds())
        if skew > SKEW_MIN_SECONDS:
            value = skew
        elif abs(skew) <= SKEW_MIN_SECONDS:
            value = 0
        else:
            return None                  # رفع متأخر لبصمات قديمة — مش مقياس للساعة
        ensure_schema(conn)
        conn.execute('UPDATE fingerprint_devices SET clock_skew_seconds = ?, clock_checked_at = ? WHERE id = ?',
                     (value, now.strftime('%Y-%m-%d %H:%M:%S'), device_id))
        if value:
            learn_correction(conn, device_id, value)
        return value
    except Exception:
        return None


STEP = 900                      # التعويض بخطوات ربع ساعة (المناطق والتوقيت الصيفي كلها مضاعفاتها)


def learn_correction(conn, device_id, skew):
    """الجهاز متقدّم بعد ضبطٍ أكّده ← الفرق تعويضٌ له، وضبطٌ جديد على طول. يُرجع التعويض الجديد أو None.

    مش بيتعلّم لو: مفيش ضبط وقت اتأكّد خلال ٢٤ ساعة (يبقى الفرق ممكن ضبط يدوي)، أو فيه ضبط لسه
    منتظر (التعويض اللي قبله لسه ما اتطبّقش).
    """
    step = int(round(skew / STEP)) * STEP
    if not step:
        return None
    confirmed = conn.execute(
        "SELECT 1 FROM adms_commands WHERE device_id = ? AND command_type = 'SET TIME' AND status = 'OK' "
        "AND updated_at >= datetime('now', '-1 day')", (device_id,)).fetchone()
    pending = conn.execute(
        "SELECT 1 FROM adms_commands WHERE device_id = ? AND command_type = 'SET TIME' "
        "AND status IN ('PENDING', 'SENT') AND created_at >= datetime('now', '-1 day')", (device_id,)).fetchone()
    if not confirmed or pending:
        return None
    new = correction_seconds(conn, device_id) + step
    conn.execute('UPDATE fingerprint_devices SET clock_correction_seconds = ? WHERE id = ?', (new, device_id))
    import json
    conn.execute("INSERT INTO adms_commands (device_id, command_type, payload, status) VALUES (?, 'SET TIME', ?, 'PENDING')",
                 (device_id, json.dumps(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))))
    return new


def reset_correction(conn, device_id=None):
    ensure_schema(conn)
    if device_id:
        conn.execute('UPDATE fingerprint_devices SET clock_correction_seconds = 0 WHERE id = ?', (device_id,))
    else:
        conn.execute('UPDATE fingerprint_devices SET clock_correction_seconds = 0')
    conn.commit()


def correction_label(seconds):
    if not seconds:
        return '—'
    m = round(abs(seconds) / 60)
    h, mm = divmod(m, 60)
    span = (f'{h} ساعة' + (f' و{mm} دقيقة' if mm else '')) if h else f'{m} دقيقة'
    return ('−' if seconds > 0 else '+') + span


def skew_label(seconds):
    if seconds is None:
        return '—'
    if abs(seconds) <= SKEW_MIN_SECONDS:
        return 'مظبوط'
    m = round(abs(seconds) / 60)
    h, mm = divmod(m, 60)
    span = (f'{h} ساعة' + (f' و{mm} دقيقة' if mm else '')) if h else f'{m} دقيقة'
    return ('متقدّم ' if seconds > 0 else 'متأخر ') + span
