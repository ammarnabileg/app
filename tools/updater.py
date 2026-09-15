"""تركيب حزمة تحديث على جهاز العميل — أو التراجع عنها كاملةً.

يُبنى بـPyInstaller ملفًا واحدًا (انظر build_app.py) ويُشغَّل بعد أن
يُغلق التطبيق نفسه، فيستبدل ملفاته ثم يعيد تشغيله:

    updater.py <zip> <install_dir> <exe_name> [--sha256 HEX] [--no-pause]

كانت النسخة الأولى تفكّ الحزمة فوق مجلد التركيب مباشرةً:

    zip_ref.extractall(install_dir)

وهذا يكتب الملفات واحدًا واحدًا. فإن تعثّر عند الملف المئة — قفلٌ من
ويندوز، أو قرص امتلأ — كان نصف التركيب جديدًا ونصفه قديمًا، ثم يُقال
للعميل «أغلق التطبيق وأعد المحاولة» ويُترك على هذا الحال. جرّبتُه:
ملفان صارا جديدين وثالث بقي قديمًا في فكٍّ واحد منقطع. والنسخة
المنفصلة عن الشبكة لا خادمَ تعيد التنزيل منه ولا رجعةَ لها.

الآن على ثلاث مراحل، لا يُمسّ التركيب إلا في الثانية:

    ١ — تحقّق: الحزمة تُفتح وتُقرأ كاملةً (testzip)، وبصمتها تُطابَق إن
        أُعطيت، والقرص يتّسع لها. الفشل هنا لا يترك أثرًا.
    ٢ — تجهيز: تُفكّ في مجلد جانبي. الفشل هنا يمحو المجلد الجانبي وحده.
    ٣ — استبدال: كل ملف يُزاح إلى مجلد تراجع قبل أن يحلّ الجديد محلّه.
        الفشل هنا يُعيد كل ما أُزيح، فيعود التركيب إلى ما كان.

فالتركيب في كل لحظة إمّا القديم كلّه أو الجديد كلّه.
"""

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import time
import zipfile

STAGING = '.update-staging'
ROLLBACK = '.update-rollback'

_log_path = None


def log(msg):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [Updater] {msg}"
    print(line)
    # المسار كان نسبيًا: يذهب السجل إلى مجلد العمل أيًّا كان، وهو آخر
    # مكان يُفتّش فيه يوم يُسأل العميل «أين سجل التحديث؟».
    if _log_path:
        try:
            with open(_log_path, 'a', encoding='utf-8') as f:
                f.write(line + '\n')
        except OSError:
            pass


def file_sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


# ------------------------------------------------------- ١ — التحقّق

def check_package(zip_path, expected_sha256=None):
    """(صالحة؟، الملفات، الحجم المفكوك). لا تكتب شيئًا."""
    if not os.path.isfile(zip_path):
        return False, f'الحزمة غير موجودة: {zip_path}', 0

    if expected_sha256:
        want = expected_sha256.strip().lower()
        got = file_sha256(zip_path)
        if got != want:
            # بادئة وحدها في السجل: البصمة كاملةً تُغني من يقرأ السجل عن
            # الحزمة الأصلية.
            return False, f'بصمة الحزمة لا تطابق ({got[:12]}… بدل {want[:12]}…)', 0

    try:
        with zipfile.ZipFile(zip_path) as z:
            bad = z.testzip()
            if bad is not None:
                return False, f'الحزمة تالفة عند: {bad}', 0
            names = [n for n in z.namelist() if not n.endswith('/')]
            size = sum(i.file_size for i in z.infolist())
    except zipfile.BadZipFile:
        return False, 'الملف ليس حزمة zip صالحة', 0
    except OSError as e:
        return False, f'تعذّرت قراءة الحزمة: {e}', 0

    if not names:
        return False, 'الحزمة فارغة', 0

    return True, names, size


def enough_space(install_dir, needed):
    """القرص الممتلئ هو ما كان يقطع الفكّ في منتصفه.

    الضِعف: نسخة في مجلد التجهيز وأخرى في مجلد التراجع.
    """
    try:
        free = shutil.disk_usage(install_dir).free
    except OSError:
        return True, 0        # تعذّر القياس: لا نمنع التحديث بسببه
    return free > needed * 2 + (50 * 1024 * 1024), free


