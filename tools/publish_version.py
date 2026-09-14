import os
import requests
import hashlib
import json
import time
import hmac
import subprocess

# Configuration
API_URL = "https://onz.one/PHP/api/publish_release.php"
# SECRET must match the server's secret
API_SECRET = "YOUR_SECRET_KEY_Should_Be_Very_Long_And_Random" 
CHANGELOG_FILE = 'CHANGELOG.md'
EXE_PATH = 'dist/HRSystem/HRSystem.exe'

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
    """Parse ONLY the latest version from CHANGELOG.md"""
    if not os.path.exists(CHANGELOG_FILE):
        return None
        
    with open(CHANGELOG_FILE, 'r', encoding='utf-8') as f:
        lines = f.readlines()
        
    version = None
    notes = []
    found_version = False
    
    for line in lines:
        line = line.strip()
        if not line: continue
        
        if line.startswith('v'):
            if found_version:
                # We reached the NEXT version, stop
                break
            # Found the first (latest) version
            # Format: v6.2 (DD Month YYYY)
            version = line.split(' ')[0] # v6.2
            found_version = True
        elif found_version:
            # This is a note item
            notes.append(line)
            
    if not version:
        return None
        
    return {
        'version': version,
        'notes': notes
    }

def publish_release():
    print("🚀 HR System Release Publisher v1.0")
    print("-" * 40)
    
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
