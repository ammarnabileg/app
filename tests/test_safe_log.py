# -*- coding: utf-8 -*-
"""ملف adms_protocol.log وصل ٢٨٠ ميجا عند عميل: الـhandler كان FileHandler عادي متسمّي
RotatingFileHandler، وكل بصمة وصورة بتتكتب فيه كاملة."""
import logging
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from utils.safe_log import SafeRotatingHandler, shrink  # noqa: E402


def _logger(path, **kw):
    lg = logging.getLogger('t_' + os.path.basename(str(path)) + str(kw))
    lg.handlers = []
    lg.propagate = False
    h = SafeRotatingHandler(str(path), **kw)
    lg.addHandler(h)
    lg.setLevel(logging.INFO)
    return lg


def test_rotates_and_keeps_only_backups(tmp_path):
    p = tmp_path / 'a.log'
    lg = _logger(p, max_bytes=1000, backups=2)
    for i in range(300):
        lg.info('x' * 40 + str(i))
    files = sorted(os.listdir(tmp_path))
    assert files == ['a.log', 'a.log.1', 'a.log.2'], files
    assert all(os.path.getsize(tmp_path / f) < 1100 for f in files)


def test_a_huge_old_file_is_cut_to_its_tail(tmp_path):
    p = tmp_path / 'adms_protocol.log'
    p.write_bytes(b'old\n' * 100000 + b'LAST-LINE\n')
    lg = _logger(p, max_bytes=1000, backups=2)
    lg.info('new')
    assert os.path.getsize(tmp_path / 'adms_protocol.log.1') == 1000
    assert b'LAST-LINE' in (tmp_path / 'adms_protocol.log.1').read_bytes()
    assert (tmp_path / 'adms_protocol.log').read_text(encoding='utf-8').strip() == 'new'


def test_rotation_failure_does_not_lose_the_line(tmp_path, monkeypatch):
    p = tmp_path / 'b.log'
    p.write_text('y' * 2000, encoding='utf-8')
    lg = _logger(p, max_bytes=1000, backups=2)

    def boom(*a):
        raise PermissionError('in use')
    monkeypatch.setattr(os, 'rename', boom)
    lg.info('still written')
    assert 'still written' in p.read_text(encoding='utf-8')


def test_shrink_removes_templates_and_photos_but_keeps_the_rest():
    tmp = 'A' * 1500
    line = f'BIODATA Pin=365\tNo=0\tType=9\tMajorVer=7\tTmp={tmp}\nBIOPHOTO PIN=365\tContent={tmp}\n101\t2026-10-12 08:00:00\t0\t1'
    out = shrink(line)
    assert tmp not in out and 'Tmp=<base64 1500 حرف>' in out and 'Content=<base64 1500 حرف>' in out
    assert 'Pin=365' in out and '101\t2026-10-12 08:00:00' in out
    assert shrink('TMP=short') == 'TMP=short'


def test_no_plain_filehandler_disguised_as_rotating():
    for rel in ('app.py', 'routes/adms_routes.py', 'utils/oracle_db.py'):
        src = open(os.path.join(ROOT, rel), encoding='utf-8').read()
        assert 'FileHandler as RotatingFileHandler' not in src, rel


def test_adms_server_closes_db_connections_after_each_request():
    """سيرفر ADMS المستقل كان بيسيب اتصال مفتوح مع كل طلب: تحت ضغط ٢٠ جهاز الملفات المفتوحة
    عدّت الألف في ثواني ووقع («filedescriptor out of range in select()»)."""
    import adms_server
    assert 'close_db_connection' in [f.__name__ for f in adms_server.app.teardown_appcontext_funcs]
