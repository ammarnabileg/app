# -*- coding: utf-8 -*-
"""الوكيل المحلّيّ — الجانب الذي يعمل داخل شبكة الشركة (انظر utils/connector).

دورةٌ كلَّ دقيقتين:
1. يسأل السحابة: ما الأجهزة؟ وآخر بصمة وصلتكم من كلٍّ منها متى؟
2. يتّصل بكلّ جهاز **كما يتّصل البرنامج المكتبيّ اليوم** (pyzk، نفس الإعداد)، يسحب
   البصمات من بعد آخر بصمة (بتداخل ١٠ دقائق — التكرار لا يُحفظ مرّتين) ويرفعها.
3. كلَّ ساعة: قائمة مستخدمي الجهاز (الجدد يُضافون موظّفين كما في الأجهزة الأخرى).
4. يسحب الأوامر وينفّذها: الإيقاف بنفس بتّ «معطّل» (utils/device_access.write_user)،
   والحذف — وبصمات الموظّف تُرفع للسحابة قبلهما لتُسترجع لو رجع.

لا قاعدة بيانات هنا: السحابة هي المرجع. ملفُّ الإعداد وحده (السيرفر والمفتاح).
"""
import base64
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta

logger = logging.getLogger('onz.connector')

DEFAULT_INTERVAL = 120
USERS_EVERY_SECONDS = 3600
OVERLAP_MINUTES = 10
CHUNK = 1000


def config_path():
    base = os.environ.get('ONZ_CONNECTOR_DIR')
    if not base:
        root = os.environ.get('PROGRAMDATA') or os.environ.get('APPDATA') or os.path.expanduser('~')
        base = os.path.join(root, 'onz_connector')
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, 'config.json')


