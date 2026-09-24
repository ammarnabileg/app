# -*- coding: utf-8 -*-
"""حدّ الموظفين من الاشتراك.

## من أين يأتي الحدّ

من **الرخصة الموقَّعة** (`max_employees`) — لا من إعدادٍ محليّ. الإعداد
المحليّ يُغيَّر بصفٍّ واحد في قاعدة بيانات العميل؛ الرمز موقَّعٌ بمفتاحنا
الخاص ويتجدّد مع كل تحقّقٍ ناجح (كل ساعة)، فزيادةُ العدد في «اشتراكي»
تصل النظامَ خلال ساعة بلا أيّ خطوة من العميل.

ولا حدَّ حين لا يحمل الرمزُ الحقل: الرخص القديمة (قبل الاشتراكات) لا
حدّ لها، ولا يجوز أن يُقفل تحديثٌ على عميلٍ اشترى بلا حدّ.

## ما يُمنع وما لا يُمنع

* **يُمنع التفعيل فقط**: إضافةُ موظفٍ نشط، أو إعادةُ تفعيل موظف، أو
  استيرادُ موظفين جدد — فوق الحدّ + الهامش.
* **لا يُمنع شيء قائم**: القراءة والتعديل والحضور والرواتب تعمل كما هي،
  ولو كان العدد فوق الحدّ (نزل الاشتراك مثلًا). الحدّ لا يُقفل على بيانات
  أحد.
* **الأجهزة لا تُرفض**: مستخدمٌ جديد على جهاز البصمة يُضاف **غيرَ نشط**
  فوق الحدّ — فبصماته لا تضيع، ويفعّله المسؤول بعد زيادة العدد.

## الهامش

10% فوق الحدّ (مقرَّبًا لأعلى) قبل المنع: موظفٌ جديد يوم الأحد لا ينتظر
تجديد الاشتراك. 25 ← 28، 10 ← 11، 40 ← 44.
"""

SUBSCRIPTION_URL = 'https://onz.one/client/subscription'


def hard_cap(cap):
    """الحدّ مع الهامش: cap + 10% مقرَّبةً لأعلى."""
    return cap + (cap + 9) // 10


def employee_cap():
    """الحدّ من الرخصة الموقَّعة — أو None (بلا حدّ).

    لا يرمي: الحدّ لا يجوز أن يُسقط صفحة الموظفين.
    """
    try:
        from utils.license import get_saved_license_key, signed_license_for
        r = signed_license_for(get_saved_license_key())
        if not r:
            return None
        v = (r.get('data') or {}).get('max_employees')
        if v is None:
            return None
        v = int(v)
        return v if v > 0 else None
    except Exception as e:
        print(f"[limits] تعذّر قراءة حدّ الموظفين: {e}")
        return None


def active_count(conn):
    return conn.execute(
        'SELECT COUNT(*) FROM employees WHERE is_active = 1').fetchone()[0]


_UNSET = object()


def usage(conn, cap=_UNSET):
    """{cap, hard, used, over, blocked} — cap=None يعني بلا حدّ.

    `cap` يُمرَّر حين يكون `conn` في معاملة كتابة مفتوحة على اتصالٍ خاصّ
    (مزامنة الأجهزة): قراءةُ الرخصة تفتح اتصالًا آخر **يكتب**
    (`init_license_table`)، فتنتظر قفلَ ذلك الاتصال حتى المهلة ثم تفشل —
    فيضيع الحدّ ببطء. فيُقرأ الحدّ مرّةً قبل المعاملة ويُمرَّر.
    """
    if cap is _UNSET:
        cap = employee_cap()
    used = active_count(conn)
    if cap is None:
        return {'cap': None, 'hard': None, 'used': used,
                'over': False, 'blocked': False}
    hard = hard_cap(cap)
    return {'cap': cap, 'hard': hard, 'used': used,
            'over': used > cap, 'blocked': used >= hard}


def can_activate(conn, n=1, cap=_UNSET):
    """أيُسمح بتفعيل n موظفين آخرين؟ (ok, usage)."""
    u = usage(conn, cap)
    if u['cap'] is None:
        return True, u
    return u['used'] + n <= u['hard'], u


def blocked_message(u):
    from flask_babel import gettext
    return gettext('x.f_employee_limit_reached') % {
        'cap': u['cap'], 'used': u['used'], 'url': SUBSCRIPTION_URL}
