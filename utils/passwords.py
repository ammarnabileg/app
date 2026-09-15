# -*- coding: utf-8 -*-
"""التحقّق من كلمة المرور بأي من الصيغتين اللتين تظهران في قاعدة المستأجر.

صيغتان لا واحدة، لأن للحسابات مصدرين:

  * ما يُنشئه النظام نفسه بـ werkzeug  →  'pbkdf2:sha256:...' أو 'scrypt:...'
  * ما يصل من لوحة onz.one            →  '$2y$...' (bcrypt من PHP)

الثانية هي ما يجعل العميل يدخل نظامه بنفس اسم المستخدم وكلمة المرور
اللذين يدخل بهما الموقع: اللوحة تمرّر البصمة المخزَّنة عندها كما هي، فلا
تخرج كلمة المرور الصريحة منها إطلاقًا — لا في متغيّر بيئة ولا في سجلّ
ولا في قاعدة.

و`check_password_hash` من werkzeug لا تفهم '$2y$' — ترمي استثناءً عند
رؤيتها. فلو مُرِّرت بصمة اللوحة إليها وحدها لرُفض الدخول الصحيح دائمًا.
"""
from werkzeug.security import check_password_hash

# بادئات bcrypt الثلاث. $2y$ ما يُصدره PHP، و$2b$ ما تُصدره مكتبة بايثون،
# و$2a$ قديمة — والخوارزمية واحدة في الثلاث.
_BCRYPT_PREFIXES = ('$2y$', '$2b$', '$2a$')

# ما تُصدره werkzeug. الصيغة: '<خوارزمية>[:معاملات]$<ملح>$<بصمة>'.
_WERKZEUG_PREFIXES = ('pbkdf2:', 'scrypt:', 'argon2', 'sha256$', 'sha1$', 'md5$')


def is_bcrypt(stored: str) -> bool:
    """هل البصمة المخزَّنة بصيغة bcrypt؟"""
    return isinstance(stored, str) and stored.startswith(_BCRYPT_PREFIXES)


def looks_hashed(stored) -> bool:
    """هل القيمة المخزَّنة بصمةٌ لا كلمةً صريحة؟

    هذا هو السؤال الذي يفصل بين حسابٍ قديم بكلمة صريحة — وهو موجود فعلًا
    عند عملاء ركّبوا قبل التشفير — وبين بصمةٍ لا يجوز أن تُقبل مكتوبةً.
    """
    if not isinstance(stored, str) or stored == '':
        return False
    return stored.startswith(_BCRYPT_PREFIXES) or stored.startswith(_WERKZEUG_PREFIXES)


def verify_password(stored, given) -> bool:
    """يطابق كلمة المرور مع البصمة المخزَّنة، أيًّا كانت صيغتها.

    لا يرمي أبدًا: أي بصمة تالفة أو صيغة مجهولة تعني «لا يطابق»، لا
    انهيارَ صفحة الدخول.
    """
    if not stored or given is None:
        return False

    if is_bcrypt(stored):
        try:
            import bcrypt
        except ImportError:
            # الحزمة غائبة: يُرفض الدخول ويُذكر السبب. الصمت هنا يعني
            # عميلًا يكتب كلمته الصحيحة ويُردّ بلا تفسير.
            print('[auth] حزمة bcrypt غير مثبَّتة — تعذّر التحقّق من بصمة اللوحة.')
            return False
        try:
            return bcrypt.checkpw(given.encode('utf-8'), stored.encode('utf-8'))
        except (ValueError, TypeError):
            return False

    try:
        if check_password_hash(stored, given):
            return True
    except Exception:
        pass

    # الحسابات القديمة بكلمة مرور صريحة — وهي موجودة عند من ركّب قبل
    # التشفير — تبقى تعمل، وتُرقّى عند أول دخول ناجح.
    #
    # لكن **لا** تُقارَن القيمة المخزَّنة بالمكتوبة إن كانت بصمة: كانت
    # المقارنة تجري دائمًا، فمن كتب البصمة نفسها في خانة كلمة المرور
    # دخل. جرّبتُه: ٣٠٢، أي دخول ناجح.
    #
    # وليست حالةً نظرية: نسخة احتياطية واحدة تحمل بصمات كل المستخدمين،
    # وهي أكثر ملف يُرسَل بالبريد ويُنسَخ على ذاكرة ويُرفع إلى سحابة.
    # فكان من يقرأ نسخةً يملك كلمة مرور كل حساب فيها.
    if not looks_hashed(stored):
        return stored == given

    return False
