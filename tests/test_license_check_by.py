# -*- coding: utf-8 -*-
"""`check_by`: جهاز إضافي على نفس الترخيص لازم يكلّم اللوحة قبل تاريخ معيّن.

التاريخ اللي العميل بيشوفه (`expiry`) مابيتغيّرش — القيد بس على الشغل من غير نت.
"""
import base64
import json
import os
import sys
from datetime import date, timedelta

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

HWID = 'a' * 32


@pytest.fixture
def signer(monkeypatch):
    import utils.license_verify as V
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub = key.public_key().public_bytes(serialization.Encoding.PEM,
                                        serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    monkeypatch.setattr(V, 'LICENSE_PUBLIC_KEY', pub)

    def sign(**extra):
        payload = dict({'key': 'LE2-x', 'expiry': (date.today() + timedelta(days=300)).isoformat(),
                        'max_devices': 2, 'hwid': HWID, 'issued_at': '2026-10-13T00:00:00Z', 'v': 1}, **extra)
        body = json.dumps(payload, separators=(',', ':')).encode()
        sig = key.sign(body, padding.PKCS1v15(), hashes.SHA256())
        b64 = lambda b: base64.urlsafe_b64encode(b).decode().rstrip('=')
        return 'ONZ1.' + b64(body) + '.' + b64(sig)
    return V, sign


def test_no_check_by_unchanged(signer):
    V, sign = signer
    r = V.verify_license_token(sign(), current_hwid=HWID)
    assert r['ok'] and r['days_left'] == 300


def test_check_by_in_future_ok_and_expiry_shown(signer):
    V, sign = signer
    r = V.verify_license_token(sign(check_by=(date.today() + timedelta(days=7)).isoformat()), current_hwid=HWID)
    assert r['ok'], r
    assert r['days_left'] == 300, 'العميل بيشوف تاريخ الترخيص الحقيقي، مش تاريخ الاتصال'


def test_check_by_passed_needs_contact(signer):
    V, sign = signer
    tok = sign(check_by=(date.today() - timedelta(days=1)).isoformat())
    r = V.verify_license_token(tok, current_hwid=HWID)
    assert not r['ok'] and r['reason'] == 'check_required'
    assert V.verify_license_token(tok, current_hwid=HWID, today=date.today() - timedelta(days=1))['ok'], \
        'يوم check_by نفسه لسه شغال'


def test_null_check_by_ignored(signer):
    V, sign = signer
    assert V.verify_license_token(sign(check_by=None), current_hwid=HWID)['ok']
