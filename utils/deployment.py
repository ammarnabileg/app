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

    return out