# ------------------------------------------------------ ٢ — التجهيز

def stage(zip_path, staging_dir):
    if os.path.exists(staging_dir):
        shutil.rmtree(staging_dir, ignore_errors=True)
    os.makedirs(staging_dir, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(staging_dir)
    return staging_dir


def staged_files(staging_dir):
    """المسارات النسبية لكل ما فُكّ، مرتَّبةً."""
    out = []
    for root, _dirs, files in os.walk(staging_dir):
        for name in files:
            full = os.path.join(root, name)
            out.append(os.path.relpath(full, staging_dir))
    return sorted(out)


# ----------------------------------------------------- ٣ — الاستبدال

def swap_in(staging_dir, install_dir, rollback_dir, rels):
    """يحلّ الجديد محلّ القديم، وكل مُزاح محفوظ.

    يعيد (نجح؟، وصف). عند الإخفاق يكون كل ما أُزيح قد رُدّ إلى مكانه.

    الإزاحة قبل الكتابة ليست احتياطًا فقط: ويندوز يمنع الكتابة على ملف
    تنفيذي يعمل ويسمح بإعادة تسميته. وهذا الملف نفسه — tools/updater.exe
    — يأتي في الحزمة، فكان extractall يحاول الكتابة فوق نفسه وهو يعمل
    فيُمنع. والإزاحة تنجح لأن مجلدَي التجهيز والتراجع داخل مجلد التركيب،
    فالنقل إعادةُ تسمية على القرص نفسه.
    """
    moved = []          # (rel, rollback_path|None) — None: ملف جديد لا بديل له
    try:
        for rel in rels:
            src = os.path.join(staging_dir, rel)
            dst = os.path.join(install_dir, rel)
            os.makedirs(os.path.dirname(dst) or install_dir, exist_ok=True)

            saved = None
            if os.path.exists(dst):
                saved = os.path.join(rollback_dir, rel)
                os.makedirs(os.path.dirname(saved) or rollback_dir, exist_ok=True)
                shutil.move(dst, saved)

            # يُسجَّل هنا لا بعد نقل الجديد: لو تعثّر النقل التالي لكان
            # القديم قد أُزيح ولم يُسجَّل، فلا يردّه أحد ويختفي الملف من
            # التركيب أصلًا. أمسك الاختبار هذا عليّ.
            moved.append((rel, saved))
            shutil.move(src, dst)
        return True, f'{len(moved)} ملفًا'

    except Exception as e:
        log(f'تعثّر الاستبدال عند «{rel}»: {e} — يُعاد ما سبق.')
        restored, lost = 0, []
        for rel_done, saved in reversed(moved):
            dst = os.path.join(install_dir, rel_done)
            try:
                if saved:
                    if os.path.exists(dst):
                        os.remove(dst)
                    shutil.move(saved, dst)
                    restored += 1
                elif os.path.exists(dst):
                    os.remove(dst)      # كان جديدًا: لا أثر له قبل التحديث
                    restored += 1
            except OSError as ee:
                lost.append(f'{rel_done}: {ee}')

        if lost:
            log(f'تعذّر ردّ {len(lost)} ملفًا — انظر {rollback_dir}:')
            for item in lost[:10]:
                log(f'  {item}')
            return False, 'انقطع الاستبدال ولم يكتمل الردّ'

        log(f'رُدّ {restored} ملفًا. التركيب كما كان قبل التحديث.')
        return False, f'انقطع الاستبدال: {e}'


# ------------------------------------------------------------- المسار

def _is_ours(zip_path, install_dir):
    """أنحذف الحزمة بعد التركيب؟

    داخل مجلد التركيب وحده — وهناك بالضبط يضعها update_manager باسم
    update_pkg.zip. وما جاء من غيره فهو نسخة العميل: قد تكون على ذاكرة
    حملها إلى جهاز لا شبكة فيه، وهي كل ما يملك.
    """
    z = os.path.abspath(zip_path)
    base = os.path.abspath(install_dir)
    try:
        return os.path.commonpath([z, base]) == base
    except ValueError:      # أقراص مختلفة على ويندوز
        return False


def run(zip_path, install_dir, exe_name, expected_sha256=None, retries=30):
    install_dir = os.path.abspath(install_dir)
    staging_dir = os.path.join(install_dir, STAGING)
    rollback_dir = os.path.join(install_dir, ROLLBACK)

    log('------------------------------------------')
    log(f'الحزمة: {zip_path}')
    log(f'مجلد التركيب: {install_dir}')

    ok, info, size = check_package(zip_path, expected_sha256)
    if not ok:
        log(f'رُفض التحديث: {info}')
        log('لم يُمسّ التركيب.')
        return 1
    log(f'الحزمة سليمة: {len(info)} ملفًا، {size:,} بايت بعد الفكّ.')

    roomy, free = enough_space(install_dir, size)
    if not roomy:
        log(f'المساحة لا تكفي: المتاح {free:,} بايت.')
        log('لم يُمسّ التركيب.')
        return 1

    try:
        stage(zip_path, staging_dir)
    except Exception as e:
        log(f'تعذّر تجهيز الحزمة: {e}')
        shutil.rmtree(staging_dir, ignore_errors=True)
        log('لم يُمسّ التركيب.')
        return 2

    rels = staged_files(staging_dir)
    log(f'جُهّزت جانبًا: {len(rels)} ملفًا. يبدأ الاستبدال.')

    if os.path.exists(rollback_dir):
        shutil.rmtree(rollback_dir, ignore_errors=True)
    os.makedirs(rollback_dir, exist_ok=True)

    # القفل من التطبيق يزول بإغلاقه؛ والمحاولة تبدأ من تركيب سليم في كل
    # مرة لأن الردّ يسبقها.
    done, detail = False, ''
    for attempt in range(1, retries + 1):
        done, detail = swap_in(staging_dir, install_dir, rollback_dir, rels)
        if done:
            break
        if 'لم يكتمل الردّ' in detail:
            break
        if attempt % 5 == 1:
            log(f'ملفات مقفلة — أغلق التطبيق. محاولة {attempt}/{retries}')
        time.sleep(1)

    if not done:
        log(f'أخفق التحديث: {detail}')
        log(f'النسخة القديمة تعمل. الحزمة باقية: {zip_path}')
        shutil.rmtree(staging_dir, ignore_errors=True)
        return 3

    log(f'اكتمل الاستبدال: {detail}')
    shutil.rmtree(staging_dir, ignore_errors=True)
    shutil.rmtree(rollback_dir, ignore_errors=True)

    if _is_ours(zip_path, install_dir):
        try:
            os.remove(zip_path)
            log('حُذفت الحزمة المؤقتة.')
        except OSError as e:
            log(f'بقيت الحزمة: {e}')
    else:
        log(f'الحزمة ليست لنا فتُركت: {zip_path}')

    exe_path = os.path.join(install_dir, exe_name)
    if os.path.exists(exe_path):
        log(f'إعادة تشغيل {exe_name}...')
        try:
            cmd = ([sys.executable, exe_path] if exe_name.lower().endswith('.py')
                   else [exe_path])
            subprocess.Popen(cmd, cwd=install_dir)
        except OSError as e:
            log(f'تعذّرت إعادة التشغيل: {e} — شغّله يدويًا.')
    else:
        log(f'لم يُعثر على {exe_path} — شغّل التطبيق يدويًا.')

    log('تمّ التحديث.')
    return 0


def main(argv=None):
    global _log_path

    p = argparse.ArgumentParser(description='تركيب حزمة تحديث.')
    p.add_argument('zip_path')
    p.add_argument('install_dir')
    p.add_argument('exe_name')
    p.add_argument('--sha256', help='بصمة الحزمة المعلَنة، إن وُجدت.')
    p.add_argument('--no-pause', action='store_true')
    p.add_argument('--retries', type=int, default=30)
    args = p.parse_args(argv)

    if os.path.isdir(args.install_dir):
        _log_path = os.path.join(os.path.abspath(args.install_dir), 'update.log')

    code = run(args.zip_path, args.install_dir, args.exe_name,
               args.sha256, args.retries)

    if code and not args.no_pause and sys.stdin and sys.stdin.isatty():
        try:
            input('\nاضغط Enter للخروج...')
        except (EOFError, KeyboardInterrupt):
            pass
    return code


if __name__ == '__main__':
    raise SystemExit(main())
