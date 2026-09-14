import os
import re
import sys
import requests
import hashlib
import json
import time
import hmac
import subprocess

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.version_info import CURRENT_VERSION

# Configuration
API_URL = os.environ.get('PUBLISH_API_URL', 'https://onz.one/PHP/api/publish_release.php')

# يُقرأ من البيئة ولا يُكتب هنا. كان مكتوبًا في الملف نصًّا
# ("YOUR_SECRET_KEY_..."), فكان كل توقيع يُرفَض من اللوحة بـInvalid
# Signature — واللوحة نفسها ترفض الانطلاق بمفتاح فارغ، وهو الصواب.
# يجب أن يساوي API_SECRET في config/secrets.local.php على اللوحة.
API_SECRET = os.environ.get('PUBLISH_API_SECRET', '')

CHANGELOG_FILE = 'CHANGELOG.md'
EXE_PATH = os.environ.get('PUBLISH_EXE_PATH', 'dist/HRSystem/HRSystem.exe')

def calculate_checksum(file_path):
    """Calculate SHA256 checksum of a file."""
    if not os.path.exists(file_path):
        return None
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        # Read and update hash string value in blocks of 4K
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()

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

    return {
        'version': CURRENT_VERSION,
        'notes': notes
    }

def publish_release():
    print("🚀 HR System Release Publisher v1.0")
    print("-" * 40)

    # المفتاح أولًا: بدونه كل توقيع يُرفض، والوقوف هنا أوضح من رسالة
    # "Invalid Signature" بعد رفع الملف كاملًا.
    if not API_SECRET:
        print("❌ PUBLISH_API_SECRET غير مضبوط في البيئة.")
        print("   يجب أن يساوي API_SECRET في config/secrets.local.php على اللوحة.")
        return

    # 1. Get Changelog Data
    release_data = parse_latest_changelog()
    if not release_data:
        print(f"❌ Error: Could not parse latest version from {CHANGELOG_FILE}")
        return

    print(f"📦 Version: {release_data['version']}")
    print(f"📝 Notes: {len(release_data['notes'])} items")

    # 2. Calculate Checksum
    print("🔍 Calculating EXE Checksum...")
    checksum = calculate_checksum(EXE_PATH)
    if not checksum:
        print(f"⚠️ Warning: EXE not found at {EXE_PATH}. Checksum will be null (Development Mode?)")
        # return # Uncomment to enforce EXE existence
    else:
        print(f"✅ Checksum: {checksum[:12]}...")

    # 3. Prepare Payload
    payload = {
        'version': release_data['version'],
        'notes': release_data['notes'], # List of strings
        'is_mandatory': True, # Can be interactive
        'checksum': checksum or ''
    }
    
    body_json = json.dumps(payload)
    
    # 4. Security Headers
    timestamp = str(int(time.time()))
    nonce = os.urandom(8).hex()
    
    # HMAC Sign: SHA256(body + timestamp + nonce)
    message = body_json + timestamp + nonce
    signature = hmac.new(API_SECRET.encode(), message.encode(), hashlib.sha256).hexdigest()
    
    headers = {
        'Content-Type': 'application/json',
        'X-API-Key': 'legacy_key_if_needed', # Or omitted if using only HMAC
        'X-Timestamp': timestamp,
        'X-Nonce': nonce,
        'X-HMAC-Signature': signature
    }
    
    # 5. Send Request
    try:
        print(f"📡 Uploading to {API_URL}...")
        response = requests.post(API_URL, data=body_json, headers=headers, timeout=10)
        
        if response.status_code == 200:
            res_json = response.json()
            if res_json.get('success'):
                print("\n✅ SUCCESS: Release published successfully!")
                print(f"Server Message: {res_json.get('message')}")
            else:
                print(f"\n❌ FAILED: Server returned error: {res_json.get('message')}")
        else:
            print(f"\n❌ HTTP ERROR {response.status_code}: {response.text}")
            
    except Exception as e:
        print(f"\n❌ CONNECTION ERROR: {e}")

if __name__ == "__main__":
    publish_release()
