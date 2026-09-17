"""على أيّ عنوانٍ يُتعرَّف جهاز البصمة، وماذا يُقال عند الفشل.

## ما وقع فعلًا

مسؤولٌ يضغط «أرسل الوقت للجهاز». الشاشة تقول «تمت الجدولة» — وهي
صادقة: الأمر يُكتب في `adms_commands` بحالة PENDING. ثم لا يصل
الجهازَ شيء، ولا يظهر في السجلّ سطرٌ واحد يقول لماذا.

السبب أن `/iclock/getrequest` يتعرّف على الجهاز بأحد أمرين: رقمه
التسلسلي مكتوبًا في `device_name`، أو عنوانه مطابقًا `device_ip`.
وفي النشر السحابي أمامنا وسيط، فـ`request.remote_addr` عنوانُ
الوسيط لا عنوان الجهاز — فالبديل بالعنوان ميت، ولا يبقى إلا
الرقم التسلسلي. وجهازٌ مسجَّل باسمٍ وصفيّ («بصمة الاستقبال») لا
يُطابَق بشيء: يسأل عن أوامره كل ثوانٍ، ويُجاب `OK` في صمت.

فالعطب عطبان: عنوانٌ يُقرأ من الموضع الخطأ، وصمتٌ يمنع تشخيصه.

يُشغَّل:  python -m pytest tests/test_adms_device_identity.py -v
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


PROXY = '10.0.1.7'        # الوسيط
DEVICE = '192.168.1.55'   # الجهاز على شبكة العميل


@pytest.fixture
def app(tmp_path, monkeypatch):
    """نظامٌ صغير بمسارات ADMS وقاعدة نظيفة."""
    import importlib

    monkeypatch.setenv('HR_DATA_DIR', str(tmp_path))
    import utils.db as db
    importlib.reload(db)
    db.init_db()

    import utils.net as net
    importlib.reload(net)
    import routes.adms_routes as adms
    importlib.reload(adms)

    from flask import Flask
    a = Flask(__name__)
    a.secret_key = 'test'
    a.register_blueprint(adms.adms_bp, url_prefix='/iclock')
    return a, db, adms


def _add_device(db, name, ip):
    conn = db.get_db_connection()
    conn.execute(
        'INSERT INTO fingerprint_devices (device_name, device_ip, is_active) VALUES (?, ?, 1)',
        (name, ip))
    conn.commit()


def _queue(db, device_id, kind='SET TIME'):
    import json
    conn = db.get_db_connection()
    conn.execute(
        "INSERT INTO adms_commands (device_id, command_type, payload, status) "
        "VALUES (?, ?, ?, 'PENDING')", (device_id, kind, json.dumps('x')))
    conn.commit()


# ------------------------------------------------ العنوان

def test_without_a_proxy_the_address_is_the_devices_own(app, monkeypatch):
    """التركيب المحلي: الجهاز يكلّمنا مباشرةً، فلا ترويسة تُقرأ."""
    a, db, adms = app
    monkeypatch.delenv('HR_TRUSTED_PROXY_HOPS', raising=False)

    with a.test_request_context('/iclock/getrequest?SN=X',
                                environ_base={'REMOTE_ADDR': DEVICE}):
        assert adms.device_addr() == DEVICE


def test_behind_one_proxy_the_devices_address_is_read_not_the_proxys(app, monkeypatch):
    """النشر السحابي: بلا هذا، كل الأجهزة عنوانها عنوان الوسيط.

    و`device_ip` عمودٌ فريد — فجهازان يأخذان العنوان نفسه يتعارضان.
    """
    a, db, adms = app
    monkeypatch.setenv('HR_TRUSTED_PROXY_HOPS', '1')

    with a.test_request_context(
            '/iclock/getrequest?SN=X',
            environ_base={'REMOTE_ADDR': PROXY},
            headers={'X-Forwarded-For': DEVICE}):
        assert adms.device_addr() == DEVICE, (
            'يُقرأ عنوان الوسيط — فالتعرّف بالعنوان ميت في السحابة')


def test_a_forged_header_is_not_trusted_beyond_the_declared_hops(app, monkeypatch):
    """العنوان مفتاح تعرّفٍ على جهاز، فلا يُقرأ كما يكتبه المرسل.

    بوسيطٍ واحد معلَن، يؤخذ آخر ما أُلحق بالسلسلة — لا أوّلها الذي
    كتبه من أرسل الطلب.
    """
    a, db, adms = app
    monkeypatch.setenv('HR_TRUSTED_PROXY_HOPS', '1')

    with a.test_request_context(
            '/iclock/getrequest?SN=X',
            environ_base={'REMOTE_ADDR': PROXY},
            headers={'X-Forwarded-For': f'1.2.3.4, {DEVICE}'}):
        assert adms.device_addr() == DEVICE


# ------------------------------------------------ الأمر يصل

def test_a_device_registered_by_its_serial_gets_its_command(app, monkeypatch):
    a, db, adms = app
    monkeypatch.setenv('HR_TRUSTED_PROXY_HOPS', '1')
    _add_device(db, 'AJE1260400983', DEVICE)
    _queue(db, 1)

    c = a.test_client()
    r = c.get('/iclock/getrequest?SN=AJE1260400983',
              environ_base={'REMOTE_ADDR': PROXY},
              headers={'X-Forwarded-For': DEVICE})

    body = r.get_data(as_text=True)
    assert 'SET OPTIONS DateTime=' in body, f'لم يُسلَّم الأمر: {body!r}'


def test_a_device_registered_by_address_only_is_reached_behind_a_proxy(app, monkeypatch):
    """الحال التي انكسرت: جهازٌ اسمه وصفيّ وتعرّفُه بعنوانه.

    كان يعمل على التركيب المحلي ويسكت في السحابة — لأن العنوان
    المقروء كان عنوان الوسيط.
    """
    a, db, adms = app
    monkeypatch.setenv('HR_TRUSTED_PROXY_HOPS', '1')
    _add_device(db, 'بصمة الاستقبال', DEVICE)
    _queue(db, 1)

    c = a.test_client()
    r = c.get('/iclock/getrequest?SN=AJE1260400983',
              environ_base={'REMOTE_ADDR': PROXY},
              headers={'X-Forwarded-For': DEVICE})

    body = r.get_data(as_text=True)
    assert 'SET OPTIONS DateTime=' in body, (
        f'الجهاز المعروف بعنوانه لم يُطابَق خلف الوسيط: {body!r}')


# ------------------------------------------------ ولا صمت

def test_an_unknown_device_is_named_in_the_log(app, monkeypatch, caplog):
    """الصمت هو ما جعل هذا العطب يستغرق ما استغرقه.

    الجهاز يسأل، ولا صفَّ له، فيُجاب `OK` ولا يُكتب شيء. فلا الشاشة
    ولا السجلّ يقولان إن الأوامر تُكتب لجهازٍ لا يسأل عنها أحد.
    """
    import logging

    a, db, adms = app
    monkeypatch.setenv('HR_TRUSTED_PROXY_HOPS', '1')
    adms._unknown_seen.clear()
    _add_device(db, 'بصمة أخرى', '192.168.1.99')
    _queue(db, 1)

    c = a.test_client()
    with caplog.at_level(logging.WARNING, logger='adms'):
        r = c.get('/iclock/getrequest?SN=AJE1260400983',
                  environ_base={'REMOTE_ADDR': PROXY},
                  headers={'X-Forwarded-For': DEVICE})

    assert r.status_code == 200          # البروتوكول لا يُكسر
    text = caplog.text
    assert 'AJE1260400983' in text, 'الرقم التسلسلي لا يُذكر — والسطر بلا الرقم لا يحلّ شيئًا'
    assert 'pending_commands' in text, 'لا يُذكر أن أوامر تنتظر'


def test_the_unknown_warning_does_not_flood_the_log(app, monkeypatch, caplog):
    """الجهاز يسأل كل ثوانٍ. وسطرٌ عند كل سؤال يغرق السجلّ فيخفي
    ما نبحث عنه — وهو الداء نفسه الذي نعالجه."""
    import logging

    a, db, adms = app
    adms._unknown_seen.clear()

    c = a.test_client()
    with caplog.at_level(logging.WARNING, logger='adms'):
        for _ in range(5):
            c.get('/iclock/getrequest?SN=AJE1260400983',
                  environ_base={'REMOTE_ADDR': PROXY})

    assert caplog.text.count('unknown device') == 1


def test_the_healthcheck_is_never_reported_as_an_unknown_device(app, monkeypatch, caplog):
    """فحص الصحة يسأل كل ثلاثين ثانية وليس جهازًا."""
    import logging

    a, db, adms = app
    adms._unknown_seen.clear()

    c = a.test_client()
    with caplog.at_level(logging.WARNING, logger='adms'):
        c.get('/iclock/getrequest?SN=healthcheck',
              environ_base={'REMOTE_ADDR': '127.0.0.1'})

    assert 'unknown device' not in caplog.text


if __name__ == '__main__':
    raise SystemExit(pytest.main([__file__, '-v']))
