"""كل مسارٍ يناديه التطبيق موجودٌ في الخادم فعلًا.

اختبارات Dart تمرّ على خادمٍ مزيّف، فهي تُثبت أن العميل يرسل ما نوى
إرساله — ولا تُثبت أن المسار موجود. وحرفٌ ناقص في مسار يعني 404 لا
يظهر إلا حين يفتح **العميل** تلك الشاشة بعينها.

فهذا الملف يقرأ المسارات من شيفرة Dart نفسها — لا من قائمةٍ أكتبها
بيدي وتتقادم — ويسألها جدول مسارات Flask.

يُشغَّل:  python -m pytest tests/test_app_routes_exist.py -v
"""
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

DART_DIR = os.path.join(ROOT, 'field_app', 'lib')

# المسار كما يُكتب في Dart: نصٌّ يبدأ بـ / داخل نداء get/postJson/
# _multipart، أو ثابتٌ في نداء مباشر.
PATH_RE = re.compile(r"""['"](/(?:portal|login)[^'"?]*)['"]""")


def dart_paths():
    found = set()
    for base, _dirs, files in os.walk(DART_DIR):
        for f in files:
            if not f.endswith('.dart'):
                continue
            with open(os.path.join(base, f), encoding='utf-8') as fh:
                for m in PATH_RE.finditer(fh.read()):
                    found.add(m.group(1))
    return sorted(found)


@pytest.fixture(scope='module')
def flask_rules():
    import importlib
    import utils.auth as auth
    importlib.reload(auth)
    auth.get_current_license_info = lambda: {'ok': True}
    import app as A
    importlib.reload(A)
    return {str(r.rule) for r in A.app.url_map.iter_rules()}


def test_the_app_calls_at_least_the_screens_it_has(flask_rules):
    """حارسٌ على الاختبار نفسه: لو فشل الاستخراج لمرّ كل شيء فارغًا."""
    paths = dart_paths()
    assert len(paths) >= 10, f'استُخرج {len(paths)} مسارًا فقط — الاستخراج معطوب'


def test_every_path_the_app_calls_exists_on_the_server(flask_rules):
    missing = [p for p in dart_paths() if p not in flask_rules]
    assert not missing, (
        'مسارات يناديها التطبيق ولا وجود لها في الخادم:\n  ' +
        '\n  '.join(missing))


def test_the_portal_endpoints_the_app_needs_are_all_present(flask_rules):
    """المسارات التي بُنيت عليها شاشات البوابة، مذكورةً بأسمائها.

    لو حُذف أحدها أو أُعيدت تسميته في الخادم، يسقط هذا فورًا بدل أن
    يُكتشف حين يفتح موظفٌ الشاشة.
    """
    needed = [
        '/portal/api/bootstrap',
        '/portal/api/my-data',
        '/portal/api/attendance',
        '/portal/api/punch-status',
        '/portal/api/punch',
        '/portal/api/request-leave',
        '/portal/api/request-excuse',
        '/portal/api/team-summary',
        '/portal/api/team-approvals',
        '/portal/api/approve-request',
        '/portal/api/field/start',
        '/portal/api/field/track',
        '/portal/api/field/stop',
        '/portal/api/field/plan',
        '/portal/api/field/token',
        '/portal/api/field/check-in',
        '/portal/api/field/check-out',
    ]
    missing = [p for p in needed if p not in flask_rules]
    assert not missing, 'مفقودة من الخادم: ' + ', '.join(missing)


if __name__ == '__main__':
    raise SystemExit(pytest.main([__file__, '-v']))
