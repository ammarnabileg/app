"""إزالة النظام عن هذا الجهاز — مع الإبقاء على البيانات.

يُبنى بـPyInstaller ملفًا واحدًا (انظر build_app.py)، ويُشغَّل غالبًا على
جهاز صار النظام فيه لا يعمل. فلا يستورد شيئًا من التطبيق: مكتبة بايثون
القياسية وحدها، حتى يبقى قادرًا على إنقاذ البيانات ولو انكسر كل ما عداه.

ثلاثة أشياء تغيّرت عن النسخة الأولى، وكلّها عن فقد بيانات:

  * كان المجلد مكتوبًا في الملف: C:\\ProgramData\\HRSystem. ومن ركّب على
    مجلد آخر (HR_DATA_DIR) كان يُقال له «لا شيء لتنظيفه» وقاعدته سليمة
    في مكان لم يُنظر فيه، فيمضي ويمسح المجلد بيده.

  * وكان «النسخ» إعادةَ تسمية للملف وحده. والقاعدة تعمل بـWAL: آخر ما
    كُتب يكون في hr_system.db-wal لا في الملف، فتُنقل القاعدة وتُترك
    معاملاتُ اليوم الأخير خلفها، والرسالة تقول «نُسخت بنجاح».

  * وكان تعذُّر النسخ يُطبع ثم يُقال بعده «تمت الإزالة» على أي حال.

الآن: لقطة متّسقة عبر واجهة النسخ في SQLite (تضمّ ما في WAL)، ثم فحص
سلامة عليها، ولا يُمسّ الأصل إلا بعد أن تنجح الاثنتان.

    python tools/uninstall.py [--data-dir PATH] [--yes] [--keep-database]
"""

import argparse
import os
import sqlite3
import sys
from datetime import datetime

DB_NAME = 'hr_system.db'

# كلمة يكتبها المستخدم كاملةً. لا y/n: الضغط على مفتاح بالغلط لا ينبغي
# أن ينقل قاعدة بيانات شركة.
CONFIRM_WORD = 'remove'


def resolve_data_dir(explicit=None):
    """المجلد نفسه الذي يستعمله النظام — بالترتيب نفسه في utils/db.py.

    مصدر واحد للحقيقة في السلوك، لا في الشيفرة: لا يمكن الاستيراد من
    utils هنا لأن هذا الملف يُبنى وحده.
    """
    if explicit:
        return os.path.abspath(explicit)
    env = os.environ.get('HR_DATA_DIR')
    if env:
        return os.path.abspath(env)
    if os.name == 'nt':
        return r'C:\ProgramData\HRSystem'
    return '/app/data'


def _unique(path):
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    stamp = datetime.now().strftime('%H%M%S')
    cand = f'{base}_{stamp}{ext}'
    n = 2
    while os.path.exists(cand):
        cand = f'{base}_{stamp}_{n}{ext}'
        n += 1
    return cand


def snapshot(db_path, dest_path):
    """لقطة كاملة متّسقة، ولو كان النظام يعمل الآن.

    src.backup تقرأ عبر محرّك SQLite نفسه، فتشمل ما لم يُدمج بعدُ من
    WAL؛ ونسخ الملف بـshutil لا يشمله.
    """
    src = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True, timeout=30)
    try:
        dst = sqlite3.connect(dest_path)
        try:
            src.backup(dst)
            # اللقطة ترث WAL عن الأصل، فتبقى إلى جانبها -wal و-shm. ومن
            # ينسخ ملف القاعدة وحده إلى ذاكرة يترك نصفها خلفه ولا يدري.
            # هنا يصير الملف قائمًا بذاته.
            dst.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            dst.execute('PRAGMA journal_mode=DELETE')
        finally:
            dst.close()
    finally:
        src.close()
    return dest_path


def _tables_phrase(n):
    if n == 1:
        return 'جدول واحد'
    if n == 2:
        return 'جدولان'
    if n <= 10:
        return f'{n} جداول'
    return f'{n} جدولًا'


def verify(path):
    """(سليمة؟، وصف). تُقرأ اللقطة وحدها — لا الأصل."""
    try:
        con = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    except sqlite3.Error as e:
        return False, f'تعذّر فتح اللقطة: {e}'
    try:
        row = con.execute('PRAGMA integrity_check').fetchone()
        if not row or row[0] != 'ok':
            return False, f'فحص السلامة: {row[0] if row else "بلا نتيجة"}'
        tables = con.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'").fetchone()[0]
        if tables == 0:
            return False, 'اللقطة بلا جداول'
        return True, _tables_phrase(tables)
    except sqlite3.Error as e:
        return False, f'تعذّرت قراءة اللقطة: {e}'
    finally:
        con.close()


