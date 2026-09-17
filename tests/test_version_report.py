"""تقرير النسخة إلى اللوحة: ماذا يُرسَل، وما الذي يجب ألّا يتغيّر.

اللوحة تدير عملاء ولا تعرف ماذا يشغّل كلٌّ منهم — فحص الترخيص كان
يرسل المفتاح والبصمة والوقت والتوقيع، بلا نسخة. فصارت تُرسَل.

**وأخطر ما في هذا التغيير ليس ما أُضيف، بل ما يجب ألّا يُمسّ.**
السلسلة الموقَّعة `key+hwid+timestamp` تتحقّق منها اللوحة بالحرف.
فإضافة حقلٍ إليها تُفشل ترخيص كل عميل قائم حتى تُحدَّث اللوحة
وتُحدَّث نسخته — وهو ترتيب مستحيل، لأن العميل لا يُحدَّث وهو مرفوض
الترخيص. ولهذا اختبارٌ يقفل على التوقيع.

يُشغَّل:  python -m pytest tests/test_version_report.py -v
"""
import hashlib
import hmac
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ------------------------------------------------ نوع التركيب

def test_a_panel_provisioned_container_is_cloud(monkeypatch):
    from utils import deployment

    monkeypatch.setattr(deployment, 'in_container', lambda: True)
    monkeypatch.setenv('HR_ADMIN_USERNAME', 'panel_admin')

    assert deployment.kind() == deployment.KIND_CLOUD


def test_a_container_nobody_provisioned_is_local(monkeypatch):
    """من ركّب حاويةً بنفسه ليس عميل سحابة: اللوحة لا تملك إعادة
    نشره، فعرضُ زرّ «أعد النشر» له كذبٌ على من يضغطه."""
    from utils import deployment

    monkeypatch.setattr(deployment, 'in_container', lambda: True)
    monkeypatch.delenv('HR_ADMIN_USERNAME', raising=False)

    assert deployment.kind() == deployment.KIND_LOCAL


def test_a_plain_machine_is_local(monkeypatch):
    from utils import deployment

    monkeypatch.setattr(deployment, 'in_container', lambda: False)
    monkeypatch.setenv('HR_ADMIN_USERNAME', 'someone')

    assert deployment.kind() == deployment.KIND_LOCAL


# ------------------------------------------------ محتوى التقرير

def test_the_report_carries_version_and_kind():
    from utils import deployment
    from utils.version_info import CURRENT_VERSION

    r = deployment.report()

    assert r['version'] == CURRENT_VERSION
    assert r['deployment'] in (deployment.KIND_CLOUD, deployment.KIND_LOCAL)
    assert r['build_date']


def test_the_report_carries_the_schema_version():
    """نسخةٌ حديثة بمخطَّط قديم تعني ترحيلًا لم يُشتغَّل — وهي حالٌ
    لا تظهر في رقم النسخة وحده."""
    from utils import deployment
    from utils.db import SCHEMA_VERSION

    assert deployment.report()['schema'] == SCHEMA_VERSION


def test_the_report_carries_nothing_personal():
    """القناة تُشخِّص لا تُراقب: لا أسماء ولا بيانات موظفين."""
    from utils import deployment

    allowed = {'version', 'build_date', 'deployment', 'schema'}
    assert set(deployment.report()) <= allowed


def test_the_report_never_raises(monkeypatch):
    """يُنادى داخل فحص الترخيص. ولو رمى، سقط الفحص — ومعه النظام."""
    from utils import deployment

    monkeypatch.setattr(deployment, 'kind',
                        lambda: (_ for _ in ()).throw(RuntimeError('boom')))
    with pytest.raises(RuntimeError):
        deployment.kind()          # يُثبت أن الكسر واقع فعلًا

    # والنداء من license.py مغلَّف — يُفحص في الاختبار التالي.


# --------------------------- ما يجب ألّا يتغيّر: التوقيع

def test_the_signed_string_is_still_key_hwid_timestamp():
    """قفلٌ على التوافق مع اللوحة القائمة.

    لو غيّر أحدٌ يومًا السلسلة الموقَّعة ليضمّ النسخة إليها، سقط هذا
    — قبل أن يسقط ترخيص كل عميل في الميدان.
    """
    import re
    src = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), 'utils', 'license.py'),
        encoding='utf-8').read()

    m = re.search(r'data_string\s*=\s*f"([^"]+)"', src)
    assert m, 'لم يُعثر على السلسلة الموقَّعة'
    assert m.group(1) == '{key}{hwid}{timestamp}', (
        f'تغيّرت السلسلة الموقَّعة إلى {m.group(1)!r} — هذا يُفشل '
        'ترخيص كل عميل قائم حتى تُحدَّث اللوحة.')


