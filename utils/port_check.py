# -*- coding: utf-8 -*-
"""هل الخادمُ الذي يردّ على المنفذ هو هذا البرنامج؟

## العطلُ الذي يحرسه هذا الملفّ

كان المُشغِّل يكتب «تم تشغيل الخادم بنجاح» **لحظةَ بدء الخيط**، قبل أن
يُعرف هل حجز المنفذ. فإن كان المنفذُ مشغولًا ببرنامجٍ آخر (نسخةٌ قديمة
من البرنامج نفسه بجانب الساعة، أو مشروعٌ آخر على الجهاز) فشل الحجزُ
بصمت — `--windowed` بلا شاشة سوداء تُظهر الخطأ — وفتح المتصفّحُ على
البرنامج الآخر: «Not Found» والسجلُّ يقول «بنجاح».

فالنجاحُ الآن لا يُعلن إلا بعد أن **يردّ هذا البرنامج** على المنفذ:
كلُّ ردٍّ منه يحمل الترويسة `X-HR-System` (انظر `app.py`)، فردٌّ بلا
ترويسةٍ ردُّ برنامجٍ آخر.
"""
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

HEADER = 'X-HR-System'


def port_free(port, host='0.0.0.0'):
    """هل يمكن حجزُ المنفذ الآن؟ بلا SO_REUSEADDR: نريد أن نفشل كما سيفشل الخادم."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind((host, int(port)))
        return True
    except OSError:
        return False
    finally:
        s.close()


def parse_netstat_pid(output, port):
    """رقمُ العملية التي تستمع على المنفذ من مخرجات `netstat -ano` (ويندوز)."""
    pat = re.compile(r'^\s*TCP\s+\S+:(\d+)\s+\S+\s+LISTENING\s+(\d+)\s*$', re.I)
    for line in (output or '').splitlines():
        m = pat.match(line)
        if m and int(m.group(1)) == int(port):
            return int(m.group(2))
    return None


def parse_tasklist_name(output):
    """اسمُ البرنامج من مخرجات `tasklist /FO CSV /NH`."""
    for line in (output or '').splitlines():
        m = re.match(r'^"([^"]+)","(\d+)"', line.strip())
        if m:
            return m.group(1)
    return None


def port_owner(port):
    """(رقم العملية، اسمها) لمن يحجز المنفذ — على ويندوز. وإلّا None."""
    if not sys.platform.startswith('win'):
        return None
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    try:
        out = subprocess.run(['netstat', '-ano', '-p', 'TCP'], capture_output=True,
                             text=True, timeout=10, creationflags=flags).stdout
        pid = parse_netstat_pid(out, port)
        if not pid:
            return None
        out = subprocess.run(['tasklist', '/FI', f'PID eq {pid}', '/FO', 'CSV', '/NH'],
                             capture_output=True, text=True, timeout=10,
                             creationflags=flags).stdout
        return pid, parse_tasklist_name(out)
    except Exception:
        return None


def probe(port, timeout=3):
    """{'answering': يردّ أحدٌ؟, 'ours': هل هو هذا البرنامج؟, 'version': نسختُه}."""
    url = f'http://127.0.0.1:{int(port)}/login'
    try:
        resp = urllib.request.urlopen(url, timeout=timeout)
        headers = resp.headers
    except urllib.error.HTTPError as e:      # 404 وأخواتُها: أحدٌ يردّ
        headers = e.headers
    except Exception:
        return {'answering': False, 'ours': False, 'version': None}
    version = headers.get(HEADER) if headers else None
    return {'answering': True, 'ours': bool(version), 'version': version}


def wait_until_ours(port, timeout=45, alive=None, interval=0.5):
    """ينتظر حتى يردّ هذا البرنامج على المنفذ. يُرجع نتيجةَ آخر `probe`.

    `alive()` اختياريّ: إن رجع False (مات خيطُ الخادم) يُكفّ عن الانتظار."""
    deadline = time.monotonic() + timeout
    last = {'answering': False, 'ours': False, 'version': None}
    while time.monotonic() < deadline:
        last = probe(port)
        if last['ours']:
            return last
        if alive is not None and not alive():
            return probe(port)
        time.sleep(interval)
    return last


def busy_message(port, owner, lang='ar'):
    """رسالةٌ تقول مَن يحجز المنفذ وماذا يُفعل."""
    name = owner[1] if owner and owner[1] else None
    pid = owner[0] if owner else None
    same_app = bool(name) and name.lower().startswith('hrsystem')
    if lang == 'ar':
        if same_app:
            return (f'المنفذ {port} مشغول بنسخةٍ أخرى من البرنامج (HRSystem، رقم العملية {pid}). '
                    'أغلقها من أيقونتها بجانب الساعة (أو من مدير المهام) ثم أعد تشغيل البرنامج.')
        who = f' ببرنامج «{name}» (رقم العملية {pid})' if name else ' ببرنامجٍ آخر'
        return (f'المنفذ {port} مشغول{who} — لم يبدأ الخادم. '
                'أغلق ذلك البرنامج أو غيّر المنفذ من «إعدادات المنفذ» ثم أعد التشغيل.')
    if same_app:
        return (f'Port {port} is used by another copy of HR System (PID {pid}). '
                'Close it from its tray icon (or Task Manager), then restart.')
    who = f' by "{name}" (PID {pid})' if name else ' by another program'
    return (f'Port {port} is in use{who} — the server did not start. '
            'Close that program or change the port in Port Settings, then restart.')
