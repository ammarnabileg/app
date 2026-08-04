# -*- coding: utf-8 -*-
"""إصدار البرنامج — أربعة أجزاء: MAJOR.MINOR.PATCH.BUILD

## دلالة كل جزء

    MAJOR   إصدار رئيسي: نسخة جديدة بالكامل أو تغيير جذري قد لا يتوافق مع
            ما قبله (Breaking Change). الرقم 1 يعني أول نسخة مستقرّة جاهزة
            للاستخدام الفعلي.
    MINOR   ميزة جديدة متوافقة مع ما قبلها ولا تعطّل وظيفةً قائمة.
    PATCH   إصلاح خطأ أو ثغرة أمنية، بلا إضافة ميزة.
    BUILD   بناء أو إصلاح عاجل (Hotfix). يزيد مع كل بناء يُرفع للمراجعة.

مثال: 1.2.4.15 = الإصدار الرئيسي الأول، ميزتان مضافتان، أربعة إصلاحات،
والبناء الخامس عشر.

## لماذا لم يُبدأ من 1.0.0.0

كان الإصدار المعلن 2.5.0، والنزول إلى 1.0.0.0 يكسر التحديث التلقائي:
`check_for_updates` يقارن `remote > local`، فأي عميل يحمل 2.5.0 لن يرى
1.x.x.x تحديثًا أبدًا ويبقى عالقًا على نسخة قديمة بلا إشعار.

فبدأ الترقيم من 2.6.0.0: الجزء الرابع أُضيف، والرقم يتقدّم لا يتراجع.
ومن هنا فصاعدًا يُتّبع المعنى الموصوف أعلاه بدقّة.

## عند كل تغيير

يُحدَّث الرقم هنا ويُضاف سطر إلى CHANGELOG.md. القاعدة:

    ميزة جديدة   → ارفع MINOR وصفّر PATCH وBUILD
    إصلاح خطأ    → ارفع PATCH وصفّر BUILD
    بناء/عاجل    → ارفع BUILD وحده
"""

VERSION_MAJOR = 2
VERSION_MINOR = 8
VERSION_PATCH = 0
VERSION_BUILD = 0

CURRENT_VERSION = f"{VERSION_MAJOR}.{VERSION_MINOR}.{VERSION_PATCH}.{VERSION_BUILD}"

# تاريخ البناء — يُحدَّث مع BUILD
BUILD_DATE = "2026-08-04"

# اسم الإصدار الداخلي: يُذكر في الدعم الفني ليُعرف ما يحمله العميل
RELEASE_NAME = "كشف المقاولات"

COPYRIGHT_YEAR = "2026"
COPYRIGHT_OWNER = "ONZ"


def version_tuple():
    """الإصدار كصفٍّ للمقارنة الرقمية."""
    return (VERSION_MAJOR, VERSION_MINOR, VERSION_PATCH, VERSION_BUILD)


def version_info():
    """معلومات الإصدار كاملةً — للعرض وللدعم الفني."""
    return {
        'version': CURRENT_VERSION,
        'major': VERSION_MAJOR,
        'minor': VERSION_MINOR,
        'patch': VERSION_PATCH,
        'build': VERSION_BUILD,
        'build_date': BUILD_DATE,
        'release_name': RELEASE_NAME,
        'copyright': f"© {COPYRIGHT_YEAR} {COPYRIGHT_OWNER}",
    }
