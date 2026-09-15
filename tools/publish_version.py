"""نشر إصدار جديد إلى لوحة onz.one.

    python tools/publish_version.py [--package PATH] [--url URL] [--build] [--dry-run]

يحسب بصمة **الحزمة التي سينزّلها العميل** ويرسلها مع رابطها، فيتحقّق
كل عميل أن ما وصله هو ما نشرناه.

ثلاثة أشياء كانت مكسورة في سلسلة التحديث كلّها، وقد فحصتُ الصفّ المنشور
فعلًا في قاعدة اللوحة فوجدتها كما هي:

  * كان يحسب بصمة dist/HRSystem/HRSystem.exe — والعميل ينزّل حزمة zip
    ويحسب بصمتها هي. فالبصمتان لملفّين مختلفين ولا يمكن أن تتطابقا أبدًا.

  * وإن لم يجد الملف طبع تحذيرًا ونشر ببصمة فارغة. والصفّ الوحيد
    المنشور اليوم — 2.5 — file_sha256 فيه NULL. والعميل بعد التشديد
    يرفض التركيب بلا بصمة، فالنشر بلا بصمة يعني إصدارًا لا يستطيع أحد
    تركيبه.

  * والرابط لم يكن يُرسل أصلًا: اللوحة كانت تركّبه بنفسها منتهيًا بـ.exe
    بينما المُركِّب يفكّ zip. والرابط المنشور فعلًا:
    https://onz.one/public/uploads/OnPointHR2.5.exe

الأخير يحتاج تعديلًا في اللوحة أيضًا (ApiController::publishRelease)،
وهو في حزمة اللوحة لا هنا.
"""

import argparse
import hashlib
import hmac
import json
import os
import re
import sys
import time
import zipfile

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.version_info import CURRENT_VERSION

API_URL = os.environ.get('PUBLISH_API_URL', 'https://onz.one/PHP/api/publish_release.php')

# يُقرأ من البيئة ولا يُكتب هنا. كان مكتوبًا في الملف نصًّا
# ("YOUR_SECRET_KEY_..."), فكان كل توقيع يُرفَض من اللوحة بـInvalid
# Signature — واللوحة نفسها ترفض الانطلاق بمفتاح فارغ، وهو الصواب.
# يجب أن يساوي API_SECRET في config/secrets.local.php على اللوحة.
API_SECRET = os.environ.get('PUBLISH_API_SECRET', '')

CHANGELOG_FILE = 'CHANGELOG.md'

# الحزمة لا الملف التنفيذي: هذا ما ينزّله update_manager ويفكّه updater.
BUILD_DIR = os.environ.get('PUBLISH_BUILD_DIR', 'dist/HRSystem')
PACKAGE_PATH = os.environ.get('PUBLISH_PACKAGE_PATH', 'dist/HRSystem.zip')
DOWNLOAD_URL = os.environ.get('PUBLISH_DOWNLOAD_URL', '')


def calculate_checksum(file_path, chunk=1 << 20):
    """بصمة ملف، مقروءًا على دفعات — الحزمة بمئات الميغابايت."""
    if not os.path.exists(file_path):
        return None
    h = hashlib.sha256()
    with open(file_path, 'rb') as f:
        for block in iter(lambda: f.read(chunk), b''):
            h.update(block)
    return h.hexdigest()


def build_package(build_dir, package_path):
    """يحزم مجلد البناء كما يتوقّعه المُركِّب: المسارات من جذر التركيب.

    لا مجلد أعلى داخل الحزمة: updater يفكّ في مجلد التركيب مباشرةً،
    فحزمةٌ جذرها HRSystem/ تُنتج HRSystem/HRSystem.exe داخل التركيب.
    """
    if not os.path.isdir(build_dir):
        return None

    os.makedirs(os.path.dirname(package_path) or '.', exist_ok=True)
    tmp = package_path + '.part'
    with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as z:
        for root, _dirs, files in os.walk(build_dir):
            for name in sorted(files):
                full = os.path.join(root, name)
                z.write(full, os.path.relpath(full, build_dir))
    os.replace(tmp, package_path)     # لا حزمة نصفها حتى تكتمل
    return package_path


