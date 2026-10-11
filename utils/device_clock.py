# -*- coding: utf-8 -*-
"""ساعة أجهزة ADMS: إيه اللي بنبعته لها، وقد إيه متقدّمة أو متأخّرة.

## المشكلة (أكتوبر 2026، جهاز AJE1262000048)

أمر الضبط `SET OPTIONS DateTime=<ثواني>` كان بيبعت `datetime.now().timestamp()`، وده توقيت غرينتش
محسوب من منطقة ويندوز. والجهاز بيضيف عليه المنطقة الزمنية **اللي جوه الجهاز نفسه**. فلو الجهاز بيضيف
مقدار غير اللي متوقّع (+4، أو توقيت صيفي في الفيرموير)، ساعته بتطلع ساعة قدّام، وكل بصماته بتتسجّل
بساعة زيادة — حتى والسيرفر وويندوز متظبطين.

## الحل

إعداد «المنطقة الزمنية على الأجهزة» (`device_tz_offset_minutes`، الافتراضي من منطقة الشركة = +3 للكويت).
الرقم المبعوث = **الساعة الظاهرة على السيرفر** − المنطقة دي. فالجهاز (ساعة الجهاز = الرقم + منطقته) بيطلع
بنفس ساعة السيرفر بالظبط، وده مستقل عن إعداد ويندوز. ولو جهاز بيضيف +4 فعلًا: الإعداد يبقى +4.

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


def device_epoch(now=None, offset=None):
    """الرقم اللي بيتبعت في `SET OPTIONS DateTime=` — بحيث ساعة الجهاز = ساعة السيرفر الظاهرة."""
    now = now or datetime.now()
    offset = offset_minutes() if offset is None else offset
    return calendar.timegm(now.timetuple()) - offset * 60


def label(minutes):
    sign = '+' if minutes >= 0 else '-'
    m = abs(int(minutes))
    return f'GMT{sign}{m // 60}' + (f':{m % 60:02d}' if m % 60 else '')


# ------------------------------------------------------------ قياس فرق الساعة

def ensure_schema(conn):
    cols = {r[1] for r in conn.execute('PRAGMA table_info(fingerprint_devices)')}
    for c, t in (('clock_skew_seconds', 'INTEGER'), ('clock_checked_at', 'TEXT')):
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
        return value
    except Exception:
        return None


def skew_label(seconds):
    if seconds is None:
        return '—'
    if abs(seconds) <= SKEW_MIN_SECONDS:
        return 'مظبوط'
    m = round(abs(seconds) / 60)
    h, mm = divmod(m, 60)
    span = (f'{h} ساعة' + (f' و{mm} دقيقة' if mm else '')) if h else f'{m} دقيقة'
    return ('متقدّم ' if seconds > 0 else 'متأخر ') + span
