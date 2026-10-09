# -*- coding: utf-8 -*-
"""تركيبٌ بلا إنترنت (المرحلة ١): ملفّ رخصة موقَّع لهذا الجهاز يُفعّل البرنامج — ولا يُزوَّر."""
from datetime import date, timedelta

import requests

from tests.test_plan_limits import HWID, le2, lic, sign, token  # noqa: F401


def test_air_gapped_install_activates_from_a_signed_file(lic, monkeypatch):
    L = lic['L']

    def offline(*a, **k):
        raise requests.exceptions.ConnectionError('no network')
    monkeypatch.setattr(requests, 'post', offline)
    monkeypatch.setattr(requests, 'get', offline)
    key = le2(L, date.today() + timedelta(days=365))
    assert L.get_saved_license_key() in (None, '')
    ok, msg = L.import_offline_token(token(key, date.today() + timedelta(days=200)))
    assert ok and 'باقي' in msg
    assert L.get_saved_license_key() == key, 'المفتاح من داخل الملف — مفيش حاجة تتكتب'
    L.invalidate_license_cache()
    assert L.verify_license_full_flow(key)[0], 'يشتغل من غير شبكة'


def test_forged_foreign_or_expired_files_are_refused(lic):
    L = lic['L']
    key = le2(L, date.today() + timedelta(days=365))
    good = token(key, date.today() + timedelta(days=30))
    raw, sig = good[5:].split('.')
    assert not L.import_offline_token('ONZ1.' + raw[:-2] + 'xx.' + sig)[0], 'تاريخ متعدّل = توقيع بايظ'
    assert L.import_offline_token(token(key, date.today() + timedelta(days=30), hwid='b' * 32)) == \
        (False, 'الترخيص ده لجهاز تاني — ابعت كود الجهاز اللي هنا')
    assert L.import_offline_token(token(key, date.today() - timedelta(days=1)))[1] == 'الترخيص ده منتهي'
    assert not L.import_offline_token('hello')[0]
    from utils.db import get_setting
    assert not get_setting('license_token'), 'ولا حاجة اتحفظت'
