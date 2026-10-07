# -*- coding: utf-8 -*-
"""الجداولُ الذكيّة (static/js/smart_table.js): فرز، فلترة، تحريك، تثبيت، تكبير.

السلوكُ في المتصفّح جُرّب بـPlaywright على صفحات البرنامج نفسِها. وهنا:
- أنّها محمّلةٌ في كلّ صفحة (عبر قوالب الأصول المشتركة)؛
- ومنطقُ المقارنة والفلترة (أرقامٌ عربيّة، عملة، تواريخ، ‎>100‎) — بـnode إن وُجد.
"""
import json
import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = os.path.join(ROOT, 'static', 'js', 'smart_table.js')
CSS = os.path.join(ROOT, 'static', 'css', 'smart_table.css')


def test_loaded_on_every_page_through_the_shared_assets():
    js = open(os.path.join(ROOT, 'templates', '_partials', 'assets_js.html'), encoding='utf-8').read()
    css = open(os.path.join(ROOT, 'templates', '_partials', 'assets.html'), encoding='utf-8').read()
    assert "js/smart_table.js" in js and "css/smart_table.css" in css
    assert os.path.exists(JS) and os.path.exists(CSS)


def test_complex_tables_are_left_alone():
    src = open(JS, encoding='utf-8').read()
    # عناوينُ بصفّين أو مدموجة، وخلايا مدموجةٌ في الجسم، ونوافذُ منبثقة، وdata-no-grid.
    for needle in ("thead.rows.length !== 1", "colSpan > 1 || hr.cells[i].rowSpan > 1",
                   "tbody td[rowspan]", ".modal", "data-no-grid"):
        assert needle in src, needle


def test_pinning_survives_the_theme():
    css = open(CSS, encoding='utf-8').read()
    # الثيمُ يقصّ الجدول (overflow: hidden) ويجعل العنوان relative — فلا يثبت شيء.
    assert 'table.st-table.st-has-pins { overflow: visible !important; }' in css
    assert 'table.st-table th.st-pinned, table.st-table td.st-pinned { position: sticky;' in css


NODE_HARNESS = r"""
global.window = { addEventListener() {}, SMART_TABLES_OFF: true };
global.document = {
  documentElement: { getAttribute: (a) => (a === 'dir' ? 'rtl' : 'ar') },
  readyState: 'complete', addEventListener() {}, querySelector() { return null; },
  querySelectorAll() { return []; }, body: {}
};
global.location = { pathname: '/' };
global.MutationObserver = class { observe() {} disconnect() {} };
require(process.argv[2]);
const S = window.SmartTables;
const sorted = (arr) => arr.slice().sort(S._compare);
console.log(JSON.stringify({
  nums: sorted(['1,250.000 د.ك', '٩٠٠', '15', '', '2.5']),
  dates: sorted(['05/01/2026', '2025-12-31', '01/02/2026']),
  text: sorted(['ب', 'أ', 'ج']),
  gt: ['50', '150', '١٠١', 'x'].map(v => S._match(v, '>100')),
  le: ['50', '100', '101'].map(v => S._match(v, '<=100')),
  eq: ['7', '70'].map(v => S._match(v, '=7')),
  ne: ['done', 'pending'].map(v => S._match(v, '!=done')),
  contains: ['Ahmad Khaled', 'Sara'].map(v => S._match(v, 'khal'))
}));
"""


@pytest.mark.skipif(not shutil.which('node'), reason='node غير مثبّت')
def test_sorting_and_filtering_logic(tmp_path):
    h = tmp_path / 'h.js'
    h.write_text(NODE_HARNESS, encoding='utf-8')
    # window.SmartTables يُعرَّف قبل init — وSMART_TABLES_OFF يمنع المسح.
    out = subprocess.run(['node', str(h), JS], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    r = json.loads(out.stdout.strip().splitlines()[-1])
    assert r['nums'] == ['2.5', '15', '٩٠٠', '1,250.000 د.ك', ''], 'أرقامٌ لا نصوص، والفارغ آخرًا'
    assert r['dates'] == ['2025-12-31', '05/01/2026', '01/02/2026'], 'يوم/شهر/سنة'
    assert r['text'] == ['أ', 'ب', 'ج']
    assert r['gt'] == [False, True, True, False]
    assert r['le'] == [True, True, False]
    assert r['eq'] == [True, False]
    assert r['ne'] == [False, True]
    assert r['contains'] == [True, False]
