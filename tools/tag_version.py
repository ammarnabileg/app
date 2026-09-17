"""يضع وسم الإصدار على جيت هب — نظام النسخ الذي يُختار منه.

    python tools/tag_version.py [--push] [--force] [--dry-run]

## لماذا هذا موجود

المستودع كان بلا وسمٍ واحد. و«اختر النسخة التي يشغّلها هذا العميل»
لا معنى له ما لم يكن هناك ما يُختار منه: الوسم هو النسخة على جيت هب.

وهو ليس زينةً على `main`. عميل السحابة يُنشر من مرجع git — اللوحة
تمرّر `git_branch` إلى Coolify وتسجّله في `provisioning_jobs.image_tag`
— فالوسم هو ما يُنشر منه عميلٌ تُثبَّت نسخته، وما يُرجَع إليه عميلٌ
أعطبته نسخة. وبلا وسوم لا يوجد إلا `main`: نقطةٌ واحدة متحرّكة لا
يمكن تثبيت أحدٍ عليها ولا الرجوع إليها.

## لماذا أداة لا أمر يدوي

الوسم يجب أن يطابق ما يعلنه `utils/version_info.py` بالحرف. فوسمٌ
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

TAG_PREFIX = 'v'


def run(args, check=True):
    """أمر git، ناتجه نصًّا."""
    p = subprocess.run(['git'] + args, capture_output=True, text=True)
    if check and p.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} فشل:\n{p.stderr.strip()}")
    return p.stdout.strip()


def tag_name(version=None):
    return TAG_PREFIX + (version or CURRENT_VERSION)


def existing_tags():
    out = run(['tag', '--list'])
    return set(out.splitlines()) if out else set()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--push', action='store_true',
                    help='ادفع الوسم إلى origin (بدونه: محلّي فقط)')
    ap.add_argument('--force', action='store_true',
                    help='أعد وضع الوسم إن كان موجودًا')
    ap.add_argument('--dry-run', action='store_true',
                    help='قل ما سيحدث ولا تفعل شيئًا')
    args = ap.parse_args()

    tag = tag_name()
    head = run(['rev-parse', '--short', 'HEAD'])
    branch = run(['rev-parse', '--abbrev-ref', 'HEAD'])

    print(f"الإصدار المعلَن : {CURRENT_VERSION}  ({RELEASE_NAME}، {BUILD_DATE})")
    print(f"الوسم          : {tag}")
    print(f"على            : {branch} @ {head}")

    # شجرةٌ متّسخة تعني وسمًا لا يطابق ما سيُنشر منه: الوسم يشير إلى
    # ما في المستودع، والتعديلات غير المودعة ليست فيه.
    dirty = run(['status', '--porcelain'])
    if dirty:
        print("\nالشجرة فيها تعديلات غير مودعة:")
        print('\n'.join('  ' + line for line in dirty.splitlines()[:10]))
        raise SystemExit("\nأودِع أو انظّف أولًا — الوسم يشير إلى ما أُودع لا إلى ما على القرص.")

    if tag in existing_tags() and not args.force:
        raise SystemExit(f"\nالوسم {tag} موجود. ارفع الإصدار في "
                         "utils/version_info.py، أو استعمل --force.")

    if args.dry_run:
        print("\n[تجربة] لم يُنشأ شيء.")
        return 0

    message = f"{CURRENT_VERSION} — {RELEASE_NAME} ({BUILD_DATE})"
    run(['tag', '-a', tag, '-m', message] + (['-f'] if args.force else []))
    print(f"\nأُنشئ الوسم {tag} محلّيًّا.")

    if args.push:
        run(['push', 'origin', tag] + (['--force'] if args.force else []))
        print(f"دُفع إلى origin. صار متاحًا للاختيار في اللوحة.")
    else:
        print(f"لم يُدفع. للدفع:  python tools/tag_version.py --push")

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
