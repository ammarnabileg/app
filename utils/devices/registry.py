# -*- coding: utf-8 -*-
"""السوّاقات المعروفة، وأيُّها لجهازٍ ما، وأيُّها مفعّلٌ عند هذه الشركة.

**أيُّ سوّاقٍ لجهاز؟** عمودُ `driver` في `fingerprint_devices`. وجهازٌ قديم بلا قيمة
يأخذ سوّاقه من حاله كما كان دائمًا: `zk_push` إن كان ADMS، وإلّا `zk_direct` —
فلا يتغيّر شيءٌ في الأجهزة الموجودة عند العملاء.

**أيُّ السوّاقات مفعّل؟** إعدادٌ للشركة (`device_drivers_enabled`). ZKTeco مفعّلٌ
دائمًا، والماركاتُ الجديدة تجريبيّة ومُطفأة حتى تُفعَّل من صفحة الأجهزة.
"""
from . import base
from .zk import ZkPushDriver, ZkDirectDriver

LEGACY_KEYS = ('zk_push', 'zk_direct')
SETTING_ENABLED = 'device_drivers_enabled'

# شرطُ SQL لـ«جهاز ZKTeco» — تضيفه المساراتُ القديمة كي لا تعامل جهازَ Hikvision
# مثلًا على أنه K40 (كلُّ جهازٍ ليس ADMS كان يُعدّ K40).
LEGACY_SQL = "COALESCE(driver, '') IN ('', 'zk_push', 'zk_direct')"


def _all():
    from .hikvision import HikvisionDriver
    from .dahua import DahuaDriver
    from .suprema import SupremaBioStarDriver
    return {d.key: d for d in (ZkPushDriver, ZkDirectDriver, HikvisionDriver, DahuaDriver,
                               SupremaBioStarDriver)}


def drivers():
    """{key: class} بترتيب العرض."""
    return _all()


def _get(device, key, default=None):
    try:
        v = device[key]
    except (KeyError, IndexError, TypeError):
        return default
    return default if v is None else v


def driver_key(device):
    k = (_get(device, 'driver', '') or '').strip()
    if k:
        return k
    return 'zk_push' if int(_get(device, 'is_adms', 0) or 0) else 'zk_direct'


def is_legacy(device):
    """جهاز ZKTeco — مساراتُه القديمة كما هي."""
    return driver_key(device) in LEGACY_KEYS


def driver_class(device_or_key):
    key = device_or_key if isinstance(device_or_key, str) else driver_key(device_or_key)
    cls = _all().get(key)
    if cls is None:
        raise base.DriverError(f'نوع جهاز غير معروف: {key}')
    return cls


def get_driver(device, http=None):
    """سوّاقٌ جاهز لجهاز (بكلمة مروره مفكوكة)."""
    from .secrets import unseal
    cls = driver_class(device)
    return cls(device, password=unseal(_get(device, 'auth_secret', '')), http=http)


def capabilities(device):
    try:
        return set(driver_class(device).capabilities)
    except base.DriverError:
        return set()


def enabled_keys():
    """السوّاقات المفعّلة عند هذه الشركة — ZKTeco دائمًا."""
    try:
        from utils.db import get_setting
        raw = get_setting(SETTING_ENABLED, '') or ''
    except Exception:
        raw = ''
    keys = [k.strip() for k in str(raw).split(',') if k.strip()]
    known = _all()
    return list(LEGACY_KEYS) + [k for k in keys if k in known and k not in LEGACY_KEYS]


def set_enabled(keys):
    from utils.db import set_setting
    known = _all()
    set_setting(SETTING_ENABLED, ','.join(k for k in keys if k in known and k not in LEGACY_KEYS))


def catalog():
    """للشاشة: [{key, label, brand, experimental, enabled, capabilities, default_port, needs_login}]."""
    on = set(enabled_keys())
    out = []
    for k, cls in _all().items():
        out.append({'key': k, 'label': cls.label, 'brand': cls.brand, 'experimental': cls.experimental,
                    'enabled': k in on, 'capabilities': sorted(cls.capabilities),
                    'default_port': cls.default_port, 'needs_login': cls.needs_login})
    return out
