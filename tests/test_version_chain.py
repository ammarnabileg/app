"""يثبت أن رقم الإصدار متسقّ عبر السلسلة كلها.

السلسلة: utils/version_info.py → tools/publish_version.py → اللوحة →
utils/update_manager.check_for_updates عند العميل.

ما وقع فعلًا في 2.10.55.1994:

  1. الكود يعلن 2.10.0.0 والبناء المنشور يحمل 2.10.55.1994. و
     check_for_updates يقارن ما تنشره اللوحة بما يعلنه العميل، فكل عميل
     يرى التحديث نفسه بعد تركيبه مباشرةً وينزّله في كل فحص بلا نهاية.

  2. أداة النشر تقرأ الرقم من CHANGELOG.md بصيغة «v6.2» لا يستعملها
     الملف في أي سطر، فتعود None وتتوقّف قبل أن ترسل شيئًا — أي أن
     النشر كان معطّلًا لا بطيئًا.

يُشغَّل:  python -m pytest tests/test_version_chain.py -v
"""
import importlib.util
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _publisher():
    """أداة النشر كوحدة، دون تشغيل publish_release()."""
    spec = importlib.util.spec_from_file_location(
        'publish_version', os.path.join(ROOT, 'tools', 'publish_version.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ------------------------------------------------------------ الرقم نفسه

def test_changelog_top_matches_declared_version():
    """أحدث مقطع في CHANGELOG هو الإصدار الذي يعلنه الكود.

    هذا هو الشرط الذي انكسر: نُشر بناء 2.10.55.1994 والكود يقول 2.10.0.0.
    """
    from utils.version_info import CURRENT_VERSION

    with open(os.path.join(ROOT, 'CHANGELOG.md'), encoding='utf-8') as f:
        text = f.read()

    m = re.search(r'^##\s+(\d+(?:\.\d+)+)\s', text, re.M)
    assert m, 'لا مقطع إصدار في CHANGELOG.md بالصيغة ## <رقم> — <تاريخ>'
    assert m.group(1) == CURRENT_VERSION, (
        f'CHANGELOG يقول {m.group(1)} و version_info.py يقول {CURRENT_VERSION}'
    )


def test_version_has_four_parts():
    """أربعة أجزاء لا ثلاثة: قصُّها يجعل 2.6.0.1 و2.6.0.9 متساويين."""
    from utils.version_info import CURRENT_VERSION, version_tuple

    assert len(CURRENT_VERSION.split('.')) == 4
    assert len(version_tuple()) == 4
    assert all(isinstance(p, int) for p in version_tuple())


def test_version_is_accepted_by_the_panel_shape():
    """اللوحة ترفض أي رقم خارج ^[0-9A-Za-z._-]{1,32}$ قبل أن تنشره."""
    from utils.version_info import CURRENT_VERSION

    assert re.fullmatch(r'[0-9A-Za-z._-]{1,32}', CURRENT_VERSION)


# ------------------------------------------------- أداة النشر تقرأ فعلًا

def test_publisher_reads_the_changelog():
    """الدالة كانت تعود None دائمًا، فلم تكن الأداة تنشر شيئًا أبدًا."""
    from utils.version_info import CURRENT_VERSION

    cwd = os.getcwd()
    os.chdir(ROOT)                       # CHANGELOG_FILE مسار نسبي
    try:
        data = _publisher().parse_latest_changelog()
    finally:
        os.chdir(cwd)

    assert data is not None, 'أداة النشر لا تقرأ CHANGELOG.md'
    assert data['version'] == CURRENT_VERSION
    assert data['notes'], 'الإصدار بلا ملاحظات — العميل يرى تحديثًا بلا سبب'


def test_publisher_takes_the_version_from_version_info_only():
    """مصدر واحد للرقم. مصدران يعني أنهما سيختلفان يومًا — وقد اختلفا."""
    src = open(os.path.join(ROOT, 'tools', 'publish_version.py'),
               encoding='utf-8').read()

    assert 'from utils.version_info import CURRENT_VERSION' in src
    # لا يُشتقّ الرقم من نصّ السجلّ
    assert "version = line.split(' ')[0]" not in src


def test_publisher_carries_no_secret_in_the_file():
    """المفتاح من البيئة. كان مكتوبًا نصًّا، فكان كل توقيع يُرفض."""
    src = open(os.path.join(ROOT, 'tools', 'publish_version.py'),
               encoding='utf-8').read()

    assert "API_SECRET = os.environ.get('PUBLISH_API_SECRET', '')" in src

    # لا قيمة نصّية تُسنَد إليه في أي سطر — والتعليق الذي يذكر القيمة
    # القديمة لا يُحسب، فهو سطر شرح لا إسناد.
    for line in src.splitlines():
        code = line.split('#', 1)[0]
        if re.match(r'\s*API_SECRET\s*=', code):
            assert 'os.environ' in code, f'مفتاح مكتوب في الملف: {line.strip()}'


# ------------------------------------------------ المقارنة عند العميل

def test_client_sees_no_update_when_versions_match():
    """بعد التركيب لا يُعرض التحديث نفسه ثانيةً.

    هذا هو الأثر المباشر لعدم التطابق: لو أعلنت اللوحة ما يعلنه العميل
    فلا تحديث؛ ولو أعلنت أعلى منه فتحديث واحد ينتهي بتركيبه.
    """
    from utils.update_manager import parse_version
    from utils.version_info import CURRENT_VERSION

    local = parse_version(CURRENT_VERSION)

    # الأرقام تُشتقّ من الإصدار لا تُكتب ثابتةً.
    #
    # كان «الأحدث» مكتوبًا 2.10.55.1995، فسقط الاختبار عند أول ترقية
    # تتجاوزه — سقوطًا لا يعني أن شيئًا انكسر، وهو أسوأ نوع من
    # الإخفاق: يُعلَّم تجاهلُه، فيُتجاهل يومًا يكون حقيقيًّا.
    newer = '.'.join(str(p) for p in local[:-1]) + f'.{local[-1] + 1}'
    older = '.'.join(str(p) for p in (local[0], local[1] - 1 if local[1] else 0, 0, 0))

    assert not (parse_version(CURRENT_VERSION) > local)      # نفسه
    assert parse_version(newer) > local                      # بناء أحدث
    assert not (parse_version(older) > local)                # أقدم


if __name__ == '__main__':
    import pytest
    raise SystemExit(pytest.main([__file__, '-v']))
