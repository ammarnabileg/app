# -*- coding: utf-8 -*-
"""إعادة تشغيل سجلّات ADMS (tools/adms_replay.py) — المرحلة ٠ من خارطة الطريق.

كلُّ `*.log` في `tests/fixtures/adms/` (مصنوع، في git) و`tests/fixtures/adms_private/`
(حقيقيّ من عملاء، خارج git) بجواره `.json` بلقطته — أيُّ تغيير في النتيجة يُسقط الاختبار.
"""
import glob
import json
import os

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
LOGS = sorted(glob.glob(os.path.join(HERE, 'fixtures', 'adms', '*.log')) +
              glob.glob(os.path.join(HERE, 'fixtures', 'adms_private', '*.log')))


@pytest.mark.parametrize('log', LOGS, ids=[os.path.basename(p) for p in LOGS])
def test_replay_matches_golden(log):
    from tools.adms_replay import replay
    golden = log[:-4] + '.json'
    with open(log, encoding='utf-8', errors='replace') as f:
        snap = json.loads(json.dumps(replay(f.read())))
    assert os.path.exists(golden), f'مفيش لقطة — شغّل: python tools/adms_replay.py {log} --save {golden}'
    with open(golden, encoding='utf-8') as f:
        want = json.load(f)
    assert snap['http_errors'] == 0
    for k in want:
        assert snap.get(k) == want[k], f'اتغيّر: {k}'


def test_parse_splits_entries():
    from tools.adms_replay import parse
    with open(os.path.join(HERE, 'fixtures', 'adms', 'sample_k40_speedface.log'), encoding='utf-8') as f:
        e = parse(f.read())
    assert [(k, t, s) for k, t, s, _b in e][:3] == [('cdata', 'OPERLOG', 'CQZ7200000001'),
                                                    ('cdata', 'ATTLOG', 'CQZ7200000001'),
                                                    ('cdata', 'BIODATA', 'CQZ7200000001')]
    assert e[3][0] == 'querydata' and e[3][1] == 'biodata' and len(e) == 7
