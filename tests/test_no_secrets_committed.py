"""لا مفتاح في المستودع — ولا يعود إليه.

## لماذا هذا الاختبار

سُرّبت في هذا المشروع ثلاثة مفاتيح على الأقل: مفتاح خرائط مدفوع كان
يخرج إلى متصفّح كل موظف، وتوكن استضافة يملك خوادم العملاء، ومفتاح
مزامنة. وكلّها تُدوَّر بيد صاحبها — أما منعُ عودتها فيُكتب هنا.

والتدوير وحده لا يكفي: مفتاحٌ يُدوَّر اليوم ويُلصق في الشيفرة غدًا
هو تسريبٌ آخر. وتاريخ git لا يُنسى، فالمفتاح الذي يدخله يخرج منه
إلى كل من استنسخ المستودع.

## ما يفحصه

الملفّات المتتبَّعة في git وحدها — لا مجلّد البيانات ولا السجلّات
ولا ما يتجاهله `.gitignore`: تلك لا تُدفع أصلًا، وفحصُها يُنتج
إخفاقاتٍ لا معنى لها فيُعلَّم تجاهل الاختبار.

يُشغَّل:  python -m pytest tests/test_no_secrets_committed.py -v
"""
import os
import re
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


# صيغٌ لا تُخطئ: كلٌّ منها شكلُ مفتاحٍ حقيقي لا نصٌّ عامّ.
#
# ولا يُدرَج هنا ما يُنتج إخفاقًا كاذبًا — اختبارٌ يصرخ على شيفرة
# سليمة يُعلَّم تجاهلُه، فلا يُرى يوم يصرخ على مفتاح.
PATTERNS = {
    'مفتاح مزامنة (sk_live_)':      r'sk_live_[A-Za-z0-9]{16,}',
    'توكن Coolify (رقم|نصّ طويل)':  r'\b\d+\|[A-Za-z0-9]{40,}\b',
    'توكن جيت هب':                  r'\bgh[pousr]_[A-Za-z0-9]{36,}\b',
    'مفتاح AWS':                    r'\bAKIA[0-9A-Z]{16}\b',
    'مفتاح خاصّ (PEM)':             r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
    'مفتاح Google API':             r'\bAIza[0-9A-Za-z_\-]{35}\b',
}

# امتدادات لا تُقرأ: ثنائيّةٌ يُنتج فحصها ضجيجًا.
SKIP_EXT = {'.png', '.jpg', '.jpeg', '.gif', '.ico', '.pdf', '.zip', '.xz',
            '.woff', '.woff2', '.ttf', '.eot', '.mp4', '.db', '.sqlite',
            '.pyc', '.so', '.dll', '.exe', '.jar', '.keystore', '.jks'}


def tracked_files():
    """ما يتتبّعه git فعلًا — لا ما على القرص."""
    out = subprocess.run(['git', '-C', ROOT, 'ls-files', '-z'],
                         capture_output=True, text=True)
    if out.returncode != 0:
        pytest.skip('ليس مستودع git')
    return [f for f in out.stdout.split('\0') if f]


@pytest.mark.parametrize('label,pattern', sorted(PATTERNS.items()))
def test_no_secret_shape_is_committed(label, pattern):
    rx = re.compile(pattern)
    hits = []

    for rel in tracked_files():
        if os.path.splitext(rel)[1].lower() in SKIP_EXT:
            continue
        # هذا الملفّ نفسه يحمل الصيغ تعريفًا لها.
        if rel.endswith('tests/test_no_secrets_committed.py'):
            continue

        path = os.path.join(ROOT, rel)
        try:
            with open(path, encoding='utf-8', errors='ignore') as fh:
                text = fh.read()
        except (OSError, IsADirectoryError):
            continue

        for m in rx.finditer(text):
            line = text[:m.start()].count('\n') + 1
            # لا يُطبع المفتاح: تقريرُ إخفاقٍ يحمله يُسرّبه إلى سجلّ
            # البناء — وهو مكانٌ يُقرأ أكثر من الشيفرة.
            hits.append(f'{rel}:{line}')

    assert not hits, (
        f'{label} موجود في ملفّات متتبَّعة: {", ".join(hits[:10])}. '
        'أخرجه إلى متغيّر بيئة أو ملفّ أسرار غير متتبَّع، '
        'ثم **دوّره** — ما دخل تاريخ git لا يخرج منه.')


def test_the_scan_actually_reads_the_repository():
    """قفلٌ على الاختبار نفسه.

    لو ضاق `tracked_files` يومًا — تغيّر مسارٌ، أو فشل نداء git —
    لمرّت الفحوص كلها على قائمةٍ فارغة وظهرت خضراء وهي لم تقرأ شيئًا.
    """
    files = tracked_files()
    assert len(files) > 50, f'قائمة الملفّات قصيرة بلا سبب: {len(files)}'
    assert any(f.endswith('utils/license.py') for f in files)


def test_a_planted_key_would_be_caught():
    """والقفل الثاني: أن الصيغ تُطابق مفاتيح حقيقية الشكل.

    صيغةٌ مكتوبة خطأً لا تُطابق شيئًا أبدًا — فيمرّ الاختبار دائمًا
    ولا يحرس شيئًا.
    """
    samples = {
        'مفتاح مزامنة (sk_live_)': 'sk_live_' + 'a1b2c3d4e5f6g7h8',
        'توكن جيت هب': 'ghp_' + 'A' * 36,
        'مفتاح AWS': 'AKIA' + 'B' * 16,
        'مفتاح Google API': 'AIza' + 'C' * 35,
    }
    for label, sample in samples.items():
        assert re.search(PATTERNS[label], sample), f'صيغة {label} لا تطابق مفتاحًا حقيقيًّا'


if __name__ == '__main__':
    raise SystemExit(pytest.main([__file__, '-v']))