def parse_latest_changelog():
    """ملاحظات إصدار CURRENT_VERSION من CHANGELOG.md.

    الرقم يأتي من utils/version_info.py وحده، لا من هنا. مصدران للرقم
    يعني أنهما سيختلفان يومًا، وقد اختلفا فعلًا: الملف أعلن 2.10.0.0
    والبناء المنشور 2.10.55.1994، فرأى كل عميل التحديث نفسه بعد تركيبه
    وأعاد تنزيله في كل فحص بلا نهاية.

    والقراءة بصيغة الملف الفعلية:  ## 2.10.55.1994 — 2026-09-14
    الصيغة القديمة كانت تبحث عن سطر يبدأ بـ'v' مثل «v6.2»، وهي صيغة لا
    يستعملها هذا الملف في أي سطر — فكانت الدالة تعود None دائمًا،
    وينتهي النشر عند أول سطر منه قبل أن يرسل شيئًا.
    """
    if not os.path.exists(CHANGELOG_FILE):
        return None

    with open(CHANGELOG_FILE, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    # ## <رقم> — <تاريخ> [· «اسم»]
    head = re.compile(r'^##\s+(\d+(?:\.\d+)+)\s')

    notes = []
    inside = False

    for line in lines:
        m = head.match(line.strip())
        if m:
            if inside:
                break                       # بدأ الإصدار الذي قبله
            if m.group(1) != CURRENT_VERSION:
                # أحدث مقطع في السجلّ ليس هو الإصدار الذي سيُنشر. النشر
                # بملاحظات إصدار آخر أسوأ من النشر بلا ملاحظات.
                print(f"❌ CHANGELOG أحدث مقطع فيه {m.group(1)} "
                      f"بينما version_info.py يقول {CURRENT_VERSION}.")
                print("   وحِّدهما قبل النشر.")
                return None
            inside = True
            continue
        if inside:
            t = line.strip()
            if t.startswith('---'):
                break
            if not t or t.startswith('#'):
                continue
            if t.startswith('- '):
                notes.append(t[2:].strip())
            elif notes:
                # سطر ملفوف من البند السابق، لا بندًا جديدًا. بدون هذا
                # كان كل سطر من ملاحظة طويلة يظهر للعميل كتغيير مستقلّ.
                notes[-1] += ' ' + t

    if not inside:
        print(f"❌ لا مقطع للإصدار {CURRENT_VERSION} في {CHANGELOG_FILE}.")
        return None

    return {'version': CURRENT_VERSION, 'notes': notes}


def check_package(path):
    """(سليمة؟، السبب). حزمةٌ لا تُفكّ تُوقف كل عميل عند التحديث."""
    if not path or not os.path.exists(path):
        return False, f'الحزمة غير موجودة: {path}'
    try:
        with zipfile.ZipFile(path) as z:
            bad = z.testzip()
            if bad is not None:
                return False, f'الحزمة تالفة عند: {bad}'
            if not [n for n in z.namelist() if not n.endswith('/')]:
                return False, 'الحزمة فارغة'
    except zipfile.BadZipFile:
        # الحالة التي كانت قائمة فعلًا: رابط ينتهي بـ.exe والمُركِّب
        # يفكّ zip.
        return False, 'الملف ليس حزمة zip — والمُركِّب لا يفكّ غيرها'
    except OSError as e:
        return False, f'تعذّرت قراءة الحزمة: {e}'
    return True, 'سليمة'


def build_payload(version, notes, checksum, size, download_url):
    return {
        'version': version,
        'notes': notes,
        'is_mandatory': True,
        # البصمة باسمين: 'sha256' هو ما تقرأه اللوحة المحدَّثة، و'checksum'
        # ما كانت تستقبله. إرسالهما معًا يعني أن النشر لا ينكسر أيّ
        # النسختين كانت على الخادم.
        'sha256': checksum,
        'checksum': checksum,
        'size': size,
        'download_url': download_url,
    }


def sign(body_json, secret):
    timestamp = str(int(time.time()))
    nonce = os.urandom(8).hex()
    message = body_json + timestamp + nonce
    signature = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()
    return {
        'Content-Type': 'application/json',
        'X-Timestamp': timestamp,
        'X-Nonce': nonce,
        'X-HMAC-Signature': signature,
    }


def publish_release(package_path=None, download_url=None, do_build=False, dry_run=False):
    print('🚀 نشر إصدار')
    print('-' * 40)

    package_path = package_path or PACKAGE_PATH
    download_url = download_url or DOWNLOAD_URL

    if not API_SECRET and not dry_run:
        print('❌ PUBLISH_API_SECRET غير مضبوط في البيئة.')
        print('   يجب أن يساوي API_SECRET في config/secrets.local.php على اللوحة.')
        return 1

    release = parse_latest_changelog()
    if not release:
        return 1
    print(f"📦 الإصدار: {release['version']} — {len(release['notes'])} ملاحظة")

    if do_build:
        print(f'🧱 بناء الحزمة من {BUILD_DIR}...')
        if not build_package(BUILD_DIR, package_path):
            print(f'❌ مجلد البناء غير موجود: {BUILD_DIR}')
            return 1

    ok, why = check_package(package_path)
    if not ok:
        # فشل مغلق: العميل يرفض التركيب بلا بصمة، فالنشر بلا حزمة
        # يُنتج إصدارًا معلنًا لا يستطيع أحد تركيبه — وهو أسوأ من ألّا
        # يُنشر شيء، لأن كل عميل يراه ويحاول ويفشل.
        print(f'❌ {why}')
        print('   لا يُنشر إصدار بلا حزمة يتحقّق منها العميل.')
        return 1

    checksum = calculate_checksum(package_path)
    size = os.path.getsize(package_path)
    print(f'✅ الحزمة: {size:,} بايت — البصمة {checksum[:12]}…')

    if not download_url:
        print('❌ رابط التنزيل غير محدَّد (--url أو PUBLISH_DOWNLOAD_URL).')
        print('   كانت اللوحة تركّب رابطًا بنفسها ينتهي بـ.exe، والمُركِّب يفكّ zip.')
        return 1
    if not download_url.lower().startswith('https://'):
        # update_manager يرفض غير https، فالنشر برابط http إصدار ميّت.
        print(f'❌ رابط التنزيل ليس HTTPS: {download_url}')
        return 1
    print(f'🔗 الرابط: {download_url}')

    payload = build_payload(release['version'], release['notes'],
                            checksum, size, download_url)
    body_json = json.dumps(payload)

    if dry_run:
        print('\n— تجربة بلا إرسال —')
        print(json.dumps(payload, ensure_ascii=False, indent=2)[:600])
        print('\nارفع الحزمة إلى الرابط أعلاه، ثم أعد التشغيل بلا --dry-run.')
        return 0

    try:
        print(f'📡 الإرسال إلى {API_URL}...')
        resp = requests.post(API_URL, data=body_json,
                             headers=sign(body_json, API_SECRET), timeout=30)
        if resp.status_code != 200:
            print(f'\n❌ HTTP {resp.status_code}: {resp.text[:300]}')
            return 1
        res = resp.json()
        if not res.get('success'):
            print(f"\n❌ رفضت اللوحة: {res.get('message')}")
            return 1
        print(f"\n✅ نُشر. {res.get('message')}")
    except Exception as e:
        print(f'\n❌ تعذّر الاتصال: {e}')
        return 1

    print('\nتأكّد أن الحزمة مرفوعة على الرابط قبل أن يفحص العملاء.')
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description='نشر إصدار جديد.')
    p.add_argument('--package', help=f'حزمة التحديث (افتراضي {PACKAGE_PATH})')
    p.add_argument('--url', help='رابط التنزيل المعلَن للعملاء (https).')
    p.add_argument('--build', action='store_true',
                   help=f'ابنِ الحزمة من {BUILD_DIR} قبل النشر.')
    p.add_argument('--dry-run', action='store_true', help='اعرض ما سيُرسل ولا ترسله.')
    a = p.parse_args(argv)
    return publish_release(a.package, a.url, a.build, a.dry_run)


if __name__ == '__main__':
    raise SystemExit(main())