def confirm(db_path, assume_yes):
    if assume_yes:
        return True
    print(f'\nستُنقل قاعدة البيانات من: {db_path}')
    print('نسخة منها تبقى في المجلد نفسه؛ لا يُحذف شيء آخر.')
    try:
        typed = input(f'اكتب «{CONFIRM_WORD}» للمتابعة، أو Enter للإلغاء: ').strip()
    except (EOFError, KeyboardInterrupt):
        return False
    return typed.lower() == CONFIRM_WORD


def uninstall(data_dir, assume_yes=False, keep_database=False):
    """يعيد 0 عند النجاح، ورقمًا غير الصفر عند الإخفاق."""
    print('--- إزالة نظام الموارد البشرية ---')
    print(f'مجلد البيانات: {data_dir}')

    if not os.path.isdir(data_dir):
        print('المجلد غير موجود — لا شيء يُنقل.')
        print('إن كنت ركّبت على مجلد آخر، مرّره: --data-dir "المسار"')
        return 0

    db_path = os.path.join(data_dir, DB_NAME)
    if not os.path.exists(db_path):
        print(f'لا توجد قاعدة باسم {DB_NAME} في هذا المجلد.')
        print('إن كنت ركّبت على مجلد آخر، مرّره: --data-dir "المسار"')
        return 0

    if not confirm(db_path, assume_yes):
        print('أُلغيت. لم يُمسّ شيء.')
        return 1

    dest = _unique(os.path.join(
        data_dir, f'hr_system_backup_{datetime.now().strftime("%Y_%m_%d")}.db'))

    try:
        snapshot(db_path, dest)
    except sqlite3.OperationalError as e:
        print(f'\nتعذّر أخذ اللقطة: {e}')
        print('أغلق النظام والمشغّل (Launcher) ثم أعد المحاولة.')
        print('لم يُمسّ شيء.')
        return 2
    except Exception as e:
        print(f'\nتعذّر أخذ اللقطة: {e}')
        print('لم يُمسّ شيء.')
        return 2

    ok, detail = verify(dest)
    if not ok:
        print(f'\nاللقطة غير صالحة ({detail}). لم تُحذف القاعدة الأصلية.')
        print(f'اللقطة الناقصة: {dest}')
        return 3

    size = os.path.getsize(dest)
    print(f'\nنسخة سليمة ({detail}، {size:,} بايت):')
    print(f'  {dest}')

    if keep_database:
        print('\nالقاعدة الأصلية باقية كما هي (--keep-database).')
        return 0

    # الأصل ولواحقه معًا: ملف WAL يتيم إلى جانب قاعدة جديدة يضلّل من
    # يفتش عن البيانات لاحقًا.
    removed, failed = [], []
    for suffix in ('', '-wal', '-shm'):
        p = db_path + suffix
        if not os.path.exists(p):
            continue
        try:
            os.remove(p)
            removed.append(os.path.basename(p))
        except OSError as e:
            failed.append(f'{os.path.basename(p)}: {e}')

    if failed:
        print('\nتعذّر حذف بعض الملفات — أغلق النظام والمشغّل ثم أعد المحاولة:')
        for f in failed:
            print(f'  {f}')
        print('النسخة أعلاه سليمة وباقية.')
        return 4

    print(f'حُذفت من مكانها: {", ".join(removed)}')
    print('\nتمت الإزالة. بياناتك في الملف أعلاه — احتفظ به قبل حذف المجلد.')
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description='إزالة النظام مع الإبقاء على البيانات.')
    p.add_argument('--data-dir', help='مجلد البيانات إن كان غير الافتراضي.')
    p.add_argument('--yes', action='store_true', help='بلا سؤال تأكيد.')
    p.add_argument('--keep-database', action='store_true',
                   help='خذ النسخة ولا تحذف القاعدة الأصلية.')
    p.add_argument('--no-pause', action='store_true', help='لا تنتظر Enter في النهاية.')
    args = p.parse_args(argv)

    code = uninstall(resolve_data_dir(args.data_dir), args.yes, args.keep_database)

    # نافذة PyInstaller تُغلق فورًا وإلا لم يُقرأ مسار النسخة.
    if not args.no_pause and sys.stdin and sys.stdin.isatty():
        try:
            input('\nاضغط Enter للخروج...')
        except (EOFError, KeyboardInterrupt):
            pass
    return code


if __name__ == '__main__':
    raise SystemExit(main())
