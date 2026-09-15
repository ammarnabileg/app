"""التركيب المعزول: نظامٌ لا يصل الإنترنت يجب أن يعمل.

كثير من العملاء يشغّلون النظام داخل شبكة مغلقة — مصنع، موقع أمنيّ، أو
شبكة بلا منفذ خارج. وهؤلاء كانوا يُقفَل عليهم:

  * من ركّب دون اتصال أصلًا: لا تحقّق ناجح مسجَّل، فلا مهلة إطلاقًا —
    يُمنع من اليوم الأول.
  * ومن انقطع بعد تشغيله: يُمنع بعد أربعة عشر يومًا.

وآلية العمل دون اتصال كانت موجودة ومعطَّلة: الخادم يُصدر رمزًا موقَّعًا مع
كل تحقق ناجح، والعميل يقرؤه في ثلاثة مواضع — **ولا يكتبه في أيّها**.
فيُصدَر، ويُرمى، ثم يُقرأ فلا يوجد.

الرمز موقَّع بمفتاحنا الخاص، ومربوط بالجهاز، ويحمل تاريخ انتهائه بنفسه.
فهو أقوى من عدّ الأيام لا أضعف: لا يُزوَّر ولا يُمدَّد، ولا يحتاج شبكة.

يُشغَّل:  python -m pytest tests/test_offline_license.py -v
"""
import os
import sys

import pytest
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def lic(tmp_path, monkeypatch):
    """نظام مرخَّص، والشبكة مقطوعة."""
    import importlib

    monkeypatch.setenv('HR_DATA_DIR', str(tmp_path))

    import utils.db as d
    importlib.reload(d)
    d.init_db()

    import utils.license as L
    importlib.reload(L)

    # الشبكة مقطوعة: هذا هو الوضع الذي يوجد هذا الملف من أجله.
    def dead(*a, **k):
        raise requests.exceptions.ConnectionError('no route to host')

    monkeypatch.setattr(L.requests, 'post', dead)
    monkeypatch.setattr(L, 'get_system_hwid', lambda: 'a' * 32)

    L.init_license_table(d.get_db_connection())
    return {'L': L, 'db': d}


def _token(lic, monkeypatch, result):
    """يزرع رمزًا موقَّعًا ويثبّت نتيجة التحقّق منه."""
    lic['db'].set_setting('license_token', 'ONZ1.pretend-signed-token')

    import utils.license_verify as V
    monkeypatch.setattr(V, 'verify_license_token',
                        lambda tok, current_hwid=None, today=None: result)


# ------------------------------------------- الرخصة الموقَّعة تُنقذ المعزول

def test_a_valid_signed_token_works_with_no_network_at_all(lic, monkeypatch):
    """لا شبكة، ولا تحقّق ناجح سابق — ومع ذلك يعمل."""
    _token(lic, monkeypatch,
           {'ok': True, 'reason': None, 'days_left': 300, 'max_devices': 5})

    ok, msg = lic['L'].check_online_license_secure('LE2-whatever')

    assert ok is True
    assert 'دون اتصال' in msg


def test_it_does_not_expire_after_the_grace_window(lic, monkeypatch):
    """عدّ الأيام لا يُطبَّق على من يحمل رخصة موقَّعة سارية.

    هذا هو جوهر الإصلاح: أربعة عشر يومًا كانت تقفل على مصنعٍ بلا إنترنت.
    """
    conn = lic['db'].get_db_connection()
    conn.execute("UPDATE license_settings SET last_ok_check = '2020-01-01 00:00:00' WHERE id=1")
    conn.commit()

    _token(lic, monkeypatch,
           {'ok': True, 'reason': None, 'days_left': 100, 'max_devices': 3})

    ok, _msg = lic['L'].check_online_license_secure('LE2-whatever')
    assert ok is True, 'قُفل على رخصة موقَّعة سارية بسبب عدّ الأيام'


# ------------------------------------------------ ولا تفتح بابًا جديدًا

def test_an_expired_token_is_refused(lic, monkeypatch):
    _token(lic, monkeypatch,
           {'ok': False, 'reason': 'expired', 'days_left': -5, 'max_devices': 0})

    ok, msg = lic['L'].check_online_license_secure('LE2-whatever')

    assert ok is False
    assert 'expired' in msg


def test_a_token_for_another_machine_is_refused(lic, monkeypatch):
    _token(lic, monkeypatch,
           {'ok': False, 'reason': 'hwid_mismatch', 'days_left': 90, 'max_devices': 3})

    ok, msg = lic['L'].check_online_license_secure('LE2-whatever')

    assert ok is False
    assert 'hwid_mismatch' in msg


def test_a_broken_token_does_not_fall_back_to_the_day_grace(lic, monkeypatch):
    """وإلا صار إتلاف الرمز وسيلةً لكسب أسبوعين."""
    from datetime import datetime

    conn = lic['db'].get_db_connection()
    conn.execute("UPDATE license_settings SET last_ok_check = ? WHERE id=1",
                 (datetime.now().strftime('%Y-%m-%d %H:%M:%S'),))
    conn.commit()

    _token(lic, monkeypatch,
           {'ok': False, 'reason': 'bad_signature', 'days_left': None, 'max_devices': 0})

    ok, _msg = lic['L'].check_online_license_secure('LE2-whatever')
    assert ok is False, 'رمز تالف منح مهلة الأيام'


# ------------------------------------- وسلوك من لا رمز له لم يتغيّر

def test_without_a_token_the_old_grace_still_applies(lic):
    from datetime import datetime, timedelta

    conn = lic['db'].get_db_connection()
    recent = (datetime.now() - timedelta(days=3)).strftime('%Y-%m-%d %H:%M:%S')
    conn.execute("UPDATE license_settings SET last_ok_check = ? WHERE id=1", (recent,))
    conn.commit()

    ok, msg = lic['L'].check_online_license_secure('LE2-whatever')

    assert ok is True
    assert 'Grace' in msg


def test_without_a_token_and_never_verified_it_still_refuses(lic):
    ok, _msg = lic['L'].check_online_license_secure('LE2-whatever')
    assert ok is False


# ------------------------------------------------- الرمز يُحفَظ أصلًا

def test_a_successful_check_stores_the_token(tmp_path, monkeypatch):
    """العطل الأصلي: كان يُصدَر ويُرمى، فلا يوجد شيء يُتحقَّق منه لاحقًا."""
    import importlib

    monkeypatch.setenv('HR_DATA_DIR', str(tmp_path))
    import utils.db as d
    importlib.reload(d)
    d.init_db()
    import utils.license as L
    importlib.reload(L)
    monkeypatch.setattr(L, 'get_system_hwid', lambda: 'b' * 32)

    class Ok:
        status_code = 200

        @staticmethod
        def json():
            return {'success': True, 'message': 'Valid', 'max_devices': 4,
                    'license_token': 'ONZ1.the-signed-token'}

    monkeypatch.setattr(L.requests, 'post', lambda *a, **k: Ok())

    ok, _msg = L.check_online_license_secure('LE2-whatever')

    assert ok is True
    assert d.get_setting('license_token') == 'ONZ1.the-signed-token', \
        'وصل الرمز من الخادم ولم يُحفَظ'


if __name__ == '__main__':
    raise SystemExit(pytest.main([__file__, '-v']))
