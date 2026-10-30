# -*- coding: utf-8 -*-
"""ZKTeco — غلافٌ رقيق للكود القائم، **لا يغيّر سلوكه**.

أجهزةُ ZKTeco تبقى على مساراتها القديمة كلّها (`routes/adms_routes.py`،
`fingerprint_sync.py`، `utils/device_access.py`، `utils/fingerprint_utils.py`).
هذا الملفّ يعرّفها للمحرّك — اسمَها وقدراتِها — فتظهر في الشاشة بجوار الماركات
الأخرى بالشكل نفسه. ولا يُنقل منطقُها إلى هنا إلا بعد اختبارات إعادة تشغيل حركة
أجهزةٍ حقيقيّة (docs/ROADMAP_2026.md، المرحلة ٣ب).
"""
from . import base


class ZkPushDriver(base.BaseDriver):
    key = 'zk_push'
    label = 'ZKTeco — ADMS (Push)'
    brand = 'ZKTeco'
    experimental = False
    needs_login = False
    default_port = 8081
    capabilities = frozenset({base.TEST, base.PUSH_EVENTS, base.USERS, base.DISABLE, base.DELETE,
                              base.TEMPLATES, base.FACE_PHOTO, base.SET_TIME})

    def test(self):
        last = self.device.get('last_activity')
        if last:
            return {'ok': True, 'message': f'الجهاز آخر مرة اتصل: {last}', 'info': {}}
        return {'ok': False, 'message': 'الجهاز لسه ما اتصلش بالبرنامج (ADMS بيتصل هو بالسيرفر)', 'info': {}}


class ZkDirectDriver(base.BaseDriver):
    key = 'zk_direct'
    label = 'ZKTeco — IP مباشر (K40 وأمثاله)'
    brand = 'ZKTeco'
    experimental = False
    needs_login = False
    default_port = 4370
    capabilities = frozenset({base.TEST, base.PULL_ATTENDANCE, base.USERS, base.DISABLE, base.DELETE,
                              base.TEMPLATES, base.SET_TIME})

    def test(self):
        from utils.fingerprint_utils import test_fingerprint_device
        ok, result = test_fingerprint_device(self.device.get('id'))
        return {'ok': bool(ok), 'message': str(result if not isinstance(result, dict) else
                                                 result.get('message', 'تم الاتصال')), 'info':
                result if isinstance(result, dict) else {}}
