
import socket
import ipaddress
import concurrent.futures
import threading
import time
import subprocess
import re
import urllib.request
from flask import current_app

# --- Database & Constants ---
ZKTECO_OUIS = {
    "8C:E7:48", "00:17:61", "54:10:EC", "00:0B:3C", 
    "00:1C:14", "00:14:10", "D0:2D:B3"
}

COMMON_SUBNETS = [
    "192.168.1.0/24",
    "192.168.0.0/24",
    "192.168.8.0/24",
    "10.0.0.0/24"
]

class DeviceCandidate:
    def __init__(self, ip, mac=None, vendor="Unknown", source="Unknown", confidence="Low"):
        self.ip = ip
        self.mac = mac if mac else "Unknown"
        self.vendor = vendor
        self.source = source
        self.confidence = confidence

    def update_mac(self, mac):
        if mac and self.mac == "Unknown":
            self.mac = mac.upper()
            # Check Vendor
            for oui in ZKTECO_OUIS:
                if self.mac.startswith(oui):
                    self.vendor = "ZKTeco"
                    self.confidence = "High"
                    break

    def to_dict(self):
        return {
            'ip': self.ip,
            'mac': self.mac,
            'vendor': self.vendor,
            'source': self.source,
            'confidence': self.confidence
        }

class NetworkScanner:
    def __init__(self):
        self.found_devices = {} # ip -> DeviceCandidate
        self.lock = threading.Lock()

    def get_local_ip(self):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(('10.255.255.255', 1))
            IP = s.getsockname()[0]
            s.close()
        except:
            IP = '127.0.0.1'
        return IP

    def get_arp_table(self):
        """Parses `arp -a` to get IP -> MAC mapping."""
        arp_map = {}
        try:
            output = subprocess.check_output("arp -a", shell=True).decode('latin-1')
            pattern = re.compile(r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\s+([0-9a-fA-F-]{17})')
            for line in output.splitlines():
                match = pattern.search(line)
                if match:
                    ip, mac = match.groups()
                    mac = mac.replace('-', ':').upper()
                    arp_map[ip] = mac
        except Exception as e:
            print(f"ARP Error: {e}")
        return arp_map

    def check_port(self, ip, port, retries=1):
        for _ in range(retries):
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(0.5) 
                result = s.connect_ex((ip, port))
                s.close()
                if result == 0:
                    return True
                time.sleep(0.1)
            except:
                pass
        return False

    def udp_discovery(self, target_ip='255.255.255.255'):
        """Sends ZKTeco discovery packet."""
        port = 4370
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            s.settimeout(1.5)
            
            message = b'\x00\x00\x00\x00' # ZK magic packet
            s.sendto(message, (target_ip, port))
            
            start = time.time()
            while time.time() - start < 1.5:
                try:
                    data, addr = s.recvfrom(1024)
                    self.add_device(DeviceCandidate(addr[0], source="UDP Discovery", confidence="High", vendor="ZKTeco"))
                except socket.timeout:
                    break
                except Exception:
                    break
            s.close()
        except:
            pass

    def add_device(self, dev):
        with self.lock:
            if dev.ip in self.found_devices:
                existing = self.found_devices[dev.ip]
                if dev.confidence == "High": existing.confidence = "High"
                if dev.vendor != "Unknown": existing.vendor = dev.vendor
                if dev.mac != "Unknown": existing.mac = dev.mac
            else:
                self.found_devices[dev.ip] = dev

    def http_fingerprint(self, ip):
        """Checks for ZKTeco signatures on port 80."""
        try:
            url = f"http://{ip}/"
            req = urllib.request.Request(url, method='HEAD')
            with urllib.request.urlopen(req, timeout=1.0) as response:
                server = response.headers.get('Server', '')
                if 'ZKTeco' in server or 'iClock' in server:
                    return True
        except:
            pass
        return False

    def scan_yield(self, subnets=None):
        self.found_devices.clear()
        arp_table = self.get_arp_table()

        # 1. ARP Phase
        yield {'type': 'status', 'message': 'تحليل جدول ARP...', 'percent': 5}
        for ip, mac in arp_table.items():
            for oui in ZKTECO_OUIS:
                if mac.startswith(oui):
                    dev = DeviceCandidate(ip, mac, "ZKTeco", "ARP-Check", "High")
                    self.add_device(dev)
                    yield {'type': 'device', 'device': dev.to_dict()}

        # 2. UDP Discovery Phase
        yield {'type': 'status', 'message': 'إرسال حزمة استكشاف UDP...', 'percent': 10}
        
        # We need to capture UDP results differently to yield them
        # Re-implementing udp_discovery inline to yield results
        try:
            port = 4370
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            s.settimeout(1.5)
            s.sendto(b'\x00\x00\x00\x00', ('255.255.255.255', port))
            
            start = time.time()
            while time.time() - start < 1.5:
                try:
                    data, addr = s.recvfrom(1024)
                    ip = addr[0]
                    if ip not in self.found_devices:
                        dev = DeviceCandidate(ip, source="UDP Discovery", confidence="High", vendor="ZKTeco")
                        self.add_device(dev)
                        yield {'type': 'device', 'device': dev.to_dict()}
                except socket.timeout:
                    break
                except:
                    break
            s.close()
        except:
            pass

        # 3. TCP Port Scan Phase
        yield {'type': 'status', 'message': 'تحضير قائمة الفحص...', 'percent': 15}
        
        scan_list = []
        subnet_count = 0
        
        if subnets:
            subnet_count = len(subnets)
            for net in subnets:
                try:
                    network = ipaddress.ip_network(net, strict=False)
                    scan_list.extend(list(network.hosts()))
                except:
                    pass
        else:
            # Default to local
            subnet_count = 1
            try:
                local_ip = self.get_local_ip()
                subnet = f"{local_ip.rpartition('.')[0]}.0/24"
                net = ipaddress.ip_network(subnet, strict=False)
                scan_list.extend(list(net.hosts()))
            except:
                pass

        total_hosts = len(scan_list)
        if total_hosts == 0:
            yield {'type': 'complete', 'count': len(self.found_devices)}
            return

        yield {'type': 'info', 'ranges_count': subnet_count, 'total_hosts': total_hosts}
        
        max_workers = 100 if total_hosts > 500 else 50
        completed = 0
        
        # Start TCP Scan
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {}
                for ip in scan_list:
                    str_ip = str(ip)
                    futures[executor.submit(self.check_port, str_ip, 4370)] = str_ip

                # Process results as they complete
                for f in concurrent.futures.as_completed(futures):
                    completed += 1
                    
                    # Calculate percentage (15% to 95%)
                    current_percent = 15 + int((completed / total_hosts) * 80)
                    
                    # Updates every 2% or every 5 hosts to avoid spamming events
                    if completed % 5 == 0 or completed == total_hosts:
                        yield {
                            'type': 'progress', 
                            'percent': current_percent, 
                            'scanned': completed, 
                            'total': total_hosts
                        }

                    ip = futures[f]
                    try:
                        if f.result(): # Port Open
                            dev = DeviceCandidate(ip, source="TCP-4370", confidence="Medium")
                            if ip in arp_table:
                                dev.update_mac(arp_table[ip])
                                for oui in ZKTECO_OUIS:
                                     if dev.mac and dev.mac.startswith(oui):
                                         dev.vendor = "ZKTeco"
                                         dev.confidence = "High"
                            
                            # HTTP Fingerprint (quick check)
                            if self.http_fingerprint(ip):
                                dev.vendor = "ZKTeco (HTTP)"
                                dev.confidence = "High"

                            # ZK Protocol Probe (Deep Check for routed networks)
                            if dev.confidence != "High" or dev.vendor == "Unknown":
                                try:
                                    from zk import ZK
                                    conn = None
                                    try:
                                        # Try connecting
                                        conn = ZK(ip, port=4370, timeout=2, force_udp=False)
                                        if conn.connect():
                                            dev.vendor = "ZKTeco (Verified)"
                                            dev.confidence = "High"
                                            # Try to get SN or MAC if missing
                                            try:
                                                sn = conn.get_serialnumber()
                                                # If we don't have MAC, use SN or placeholder
                                                if not dev.mac: 
                                                    dev.mac = f"SN:{sn}"
                                            except: pass
                                            conn.disconnect()
                                    except:
                                        pass
                                    finally:
                                        if conn: 
                                            try: 
                                                conn.disconnect() 
                                            except: 
                                                pass
                                except: pass

                            if ip not in self.found_devices or self.found_devices[ip].confidence != dev.confidence:
                                self.add_device(dev)
                                yield {'type': 'device', 'device': dev.to_dict()}
                    except: pass
        except Exception as e:
            print(f"Scan error: {e}")
            
        yield {'type': 'progress', 'percent': 100, 'message': 'اكتمل الفحص'}
        yield {'type': 'complete', 'count': len(self.found_devices)}
