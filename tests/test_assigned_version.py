"""العميل يسأل اللوحة عن نسخته هو، لا عن نسخةٍ للجميع.

اللوحة صارت تسمح بتثبيت عميلٍ على نسخة بعينها — لعميلٍ يؤجّل
التحديث، أو تُجرَّب عليه نسخةٌ قبل الباقين، أو أعطبته نسخةٌ فيُرجَع.
وكل ذلك لا يساوي شيئًا إن لم يقل العميل من هو حين يسأل: اللوحة لا
تعرف السائل، فتردّ بالأحدث للجميع.

ويُفحص هنا ما يخرج على السلك لا نيّة الشيفرة: أن المفتاح يُرسَل،
وأنه يُرسَل **في الجسم لا في سطر العنوان** — سطر العنوان يُكتب في
سجلّات الخادم والوسطاء، فمفتاحٌ فيه يصير مفتاحًا في ملفّ نصّي.

يُشغَّل:  python -m pytest tests/test_assigned_version.py -v
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class _Resp:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


@pytest.fixture
def wire(monkeypatch):
    """يلتقط الطلب الخارج ويردّ ردًّا مصطنعًا."""
    from utils import update_manager as um

    captured = {}

    def fake_post(url, data=None, params=None, timeout=None, **kw):
        captured.update({'url': url, 'data': data, 'params': params,
                         'method': 'POST'})
        return _Resp(captured.get('reply', {
            'success': True, 'version': '9.9.9',
            'download_url': 'https://x/9.9.9.zip',
            'release_notes': '', 'mandatory': False,
        }))

    def fake_get(url, **kw):
        captured.update({'url': url, 'method': 'GET'})
        return _Resp({'success': False})

    monkeypatch.setattr(um.requests, 'post', fake_post)
    monkeypatch.setattr(um.requests, 'get', fake_get)
    return um, captured


def _with_key(monkeypatch, key):
    import utils.license as lic
    monkeypatch.setattr(lic, 'get_saved_license_key', lambda: key)


# ------------------------------------------------- المفتاح يُرسَل

def test_the_licence_key_goes_out_with_the_question(wire, monkeypatch):
    um, captured = wire
    _with_key(monkeypatch, 'LE2-CUSTOMER-A')

    um.check_for_updates()

    assert captured, 'لم يخرج أي طلب — الاختبار لا يفحص شيئًا'
    assert (captured['data'] or {}).get('license_key') == 'LE2-CUSTOMER-A', (
        'المفتاح لا يصل اللوحة — فيردّ عليه بالأحدث كأنه أي عميل')


def test_the_key_travels_in_the_body_not_the_url(wire, monkeypatch):
    """سطر العنوان يستقرّ في السجلّات. الجسم لا.

    لو عاد أحدٌ يومًا إلى `requests.get(..., params=...)` سقط هذا —
    قبل أن تمتلئ سجلّات الخادم بمفاتيح تراخيص العملاء.
    """
    um, captured = wire
    _with_key(monkeypatch, 'LE2-CUSTOMER-A')

    um.check_for_updates()

    assert captured['method'] == 'POST'
    assert not captured.get('params'), (
        'المفتاح يسافر في سطر العنوان — فيُكتب في كل سجلّ يمرّ به')
    assert 'LE2-CUSTOMER-A' not in captured['url']


# ------------------------------------------------- ولا يُسقط شيئًا

def test_a_machine_with_no_key_still_asks(wire, monkeypatch):
    """السؤال بلا مفتاح يعيد الأحدث — وهو سلوك ما قبل التثبيت تمامًا."""
    um, captured = wire
    _with_key(monkeypatch, None)

    um.check_for_updates()

    assert captured, 'توقّف الفحص لغياب المفتاح — والتحديثات تقف معه'
    assert captured['data'] == {}


def test_a_broken_local_database_does_not_stop_updates(wire, monkeypatch):
    """قراءة المفتاح تقرأ قاعدة محلّية. وتعثّرها لا يجوز أن يقطع
    التحديثات عن الجهاز — وفيها التحديثات الأمنية."""
    um, captured = wire
    import utils.license as lic
    monkeypatch.setattr(lic, 'get_saved_license_key',
                        lambda: (_ for _ in ()).throw(RuntimeError('boom')))

    available, url, _notes, _m = um.check_for_updates()

    assert captured, 'سقط الفحص لأن قراءة المفتاح تعثّرت'
    assert captured['data'] == {}
    assert available is True


# ------------------------------------------------- النسخة المسنَدة تُحترم

def test_the_assigned_version_is_what_gets_installed(wire, monkeypatch):
    """ما تردّ به اللوحة هو ما يُركَّب — ولو لم يكن الأحدث عندها.

    عميلٌ مثبَّت على 2.0.0 يجب أن ينزّل 2.0.0. والمقارنة تبقى على
    الرقم المحلّي: نسخةٌ أقدم ممّا يشغّله ليست تحديثًا.
    """
    um, captured = wire
    _with_key(monkeypatch, 'LE2-PINNED')
    monkeypatch.setattr(um, 'CURRENT_VERSION', '1.0.0')
    captured['reply'] = {
        'success': True, 'version': '2.0.0',
        'download_url': 'https://x/2.0.0.zip',
        'release_notes': 'المثبَّتة', 'mandatory': True,
        'sha256': 'ab' * 32,
    }

    available, url, notes, mandatory = um.check_for_updates()

    assert available is True
    assert url == 'https://x/2.0.0.zip'
    assert mandatory is True
    assert um._EXPECTED_SHA256 == 'ab' * 32


def test_an_old_panel_still_answers(wire, monkeypatch):
    """ترتيبٌ سيقع: نظامٌ حُدِّث قبل أن تُحدَّث لوحته.

    اللوحة كانت تسجّل هذا المسار لـGET وحده، فيقابل POST منها 404 —
    ويتوقّف الفحص عن العمل بلا عَرَضٍ يراه أحد. فيُسقَط إلى GET.
    """
    um, captured = wire
    _with_key(monkeypatch, 'LE2-CUSTOMER-A')
    monkeypatch.setattr(um, 'CURRENT_VERSION', '1.0.0')

    calls = []

    class _NotFound:
        status_code = 404

        @staticmethod
        def json():
            return {}

    monkeypatch.setattr(um.requests, 'post',
                        lambda *a, **k: (calls.append('post'), _NotFound())[1])
    monkeypatch.setattr(um.requests, 'get',
                        lambda *a, **k: (calls.append('get'), _Resp({
                            'success': True, 'version': '2.1.0',
                            'download_url': 'https://x/2.1.0.zip',
                            'release_notes': '', 'mandatory': False,
                        }))[1])

    available, url, _notes, _m = um.check_for_updates()

    assert calls == ['post', 'get'], f'تسلسل غير متوقَّع: {calls}'
    assert available is True, 'لوحةٌ قديمة توقف التحديثات عن الجهاز كلّها'
    assert url == 'https://x/2.1.0.zip'


def test_a_version_older_than_the_installed_one_is_not_an_update(wire, monkeypatch):
    """اللوحة قد تُسند نسخةً أقدم (إرجاع عميلٍ أعطبته نسخة).

    والتنزيل هنا ليس ترقيةً بل تنزيلًا إلى الخلف — ولا يفعله الفحص
    الدوري من تلقائه: `download_and_install_update` تفكّ فوق مجلّد
    التركيب وتشغّل، فتنفيذها بلا قرارٍ صريح هو إرجاعٌ صامت.
    """
    um, captured = wire
    _with_key(monkeypatch, 'LE2-ROLLBACK')
    monkeypatch.setattr(um, 'CURRENT_VERSION', '2.1.0')
    captured['reply'] = {
        'success': True, 'version': '2.0.0',
        'download_url': 'https://x/2.0.0.zip',
        'release_notes': '', 'mandatory': True,
    }

    available, url, _notes, _m = um.check_for_updates()

    assert available is False
    assert url is None


if __name__ == '__main__':
    raise SystemExit(pytest.main([__file__, '-v']))
