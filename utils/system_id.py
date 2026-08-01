
import subprocess
import hashlib
import platform
import re
import os

def get_windows_uuid():
    """Get Windows Machine GUID from Registry."""
    try:
        cmd = 'reg query "HKEY_LOCAL_MACHINE\\SOFTWARE\\Microsoft\\Cryptography" /v MachineGuid'
        output = subprocess.check_output(cmd, shell=True).decode()
        match = re.search(r'MachineGuid\s+REG_SZ\s+([a-fA-F0-9-]+)', output)
        if match:
            return match.group(1)
    except Exception:
        pass
    return None

def get_mac_address():
    """Get MAC Address."""
    try:
        from uuid import getnode
        mac = getnode()
        return ':'.join(("%012X" % mac)[i:i+2] for i in range(0, 12, 2))
    except Exception:
        return None

def get_processor_id():
    """Get Processor ID using PowerShell (wmic deprecated)."""
    try:
        cmd = 'powershell -NoProfile -Command "Get-CimInstance Win32_Processor | Select-Object -ExpandProperty ProcessorId"'
        output = subprocess.check_output(cmd, shell=True, text=True, stderr=subprocess.DEVNULL)
        if output.strip():
            return output.strip()
    except Exception:
        pass
    return None

def _hwid_file():
    """مسار ملف المعرّف داخل مجلد البيانات الدائم — بجوار قاعدة البيانات."""
    # متغيّر البيئة أولًا: utils.db يقرأ DATA_DIR مرة واحدة عند الاستيراد،
    # فيبقى قديمًا إن تغيّر المسار بعد ذلك — وهو ما يجعل تركيبًا جديدًا
    # يرث معرّف تركيب سابق.
    base = os.environ.get('HR_DATA_DIR')
    if not base:
        try:
            from utils.db import DATA_DIR
            base = DATA_DIR
        except Exception:
            base = (r'C:\ProgramData\HRSystem' if os.name == 'nt'
                    else '/app/data')
    return os.path.join(base, '.instance_id')


def _read_stored_hwid():
    try:
        p = _hwid_file()
        if os.path.isfile(p):
            with open(p, 'r', encoding='utf-8') as f:
                v = f.read().strip()
            if len(v) == 64 and all(c in '0123456789abcdef' for c in v):
                return v
    except Exception:
        pass
    return None


def _write_stored_hwid(value):
    try:
        p = _hwid_file()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, 'w', encoding='utf-8') as f:
            f.write(value)
        try:
            os.chmod(p, 0o600)
        except Exception:
            pass
        return True
    except Exception:
        return False


def get_hardware_fingerprint():
    """بصمة العتاد الخام — تُستخدم للتشخيص لا للترخيص.

    داخل حاوية Docker لا تتوفّر أوامر ويندوز، فتصير هذه البصمة معتمدة على
    عنوان MAC وحده — وهو عنوان تولّده Docker عشوائيًّا عند كل إعادة بناء.
    """
    win_id = get_windows_uuid() or "UNKNOWN_WIN"
    mac_id = get_mac_address() or "UNKNOWN_MAC"
    cpu_id = get_processor_id() or "UNKNOWN_CPU"
    raw_id = f"{win_id}|{mac_id}|{cpu_id}"
    return hashlib.sha256(raw_id.encode()).hexdigest()


def get_system_hwid():
    """معرّف التركيب — ثابت ما دام مجلد البيانات قائمًا.

    ## لماذا لا يُشتقّ من العتاد مباشرةً

    كان المعرّف sha256(MachineGuid + MAC + ProcessorId). وهذا ينهار في
    النشر الفعلي: النظام يعمل داخل Docker، والحاوية تأخذ عنوان MAC
    عشوائيًّا عند كل إعادة بناء — أي معرّفًا جديدًا بعد كل
    `docker compose up --build`، فتُرفض رخصة عميل يدفع ويعمل.

    وأوامر ويندوز لا تعمل داخل الحاوية أصلًا، فيبقى MAC وحده مصدرَ
    التمييز — وهو أكثر المكوّنات الثلاثة تقلّبًا.

    ## البديل

    يُولَّد معرّف عشوائي مرة واحدة ويُحفظ في مجلد البيانات الدائم بجوار
    قاعدة البيانات. فيثبت عبر إعادة البناء وتحديث النسخة وإعادة التشغيل،
    ويُنقل مع البيانات عند نقل التركيب إلى خادم آخر — وهو نقل لا نسخ.

    ## الحدّ الصريح

    نسخ مجلد البيانات إلى خادم ثانٍ ينقل المعرّف معه، فيعمل التركيبان
    بالمعرّف نفسه. المنع هنا ليس عتاديًّا بل بالكشف: الخادم يرى نداءات
    من عنوانين مختلفين بالمعرّف ذاته. ومقابل ذلك لا يتوقّف عميل شرعيّ
    بسبب إعادة بناء حاوية — وهذه المقايضة مقصودة، لأن توقّف الرواتب خطأً
    أسوأ من نسخة غير مرخّصة.
    """
    stored = _read_stored_hwid()
    if stored:
        return stored

    # أول تشغيل: يُشتقّ من العتاد إن أمكن، ويُخلط بعشوائيّة كي لا يتكرّر
    # عبر حاويات متطابقة على المضيف نفسه.
    seed = get_hardware_fingerprint() + '|' + os.urandom(16).hex()
    hwid = hashlib.sha256(seed.encode()).hexdigest()
    _write_stored_hwid(hwid)
    return hwid


def reset_system_hwid():
    """فكّ الارتباط: يحذف المعرّف المحفوظ فيُولَّد جديد عند الطلب التالي.

    يلزم عند نقل العميل إلى خادم جديد — وإلا بقيت الرخصة مربوطةً بمعرّف
    لم يعد قائمًا، فيتوقّف عميل شرعيّ.
    """
    try:
        p = _hwid_file()
        if os.path.isfile(p):
            os.remove(p)
            return True
    except Exception:
        pass
    return False

if __name__ == "__main__":
    print(f"HWID: {get_system_hwid()}")
