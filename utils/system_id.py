
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

def get_system_hwid():
    """
    Generates a unique Hardware ID based on multiple components.
    Returns SHA256 hash.
    """
    win_id = get_windows_uuid() or "UNKNOWN_WIN"
    mac_id = get_mac_address() or "UNKNOWN_MAC"
    cpu_id = get_processor_id() or "UNKNOWN_CPU"
    
    # Combine them
    raw_id = f"{win_id}|{mac_id}|{cpu_id}"
    
    # Hash it
    return hashlib.sha256(raw_id.encode()).hexdigest()

if __name__ == "__main__":
    print(f"HWID: {get_system_hwid()}")
