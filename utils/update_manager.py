import requests
import os
import sys
import subprocess
import threading
from utils.version_info import CURRENT_VERSION

UPDATE_API_URL = "https://onz.one/PHP/version_api.php"

# بصمة الإصدار المعلَن، تُملأ من ردّ الفحص وتُتحقَّق قبل التركيب.
_EXPECTED_SHA256 = ''


def file_sha256(path, chunk=1 << 20):
    """بصمة ملف، مقروءًا على دفعات.

    على دفعات لا دفعةً واحدة: حزمة التحديث تُقاس بمئات الميغابايت، وقراءتها
    كاملةً إلى الذاكرة على جهاز عميل قديم هي نفسها سبب فشل التحديث.
    """
    import hashlib
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()

def parse_version(v):
    """تحويل الإصدار إلى صفٍّ للمقارنة.

    يُكمَّل إلى أربعة أجزاء لا ثلاثة: صيغة الإصدار صارت
    MAJOR.MINOR.PATCH.BUILD، وقصّها عند ثلاثة يجعل 2.6.0.1 و2.6.0.9
    متساويين — فلا يُرى إصلاح عاجل تحديثًا.

    والمقارنة على صفٍّ لا قائمة: القائمة تُقارن عنصرًا عنصرًا كذلك، لكن
    الصفّ يمنع تعديلها سهوًا.
    """
    parts = [int(x) for x in str(v).split('.') if x.isdigit()]
    while len(parts) < 4:
        parts.append(0)
    return tuple(parts[:4])

def _license_key_param():
    """مفتاح الترخيص ليُسأل عن نسخة *هذا* العميل لا عن نسخةٍ للجميع.

    اللوحة تسمح بتثبيت عميلٍ على نسخة بعينها — لعميلٍ يؤجّل التحديث،
    أو تُجرَّب عليه نسخةٌ قبل الباقين، أو أعطبته نسخةٌ فيُرجَع. وبلا
    إرسال المفتاح لا تعرف اللوحة من السائل، فتردّ بالأحدث للجميع
    ويصير التثبيت حبرًا على ورق.

    ولا يُسقط الفحص إن تعذّرت القراءة: السؤال بلا مفتاح يعيد الأحدث،
    وهو سلوك ما قبل التثبيت تمامًا — أما رمي الاستثناء فيقطع
    التحديثات كلها عن جهازٍ لأن قاعدته المحلّية تعثّرت لحظة.
    """
    try:
        from utils.license import get_saved_license_key
        key = (get_saved_license_key() or '').strip()
        return {'license_key': key} if key else {}
    except Exception:
        return {}


def _ask_panel():
    """يسأل اللوحة عن النسخة — بـPOST، وبـGET إن لزم.

    **POST أولًا** لأن المفتاح يسافر في الجسم لا في سطر العنوان: سطر
    العنوان يُكتب في سجلّات الخادم والوسطاء كاملًا، فمفتاحٌ فيه يصير
    مفتاحًا في ملفّ نصّي يقرؤه من يبلغ السجلّات.

    **وGET احتياطًا** لترتيبٍ سيقع: اللوحة كانت تسجّل هذا المسار
    لـGET وحده، فنظامٌ حُدِّث قبل أن تُحدَّث لوحته سيقابل 404 —
    ويتوقّف عن فحص التحديثات كلها، بلا أي عَرَض ظاهر لأحد. والسقوط
    إلى GET بلا مفتاح هو الصواب هنا لا مجرّد اللطف: لوحةٌ قديمة لا
    تعرف التثبيت أصلًا، فأحدث نسخة هي كل ما لديها لتقوله.
    """
    resp = requests.post(UPDATE_API_URL, data=_license_key_param(), timeout=5)
    if resp.status_code == 200:
        return resp
    return requests.get(UPDATE_API_URL, timeout=5)


def check_for_updates():
    """
    Returns: (update_available: bool, download_url: str, notes: str, mandatory: bool)
    """
    try:
        resp = _ask_panel()
        if resp.status_code == 200:
            data = resp.json()
            if data.get('success'):
                remote_ver_str = data['version']
                download_url = data['download_url']
                notes = data.get('release_notes', '')
                mandatory = data.get('mandatory', False)
                
                local = parse_version(CURRENT_VERSION)
                remote = parse_version(remote_ver_str)
                
                # Compare semantic versions
                if remote > local:
                    # البصمة تُنقل مع الرابط لا بعده: من يفصل الاثنين
                    # يفتح نافذةً يُركَّب فيها ملف لم يُتحقَّق منه.
                    global _EXPECTED_SHA256
                    _EXPECTED_SHA256 = (data.get('sha256') or '').strip().lower()
                    return True, download_url, notes, mandatory
                    
    except Exception as e:
        print(f"[UpdateManager] Error checking updates: {e}")
        
    return False, None, None, False

