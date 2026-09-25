"""جهازُ البصمة خلف وسيط Coolify.

## ما وقع

في Apache يُكتب بيدٍ: `/iclock` بلا إجبار HTTPS ويذهب إلى خادم البصمة.
وفي Coolify يُعلن `hr_adms` بدومين `http://<الدومين>/iclock`، فوقع عطلان
صامتان — الجهازُ يسكت، ولا يصل النظامَ طلبٌ ليُسجِّل شيئًا:

1. **المنفذ:** الصورةُ كانت تعلن `EXPOSE 5000 8081` للخدمتين، وTraefik حين لا
   يُعطى منفذًا يختار **الأصغر** — فيُرسل البصمة إلى 5000 في حاوية لا يسمع
   فيها أحدٌ على 5000.
2. **البادئة:** Coolify يقصّ `/iclock` من الطلب قبل تمريره، والخادمُ لا
   يجيب إلا تحت `/iclock`.

يُشغَّل:  python -m pytest tests/test_adms_behind_proxy.py -v
"""
import os
import re
import sqlite3
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


@pytest.fixture
def adms(tmp_path, monkeypatch):
    import importlib
    monkeypatch.setenv('HR_DATA_DIR', str(tmp_path))
    import utils.db as db
    importlib.reload(db)
    db.init_db()
    con = sqlite3.connect(os.path.join(str(tmp_path), 'hr_system.db'))
    con.execute("INSERT INTO fingerprint_devices (device_name, device_ip, is_active, is_adms) "
                "VALUES ('SNPROXY1', '10.0.0.5', 1, 1)")
    req = [r for r in con.execute('PRAGMA table_info(employees)') if r[3] == 1 and r[4] is None and r[1] != 'id']
    v = {r[1]: (1 if r[2].upper() in ('INTEGER', 'REAL', 'NUMERIC') else 'x') for r in req}
    v.update({'employee_number': '101', 'name': 'emp', 'is_active': 1})
    con.execute(f"INSERT INTO employees ({', '.join(v)}) VALUES ({', '.join('?' * len(v))})", list(v.values()))
    con.commit()
    con.close()
    import routes.adms_routes as R
    importlib.reload(R)
    import adms_server
    importlib.reload(adms_server)
    return adms_server.app.test_client(), os.path.join(str(tmp_path), 'hr_system.db')


@pytest.mark.parametrize('prefix', ['/iclock', ''])
def test_the_device_session_works_with_and_without_the_prefix(adms, prefix):
    """`/iclock/cdata` (Apache، أو Coolify بلا قصّ) و`/cdata` (Coolify يقصّ)."""
    c, path = adms
    assert c.get(f'{prefix}/cdata?SN=SNPROXY1&options=all').status_code == 200
    r = c.post(f'{prefix}/cdata?SN=SNPROXY1&table=ATTLOG&Stamp=1',
               data='101\t2026-09-25 08:01:00\t0\t1\t0\t0\n', content_type='text/plain')
    assert r.status_code == 200 and r.get_data(as_text=True).startswith('OK')
    assert c.get(f'{prefix}/getrequest?SN=SNPROXY1').status_code == 200
    assert c.get(f'{prefix}/cdata?SN=healthcheck').status_code == 200
    con = sqlite3.connect(path)
    try:
        n = con.execute('SELECT COUNT(*) FROM attendance_records').fetchone()[0]
    finally:
        con.close()
    assert n == 1, 'البصمةُ سُجِّلت'


def test_the_image_does_not_expose_both_ports():
    """Traefik يختار أصغرَ منفذٍ معلَن: صورةٌ تعلن 5000 و8081 تُرسل البصمةَ إلى 5000."""
    text = open(os.path.join(ROOT, 'Dockerfile'), encoding='utf-8').read()
    exposed = re.findall(r'^\s*EXPOSE\s+(.+)$', text, re.M)
    ports = {p for line in exposed for p in line.split()}
    assert not ({'5000', '8081'} <= ports), exposed


def test_each_coolify_service_exposes_its_own_port():
    text = open(os.path.join(ROOT, 'docker-compose.coolify.yml'), encoding='utf-8').read()

    def block(name):
        m = re.search(rf'^  {name}:\n(.*?)(?=^  \S|\Z)', text, re.M | re.S)
        return m.group(1) if m else ''
    assert re.search(r'expose:\s*\n\s*-\s*"5000"', block('hr_web'))
    assert re.search(r'expose:\s*\n\s*-\s*"8081"', block('hr_adms'))
    assert '"5000"' not in block('hr_adms')