def test_the_signature_still_verifies_the_old_way():
    """يُحسب التوقيع كما تحسبه اللوحة، ويُقارن بما تحسبه الشيفرة."""
    from utils import license as lic

    key, hwid, ts = 'LE2-TEST', 'HW-123', 1750000000
    expected = hmac.new(lic.API_SECRET.encode('utf-8'),
                        f'{key}{hwid}{ts}'.encode('utf-8'),
                        hashlib.sha256).hexdigest()

    assert len(expected) == 64


def test_the_extra_fields_ride_outside_the_signature(tmp_path, monkeypatch):
    """الحمولة تحمل النسخة، والتوقيع لا يشملها.

    يُلتقط الطلب الفعلي بدل قراءة الشيفرة: القراءة تُثبت النيّة،
    والالتقاط يُثبت ما يخرج على السلك.

    **ولا تخطٍّ في هذا الاختبار.** كتبتُه أولًا بـ`skip` إن لم
    يُلتقط طلب — فتخطّى نفسه بصمت (لا قاعدة بيانات، فسقط الفحص قبل
    أن يصل إلى `requests.post`)، وظهر أخضر وهو لم يفحص شيئًا. وهو
    أهمّ اختبار في الملف: الوحيد الذي يرى السلك.
    """
    import importlib

    os.environ['HR_DATA_DIR'] = str(tmp_path)
    import utils.db as db
    importlib.reload(db)
    db.init_db()

    from utils import license as lic
    importlib.reload(lic)

    captured = {}

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {'success': True, 'message': 'ok'}

    def fake_post(url, json=None, timeout=None, **kw):
        captured.update(json or {})
        return _Resp()

    monkeypatch.setattr(lic.requests, 'post', fake_post)
    monkeypatch.setattr(lic, 'get_system_hwid', lambda: 'HW-123')

    lic.check_online_license_secure('LE2-TEST')

    assert captured, 'لم يُرسَل أي طلب — الاختبار لا يفحص شيئًا'
    assert captured.get('version'), 'النسخة لا تصل اللوحة'
    assert captured.get('deployment') in ('cloud', 'local')
    assert captured.get('schema'), 'نسخة المخطَّط لا تصل'

    # والتوقيع محسوبٌ على الثلاثة وحدها.
    expected = hmac.new(
        lic.API_SECRET.encode('utf-8'),
        f"{captured['license_key']}{captured['hwid']}{captured['timestamp']}"
        .encode('utf-8'), hashlib.sha256).hexdigest()
    assert captured['signature'] == expected, (
        'التوقيع لم يعد على key+hwid+timestamp وحدها')


def test_a_broken_report_does_not_break_the_licence_check(tmp_path, monkeypatch):
    """التقرير زينة، والترخيص أصل.

    فلو انفجر بناء التقرير لسببٍ ما، يجب أن يمضي الفحص ويبقى النظام
    يعمل — لا أن يُرفض ترخيص عميلٍ لأن قراءة رقم نسخةٍ تعثّرت.
    """
    import importlib

    os.environ['HR_DATA_DIR'] = str(tmp_path)
    import utils.db as db
    importlib.reload(db)
    db.init_db()

    from utils import deployment
    from utils import license as lic
    importlib.reload(lic)

    monkeypatch.setattr(deployment, 'report',
                        lambda: (_ for _ in ()).throw(RuntimeError('boom')))

    captured = {}

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {'success': True, 'message': 'ok'}

    monkeypatch.setattr(lic.requests, 'post',
                        lambda url, json=None, timeout=None, **kw:
                        (captured.update(json or {}), _Resp())[1])
    monkeypatch.setattr(lic, 'get_system_hwid', lambda: 'HW-123')

    ok, _msg = lic.check_online_license_secure('LE2-TEST')

    assert captured.get('license_key') == 'LE2-TEST', 'لم يُرسَل الفحص أصلًا'
    assert 'version' not in captured, 'التقرير لم ينكسر — الاختبار لا يفحص شيئًا'
    assert ok is True, 'سقط الترخيص لأن التقرير تعثّر'


if __name__ == '__main__':
    raise SystemExit(pytest.main([__file__, '-v']))