def download_and_install_update(url, install_dir=None, expected_sha256=None):
    """ينزّل الحزمة، **يتحقّق منها**، ثم يسلّمها للمحدِّث.

    التحقّق ليس احتياطًا زائدًا: المحدِّث يفكّ ما يُعطى له فوق مجلّد
    التركيب ثم يشغّل ما فيه. فملفٌ لم يُتحقَّق منه هو تنفيذُ شيفرةٍ على
    كل جهاز عميل، ومن يتحكّم في الرابط يومًا — خادمٌ مخترَق، أو ردٌّ
    مزوَّر — يتحكّم فيهم جميعًا.

    والبصمة محسوبة ومخزَّنة منذ أول يوم؛ لم تكن تخرج من الخادم فحسب.

    بلا بصمة لا تركيب. والفشل مغلق عن قصد: لو سقطنا إلى التركيب حين
    تغيب البصمة، لأسقطها المهاجم من ردّه وانتهى الأمر. والخادم واحد
    نملكه، فتحديثه أرخص من ترك البابين مفتوحين.
    """
    if not install_dir:
        install_dir = os.getcwd()

    expected = (expected_sha256 or _EXPECTED_SHA256 or '').strip().lower()
    if len(expected) != 64 or not all(c in '0123456789abcdef' for c in expected):
        print("[UpdateManager] رُفض التحديث: الخادم لم يرسل بصمة للحزمة.")
        return False

    # HTTPS وحده: رابط http يعني حزمةً يستطيع من على الشبكة استبدالها،
    # والبصمة نفسها وصلت عبر القناة ذاتها.
    if not str(url).lower().startswith('https://'):
        print("[UpdateManager] رُفض التحديث: رابط التنزيل ليس HTTPS.")
        return False

    try:
        print(f"[UpdateManager] Downloading update from {url}...")
        resp = requests.get(url, stream=True, timeout=60)
        if resp.status_code != 200:
            print("[UpdateManager] Failed to download file.")
            return False

        # Save to temp zip
        zip_path = os.path.join(install_dir, "update_pkg.zip")
        with open(zip_path, 'wb') as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)

        actual = file_sha256(zip_path)
        if actual != expected:
            # يُحذف فورًا: ملفٌ مرفوض يبقى في مجلّد التركيب هو ملفٌ
            # سيُفَكّ يومًا بالخطأ.
            try:
                os.remove(zip_path)
            except OSError:
                pass
            print("[UpdateManager] رُفض التحديث: بصمة الحزمة لا تطابق المعلَن.")
            print(f"[UpdateManager]   المتوقَّع {expected[:12]}… والواصل {actual[:12]}…")
            return False

        print("[UpdateManager] Download complete and verified. Launching updater...")
        
        # Determine app executable to restart
        app_name = os.path.basename(sys.argv[0])
        updater_cwd = os.path.join(install_dir, "tools")
        
        if getattr(sys, 'frozen', False):
            # Running as EXE -> Expect tools/updater.exe
            updater_exe = os.path.join(install_dir, "tools", "updater.exe")
            if os.path.exists(updater_exe):
                # تُمرَّر البصمة ثانيةً ليتحقّق المُركِّب بنفسه: بين
                # التحقّق هنا وفكّ الحزمة هناك عمليتان وملفٌ على القرص.
                # والنسخة القديمة من updater.exe تتجاهل ما زاد عن ثلاثة
                # معامِلات، فالتمرير آمن على من لم يُحدِّث بعد.
                subprocess.Popen([updater_exe, zip_path, install_dir, app_name,
                                  '--sha256', expected], cwd=install_dir)
                return True
            else:
                print(f"[UpdateManager] Error: {updater_exe} not found.")
                return False
        else:
            # Running as Python Script
            python_exe = sys.executable
            updater_script = os.path.join(install_dir, "tools", "updater.py")
            subprocess.Popen([python_exe, updater_script, zip_path, install_dir, app_name,
                              '--sha256', expected], cwd=install_dir)
            return True
        
    except Exception as e:
        print(f"[UpdateManager] Install failed: {e}")
        return False
