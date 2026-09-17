"""يجعل نسخةً قابلةً للاختيار على جيت هب — وسمًا وفرعَ إصدار.

    python tools/tag_version.py [--push] [--force] [--dry-run]

## لماذا هذا موجود

المستودع كان بلا وسمٍ واحد. و«اختر النسخة التي يشغّلها هذا العميل»
لا معنى له ما لم يكن هناك ما يُختار منه.

وهو ليس زينةً على `main`. عميل السحابة يُنشر من مرجع git — اللوحة
تمرّر المرجع إلى Coolify في حقلٍ اسمه `git_branch` وتسجّله في
`provisioning_jobs.image_tag` — فالمرجع هو ما يُنشر منه عميلٌ
تُثبَّت نسخته، وما يُرجَع إليه عميلٌ أعطبته نسخة. وبلا مراجع لا
يوجد إلا `main`: نقطةٌ واحدة متحرّكة لا يمكن تثبيت أحدٍ عليها ولا
الرجوع إليها.

## لماذا وسمٌ **وفرع** لا وسمٌ وحده

الوسم هو الصواب اصطلاحًا: مرجعٌ لا يتحرّك. لكن حقل Coolify يقرأ
`git_branch`، والفرع أضمن قبولًا عنده. والأهمّ عمليًّا: بعض
الصلاحيات تسمح بدفع الفروع وتمنع الوسوم — وهي الحال في بيئة
التطوير السحابية التي بُني منها هذا. فلو تعثّر دفع الوسم، يمضي
الفرع، ويبقى ما تحتاجه اللوحة قائمًا.

والاسم واحد في الحالين: `v` + الرقم. فاللوحة تبني المرجع من رقم
النسخة ولا تسأل أهو وسمٌ أم فرع.

**وفرع الإصدار مجمَّد.** لا يُودَع فيه شيء بعد إنشائه: عملاءُ
مثبَّتون عليه، فإيداعٌ واحد فيه يغيّر ما يشغّلونه دون أن يطلبه
أحد — وهو بالضبط ما يحرسه الوسم بطبيعته ويحتاج الفرعُ انضباطًا
ليحرسه.

## لماذا أداة لا أمر يدوي

المرجع يجب أن يطابق ما يعلنه `utils/version_info.py` بالحرف. فمرجعٌ
باسمٍ آخر يجعل اللوحة تُسند نسخةً لا وجود لها في جيت هب، فيفشل نشر
عميل — والعطب يظهر عند العميل لا عند من أخطأ في الكتابة.

يُشغَّل بلا `--push` أولًا: يقول ما سيفعل ولا يلمس شيئًا بعيدًا.
"""

import argparse
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.version_info import BUILD_DATE, CURRENT_VERSION, RELEASE_NAME

REF_PREFIX = 'v'


def run(args, check=True):
    """أمر git، ناتجه نصًّا."""
    p = subprocess.run(['git'] + args, capture_output=True, text=True)
    if check and p.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} فشل:\n{p.stderr.strip()}")
    return p.stdout.strip()


def try_run(args):
    """مثلها، لكن يعيد (نجح، المخرجات) بدل أن يوقف كل شيء."""
    p = subprocess.run(['git'] + args, capture_output=True, text=True)
    return p.returncode == 0, (p.stdout + p.stderr).strip()


def ref_name(version=None):
    return REF_PREFIX + (version or CURRENT_VERSION)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--push', action='store_true',
                    help='ادفع المرجعين إلى origin (بدونه: محلّي فقط)')
    ap.add_argument('--force', action='store_true',
                    help='أعد وضع المرجع إن كان موجودًا')
    ap.add_argument('--dry-run', action='store_true',
                    help='قل ما سيحدث ولا تفعل شيئًا')
    args = ap.parse_args()

    ref = ref_name()
    head = run(['rev-parse', '--short', 'HEAD'])
    branch = run(['rev-parse', '--abbrev-ref', 'HEAD'])

    print(f"الإصدار المعلَن : {CURRENT_VERSION}  ({RELEASE_NAME}، {BUILD_DATE})")
    print(f"المرجع         : {ref}   (وسمًا وفرعَ إصدار)")
    print(f"على            : {branch} @ {head}")

    # شجرةٌ متّسخة تعني مرجعًا لا يطابق ما سيُنشر منه: المرجع يشير
    # إلى ما في المستودع، والتعديلات غير المودعة ليست فيه.
    dirty = run(['status', '--porcelain'])
    if dirty:
        print("\nالشجرة فيها تعديلات غير مودعة:")
        print('\n'.join('  ' + line for line in dirty.splitlines()[:10]))
        raise SystemExit("\nأودِع أو انظّف أولًا — المرجع يشير إلى ما أُودع لا إلى ما على القرص.")

    tags = set(run(['tag', '--list']).splitlines())
    if ref in tags and not args.force:
        raise SystemExit(f"\nالمرجع {ref} موجود. ارفع الإصدار في "
                         "utils/version_info.py، أو استعمل --force.")

    if args.dry_run:
        print("\n[تجربة] لم يُنشأ شيء.")
        return 0

    message = f"{CURRENT_VERSION} — {RELEASE_NAME} ({BUILD_DATE})"
    run(['tag', '-a', ref, '-m', message] + (['-f'] if args.force else []))
    print(f"\nأُنشئ الوسم {ref} محلّيًّا.")

    if not args.push:
        print(f"لم يُدفع. للدفع:  python tools/tag_version.py --push")
        return 0

    # الفرع أولًا: هو ما تقرؤه اللوحة فعلًا، والوسم سجلٌّ فوقه.
    ok_branch, out_branch = try_run(
        ['push', 'origin', f'HEAD:refs/heads/{ref}']
        + (['--force'] if args.force else []))
    print(("دُفع فرع الإصدار " + ref) if ok_branch
          else f"تعذّر دفع فرع الإصدار:\n  {out_branch.splitlines()[-1] if out_branch else '?'}")

    ok_tag, out_tag = try_run(['push', 'origin', ref]
                              + (['--force'] if args.force else []))
    if ok_tag:
        print(f"ودُفع الوسم {ref}")
    else:
        # ليس فشلًا يوقف كل شيء: اللوحة تقرأ المرجع بالاسم، والفرع
        # يحمل الاسم نفسه. فتُقال الحقيقة ويمضي العمل.
        print(f"لم يُدفع الوسم (صلاحية الدفع تمنع الوسوم غالبًا):"
              f"\n  {out_tag.splitlines()[-1] if out_tag else '?'}"
              f"\n  والفرع {ref} يكفي اللوحةَ — الوسم سجلٌّ لا أكثر.")

    if not ok_branch:
        raise SystemExit("\nفرع الإصدار لم يُدفع — واللوحة تحتاجه. عالج السبب أعلاه.")

    print(f"\n{ref} صار متاحًا للاختيار في اللوحة.")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