def load_config():
    try:
        with open(config_path(), encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_config(cfg):
    with open(config_path(), 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def _version():
    try:
        from utils.version_info import CURRENT_VERSION
        return CURRENT_VERSION
    except Exception:
        return 'dev'


def _zk_connect(ip, port):
    from zk import ZK
    # كما يتّصل fingerprint_sync / device_access تمامًا.
    return ZK(ip, port=int(port or 4370), timeout=10).connect()


class Agent:
    def __init__(self, server, key, http=None, zk_connect=None, now=None):
        self.server = server.rstrip('/')
        self.key = key
        self.http = http
        self.zk_connect = zk_connect or _zk_connect
        self.now = now or datetime.now
        self.users_sent_at = {}

    # ---- HTTP
    def _headers(self):
        return {'X-Connector-Key': self.key, 'X-Connector-Version': _version(), 'Content-Type': 'application/json'}

    def _call(self, method, path, body=None):
        http = self.http
        if http is None:
            import requests
            http = requests
        kw = {'headers': self._headers(), 'timeout': 60}
        if body is not None:
            kw['data'] = json.dumps(body, ensure_ascii=False).encode('utf-8')
        r = http.request(method, self.server + path, **kw)
        try:
            data = r.json()
        except Exception:
            data = {}
        if r.status_code == 401:
            raise PermissionError('مفتاح الوكيل غلط أو موقوف — اعمل مفتاح جديد من شاشة «الوكيل المحلي»')
        return r.status_code, data

    # ---- الدورة
    def run_once(self):
        out = {'devices': 0, 'punches': 0, 'saved': 0, 'commands': 0, 'errors': []}
        code, cfg = self._call('GET', '/api/connector/config')
        if code != 200:
            raise RuntimeError(f'السيرفر رد {code}')
        for d in cfg.get('devices', []):
            try:
                r = self.sync_device(d)
                out['devices'] += 1
                out['punches'] += r['sent']
                out['saved'] += r['saved']
            except Exception as e:              # noqa: BLE001 — جهازٌ مقفول لا يوقف البقيّة
                out['errors'].append(f"{d['name']} ({d['ip']}): {str(e)[:150]}")
        try:
            out['commands'] = self.run_commands({d['id']: d for d in cfg.get('devices', [])})
        except PermissionError:
            raise
        except Exception as e:                  # noqa: BLE001
            out['errors'].append(f'commands: {str(e)[:150]}')
        if out['errors']:
            try:
                self._call('POST', '/api/connector/ack', {'results': [], 'error': ' | '.join(out['errors'])[:300]})
            except Exception:
                pass
        return out

    def sync_device(self, d):
        last = None
        if d.get('last_sync_time'):
            try:
                last = datetime.strptime(str(d['last_sync_time'])[:19], '%Y-%m-%d %H:%M:%S')
            except ValueError:
                last = None
        since = last - timedelta(minutes=OVERLAP_MINUTES) if last else None
        dev = self.zk_connect(d['ip'], d['port'])
        users = None
        try:
            dev.disable_device()
            try:
                logs = dev.get_attendance() or []
                due = time.time() - self.users_sent_at.get(d['id'], 0) >= USERS_EVERY_SECONDS
                if due:
                    users = dev.get_users() or []
            finally:
                try:
                    dev.enable_device()
                except Exception:
                    pass
        finally:
            try:
                dev.disconnect()
            except Exception:
                pass
        punches = [{'pin': str(l.user_id).strip(), 'time': l.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
                    'status': int(getattr(l, 'status', 0) or 0), 'verify': str(getattr(l, 'punch', '') or '')}
                   for l in logs if since is None or l.timestamp >= since]
        saved = 0
        for i in range(0, len(punches), CHUNK):
            code, res = self._call('POST', '/api/connector/punches', {'device_id': d['id'], 'punches': punches[i:i + CHUNK]})
            if code != 200:
                raise RuntimeError(f'رفع البصمات: السيرفر رد {code} {res.get("reason", "")}')
            saved += res.get('saved', 0)
        if users is not None:
            from utils.device_access import DISABLED_BIT
            self._call('POST', '/api/connector/users', {'device_id': d['id'], 'users': [
                {'pin': str(u.user_id).strip(), 'name': u.name or '', 'card': str(u.card or ''),
                 'disabled': bool(int(u.privilege or 0) & DISABLED_BIT)} for u in users]})
            self.users_sent_at[d['id']] = time.time()
        return {'sent': len(punches), 'saved': saved}

    # ---- الأوامر
    def run_commands(self, devices):
        code, data = self._call('GET', '/api/connector/commands')
        cmds = data.get('commands') or []
        if not cmds:
            return 0
        by_dev = {}
        for c in cmds:
            by_dev.setdefault(c['device_id'], []).append(c)
        results = []
        for dev_id, items in by_dev.items():
            d = devices.get(dev_id)
            if not d:
                results += [{'id': c['id'], 'ok': False, 'message': 'الجهاز مش عند الوكيل ده'} for c in items]
                continue
            try:
                dev = self.zk_connect(d['ip'], d['port'])
            except Exception as e:              # noqa: BLE001
                results += [{'id': c['id'], 'ok': False, 'message': f'الجهاز مش بيرد: {str(e)[:100]}'} for c in items]
                continue
            try:
                dev.disable_device()
                for c in items:
                    try:
                        results.append({'id': c['id'], **self.apply(dev, d, c)})
                    except Exception as e:      # noqa: BLE001
                        results.append({'id': c['id'], 'ok': False, 'message': str(e)[:200]})
            finally:
                try:
                    dev.enable_device()
                except Exception:
                    pass
                try:
                    dev.disconnect()
                except Exception:
                    pass
        self._call('POST', '/api/connector/ack', {'results': results})
        return sum(1 for r in results if r.get('ok'))

    @staticmethod
    def _templates(dev, user):
        out = []
        try:
            fp_ver = str(dev.get_fp_version())
        except Exception:
            fp_ver = None
        try:
            sn = dev.get_serialnumber()
        except Exception:
            sn = None
        try:
            for f in dev.get_templates() or []:
                if getattr(f, 'uid', None) == user.uid and getattr(f, 'template', None):
                    out.append({'fid': int(f.fid), 'valid': int(f.valid or 1), 'fp_ver': fp_ver, 'sn': sn,
                                'template': base64.b64encode(bytes(f.template)).decode('ascii')})
        except Exception:
            pass
        return out

    def apply(self, dev, d, c):
        from utils.device_access import DISABLED_BIT, write_user
        kind, pin = c['kind'], str(c.get('pin') or '').strip()
        if kind == 'set_time':
            dev.set_time(datetime.strptime(c['payload']['time'], '%Y-%m-%d %H:%M:%S'))
            return {'ok': True, 'message': 'اتضبط الوقت'}
        users = dev.get_users() or []
        user = next((u for u in users if str(u.user_id).strip() == pin), None)
        if kind in ('disable_user', 'enable_user', 'delete_user') and user is None:
            # مش موجود على الجهاز = المطلوب حاصل (لا يبصم).
            return {'ok': kind != 'enable_user', 'message': 'مش موجود على الجهاز'}
        if kind == 'disable_user':
            temps = self._templates(dev, user)
            write_user(dev, user, int(user.privilege or 0) | DISABLED_BIT)
            return {'ok': True, 'message': 'اتوقف (ببصماته)', 'templates': temps}
        if kind == 'enable_user':
            write_user(dev, user, int(user.privilege or 0) & ~DISABLED_BIT)
            return {'ok': True, 'message': 'اتفعّل'}
        if kind == 'delete_user':
            temps = self._templates(dev, user)
            dev.delete_user(uid=user.uid)
            return {'ok': True, 'message': 'اتمسح', 'templates': temps}
        if kind == 'upsert_user':
            p = c.get('payload') or {}
            try:
                card = int(p.get('card') or 0)
            except (TypeError, ValueError):
                card = 0
            if user is None:
                used = {u.uid for u in users}
                uid = int(pin) if pin.isdigit() and int(pin) < 65535 and int(pin) not in used else (max(used) + 1 if used else 1)
                dev.set_user(uid=uid, name=str(p.get('name') or '')[:24], privilege=0, password='', group_id='',
                             user_id=pin, card=card)
                user = next((u for u in (dev.get_users() or []) if str(u.user_id).strip() == pin), None)
            else:
                dev.set_user(uid=user.uid, name=str(p.get('name') or user.name or '')[:24],
                             privilege=int(user.privilege or 0) & ~DISABLED_BIT & 0xFF, password=user.password or '',
                             group_id=user.group_id or '', user_id=pin, card=card or int(user.card or 0))
            if user is not None and not p.get('enabled', True):
                write_user(dev, user, int(user.privilege or 0) | DISABLED_BIT)
            return {'ok': True, 'message': 'اترفع — البصمة/الوش بيتسجلوا على الجهاز'}
        return {'ok': False, 'message': f'أمر غير معروف: {kind}'}


def run_forever(agent, interval=DEFAULT_INTERVAL):
    while True:
        try:
            r = agent.run_once()
            logger.info(f"devices={r['devices']} punches={r['punches']} saved={r['saved']} commands={r['commands']}"
                        + (f" errors={r['errors']}" if r['errors'] else ''))
        except PermissionError as e:
            logger.error(str(e))
            time.sleep(max(interval, 600))
            continue
        except Exception as e:                  # noqa: BLE001 — الشبكة تقع وترجع
            logger.warning(f'cycle failed: {e}')
        time.sleep(interval)


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description='onz — الوكيل المحلي لأجهزة البصمة')
    ap.add_argument('--setup', action='store_true', help='احفظ السيرفر والمفتاح')
    ap.add_argument('--server')
    ap.add_argument('--key')
    ap.add_argument('--interval', type=int)
    ap.add_argument('--once', action='store_true', help='دورة واحدة واطبع النتيجة')
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s',
                        handlers=[logging.StreamHandler(sys.stdout),
                                  logging.FileHandler(os.path.join(os.path.dirname(config_path()), 'connector.log'),
                                                      encoding='utf-8')])
    cfg = load_config()
    if a.setup:
        if not a.server or not a.key:
            ap.error('--setup محتاج --server و --key')
        cfg.update(server=a.server.rstrip('/'), key=a.key.strip())
        if a.interval:
            cfg['interval'] = a.interval
        save_config(cfg)
        print(f'اتحفظ: {config_path()}')
    if not cfg.get('server') or not cfg.get('key'):
        print('لسه مفيش إعداد — شغّل: connector_agent --setup --server https://… --key onzc_…')
        return 2
    agent = Agent(cfg['server'], cfg['key'])
    if a.once or a.setup:
        try:
            print(json.dumps(agent.run_once(), ensure_ascii=False, indent=2))
        except Exception as e:                  # noqa: BLE001
            print(f'تعذّر: {e}')
            return 1
        return 0
    run_forever(agent, int(cfg.get('interval') or DEFAULT_INTERVAL))
    return 0
