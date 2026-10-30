# -*- coding: utf-8 -*-
"""كلمة مرور الجهاز مشفّرةً في القاعدة — لا نصًّا يُقرأ من نسخةٍ احتياطيّة.

المفتاحُ ملفٌّ في مجلّد البيانات يُنشأ عند أوّل استعمال (`device_secret.key`).
نسخةٌ احتياطيّة للقاعدة وحدها لا تكشف كلمات مرور الأجهزة؛ واسترجاعُها على جهازٍ
آخر يطلب إعادة إدخالها — وهو المقبول.
"""
import os

from cryptography.fernet import Fernet, InvalidToken

PREFIX = 'enc1:'


def _key_path():
    from utils.db import DATA_DIR
    return os.path.join(DATA_DIR, 'device_secret.key')


def _fernet():
    path = _key_path()
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as f:
            f.write(Fernet.generate_key())
    with open(path, 'rb') as f:
        return Fernet(f.read().strip())


def seal(plain):
    if not plain:
        return ''
    return PREFIX + _fernet().encrypt(str(plain).encode('utf-8')).decode('ascii')


def unseal(stored):
    """'' إن تعذّر (مفتاحٌ آخر بعد استرجاع نسخة) — فيُطلب إدخالها ثانيةً."""
    if not stored:
        return ''
    if not str(stored).startswith(PREFIX):
        return str(stored)
    try:
        return _fernet().decrypt(str(stored)[len(PREFIX):].encode('ascii')).decode('utf-8')
    except (InvalidToken, ValueError):
        return ''
