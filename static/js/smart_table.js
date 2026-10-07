/*
 * الجداولُ الذكيّة — لكلّ جدولٍ في البرنامج، بلا تعديل صفحاته:
 *
 *   - الفرز (Sorting): ضغطةٌ على العنوان تصاعدي ← تنازلي ← بلا فرز.
 *     وShift + ضغطة يضيف عمودًا ثانيًا للفرز.
 *   - الفلترة (Multi-column Filtering): بحثٌ عامّ فوق الجدول، وفلترٌ لكلّ عمود
 *     من قائمة العمود (يقبل: نصًّا، أو ‎>100‎ ‎<50‎ ‎=7‎ للأرقام).
 *   - تحريك الأعمدة (Column Reordering): اسحب العنوان وأفلته مكان عمودٍ آخر.
 *   - تثبيت الأعمدة (Column Pinning): من قائمة العمود — يمين أو يسار — فيبقى
 *     ظاهرًا أثناء التمرير الأفقي.
 *   - تكبير وتصغير الأعمدة (Column Resizing): اسحب حافّة العنوان.
 *
 * ويُحفظ ترتيبُ الأعمدة وعرضُها وتثبيتُها والفرزُ لكلّ جدولٍ في كلّ صفحة على هذا
 * الجهاز (localStorage) — و«إعادة ضبط» يرجعه كما كان.
 *
 * لا يُلمس جدولٌ:
 *   - عليه data-no-grid، أو داخل نافذة منبثقة (modal)؛
 *   - عناوينُه في أكثر من صفّ أو فيها colspan/rowspan (تقارير مجمّعة)؛
 *   - في جسمه خلايا مدموجة (rowspan) — الفرزُ والتحريك يمزّقان مجموعاتها.
 *
 * والصفحاتُ التي تبني صفوفَها بالجافاسكربت (الموظفين…): الصفوفُ الجديدة تأخذ
 * ترتيبَ الأعمدة والفرزَ والفلتر نفسَه تلقائيًّا (MutationObserver).
 */
(function () {
    'use strict';

    var RTL = (document.documentElement.getAttribute('dir') || 'rtl') !== 'ltr';
    var AR = (document.documentElement.getAttribute('lang') || 'ar').indexOf('ar') === 0;
    var T = AR ? {
        search: 'بحث في الجدول…', reset: 'إعادة ضبط الجدول', shown: 'المعروض: %s من %t',
        asc: 'ترتيب تصاعدي', desc: 'ترتيب تنازلي', nosort: 'إلغاء الترتيب',
        filter: 'فلتر العمود', filterPh: 'نص، أو ‎>100‎ ‎<50‎ ‎=7‎',
        pinStart: RTL ? 'تثبيت يمين' : 'تثبيت يسار', pinEnd: RTL ? 'تثبيت يسار' : 'تثبيت يمين',
        unpin: 'إلغاء التثبيت', drag: 'اسحب العنوان لتحريك العمود، واسحب حافته لتكبيره أو تصغيره',
        menu: 'خيارات العمود', clear: 'مسح الفلاتر'
    } : {
        search: 'Search table…', reset: 'Reset table', shown: 'Showing %s of %t',
        asc: 'Sort ascending', desc: 'Sort descending', nosort: 'Clear sort',
        filter: 'Column filter', filterPh: 'text, or >100 <50 =7',
        pinStart: RTL ? 'Pin right' : 'Pin left', pinEnd: RTL ? 'Pin left' : 'Pin right',
        unpin: 'Unpin', drag: 'Drag the header to move the column, drag its edge to resize',
        menu: 'Column options', clear: 'Clear filters'
    };

    // ------------------------------------------------------------ أدوات

    var AR_DIGITS = /[٠-٩۰-۹]/g;
    function normDigits(s) {
        return s.replace(AR_DIGITS, function (d) {
            var c = d.charCodeAt(0);
            return String(c >= 0x06F0 ? c - 0x06F0 : c - 0x0660);
        });
    }
    function cellText(td) {
        if (!td) return '';
        if (td.hasAttribute('data-sort')) return td.getAttribute('data-sort');
        var inp = td.querySelector('input:not([type=checkbox]):not([type=radio]):not([type=hidden]), select, textarea');
        if (inp && !td.textContent.trim()) {
            return inp.tagName === 'SELECT' ? (inp.options[inp.selectedIndex] || {}).text || '' : inp.value || '';
        }
        return td.textContent.replace(/\s+/g, ' ').trim();
    }
    // رقمٌ إن كان النصّ رقمًا (بفواصل، أو ومعه عملةٌ أو % قصيرة)، وإلّا null.
    function asNumber(s) {
        s = normDigits(String(s)).replace(/٫/g, '.').replace(/٬/g, ',').trim();
        var m = s.match(/^([^\d\-+]{0,6})([-+]?[\d,]*\.?\d+)\s*([^\d]{0,8})$/);
        if (!m) return null;
        var n = parseFloat(m[2].replace(/,/g, ''));
        return isNaN(n) ? null : n;
    }
    // تاريخٌ dd/mm/yyyy → yyyy-mm-dd ليُقارن نصًّا.
    function asDateKey(s) {
        s = normDigits(String(s));
        var m = s.match(/^(\d{1,2})[\/\-.](\d{1,2})[\/\-.](\d{4})(.*)$/);
        if (m) return m[3] + '-' + ('0' + m[2]).slice(-2) + '-' + ('0' + m[1]).slice(-2) + m[4];
        if (/^\d{4}-\d{2}-\d{2}/.test(s)) return s;
        return null;
    }
    function compareValues(a, b) {
        if (a === b) return 0;
        if (a === '') return 1;        // الفارغ آخرًا في الاتّجاهين
        if (b === '') return -1;
        var na = asNumber(a), nb = asNumber(b);
        if (na !== null && nb !== null) return na - nb;
        var da = asDateKey(a), db = asDateKey(b);
        if (da !== null && db !== null) return da < db ? -1 : da > db ? 1 : 0;
        if (na !== null) return -1;
        if (nb !== null) return 1;
        return a.localeCompare(b, AR ? 'ar' : undefined, { numeric: true, sensitivity: 'base' });
    }
    function matchFilter(text, f) {
        if (!f) return true;
        var t = normDigits(text).toLowerCase();
        var q = normDigits(f).trim().toLowerCase();
        var op = q.match(/^(>=|<=|>|<|=|!=)\s*(.+)$/);
        if (op) {
            var n = asNumber(text), v = asNumber(op[2]);
            if (v !== null) {
                if (n === null) return false;
                switch (op[1]) {
                    case '>': return n > v; case '<': return n < v;
                    case '>=': return n >= v; case '<=': return n <= v;
                    case '=': return n === v; case '!=': return n !== v;
                }
            }
            if (op[1] === '=') return t === op[2].trim();
            if (op[1] === '!=') return t.indexOf(op[2].trim()) === -1;
        }
        return t.indexOf(q) !== -1;
    }
    function store(key, val) {
        try {
            if (val === null) localStorage.removeItem(key);
            else localStorage.setItem(key, JSON.stringify(val));
        } catch (e) { /* وضعُ التصفّح الخاصّ — تعمل بلا حفظ */ }
    }
    function load(key) {
        try { return JSON.parse(localStorage.getItem(key) || 'null'); } catch (e) { return null; }
    }
    function hash(s) {
        var h = 0;
        for (var i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
        return (h >>> 0).toString(36);
    }

    // ------------------------------------------------------------ أيُّ جدولٍ يُحسَّن

    function eligible(table) {
        if (table.hasAttribute('data-no-grid') || table.dataset.stReady) return false;
        if (table.closest('.modal, [data-no-grid], .no-grid, .dataTables_wrapper')) return false;
        if (table.classList.contains('dataTable')) return false;
        var thead = table.tHead;
        if (!thead || thead.rows.length !== 1) return false;
        var hr = thead.rows[0];
        if (hr.cells.length < 2) return false;
        for (var i = 0; i < hr.cells.length; i++) {
            if (hr.cells[i].colSpan > 1 || hr.cells[i].rowSpan > 1) return false;
        }
        if (table.querySelector('tbody td[rowspan]:not([rowspan="1"]), tbody th[rowspan]:not([rowspan="1"])')) return false;
        if (table.querySelector('table')) return false;           // جدولٌ داخل جدول
        return true;
    }

    // ------------------------------------------------------------ الجدول

    var instances = [];

    function SmartTable(table, index) {
        this.table = table;
        this.headRow = table.tHead.rows[0];
        this.n = this.headRow.cells.length;
        var sig = Array.prototype.map.call(this.headRow.cells, function (c) { return c.textContent.trim(); }).join('|');
        this.key = 'st:' + location.pathname + ':' + (table.id || index) + ':' + hash(sig);
        var saved = load(this.key) || {};
        this.order = validOrder(saved.order, this.n) || range(this.n);
        this.widths = saved.widths || {};
        this.pins = saved.pins || {};
        this.sort = Array.isArray(saved.sort) ? saved.sort : [];
        this.filters = {};
        this.query = '';
        this.rowSeq = 0;
        table.dataset.stReady = '1';
        this.setup();
    }

    function range(n) { var a = []; for (var i = 0; i < n; i++) a.push(i); return a; }
    function validOrder(o, n) {
        if (!Array.isArray(o) || o.length !== n) return null;
        var seen = {};
        for (var i = 0; i < n; i++) {
            if (typeof o[i] !== 'number' || o[i] < 0 || o[i] >= n || seen[o[i]]) return null;
            seen[o[i]] = 1;
        }
        return o;
    }

    SmartTable.prototype.save = function () {
        store(this.key, { order: this.order, widths: this.widths, pins: this.pins, sort: this.sort });
    };

    SmartTable.prototype.setup = function () {
        var self = this, table = this.table;
        table.classList.add('st-table');

        // حاويةُ تمرير: التثبيتُ (sticky) يحتاج أقربَ أبٍ يمرَّر أفقيًّا.
        var p = table.parentElement;
        var scrolls = p && (p.classList.contains('table-responsive') ||
            /(auto|scroll)/.test(getComputedStyle(p).overflowX));
        if (!scrolls) {
            var wrap = document.createElement('div');
            wrap.className = 'st-scroll';
            p.insertBefore(wrap, table);
            wrap.appendChild(table);
        }
        this.scroller = table.parentElement;

        // كلُّ خليّةٍ تحمل رقمَ عمودها الأصليّ — فيُعاد ترتيبُها أيًّا كان مكانُها الآن.
        this.tagRow(this.headRow, true);
        Array.prototype.forEach.call(this.headRow.cells, function (th) { self.decorateHeader(th); });
        this.bodies().forEach(function (tr) { self.tagRow(tr); });
        if (table.tFoot) Array.prototype.forEach.call(table.tFoot.rows, function (tr) { self.tagRow(tr); });

        this.buildToolbar();
        this.applyAll();

        // صفوفٌ تُبنى لاحقًا (بحثٌ، تحديث، صفحةٌ تملأ الجدول بالجافاسكربت).
        this.observer = new MutationObserver(function (muts) {
            if (self.busy) return;
            var changed = muts.some(function (m) {
                return m.type === 'childList' && (m.target.tagName === 'TBODY' || m.target.tagName === 'THEAD' ||
                                                  m.target === table);
            });
            if (changed) {
                clearTimeout(self.t);
                self.t = setTimeout(function () { self.refresh(); }, 30);
            }
        });
        this.observer.observe(table, { childList: true, subtree: true });
        window.addEventListener('resize', function () { self.applyPins(); });
    };

    SmartTable.prototype.destroy = function () {
        this.dead = true;
        if (this.observer) this.observer.disconnect();
        if (this.toolbar) this.toolbar.remove();
        delete this.table.dataset.stReady;
        var i = instances.indexOf(this);
        if (i !== -1) instances.splice(i, 1);
    };

    SmartTable.prototype.bodies = function () {
        var out = [];
        Array.prototype.forEach.call(this.table.tBodies, function (tb) {
            Array.prototype.forEach.call(tb.rows, function (tr) { out.push(tr); });
        });
        return out;
    };

    // صفٌّ بعددِ أعمدة العناوين نفسِه يُرتَّب؛ وصفٌّ بخلايا مدموجة («لا بيانات») يُترك.
    SmartTable.prototype.tagRow = function (tr, isHead) {
        if (!isHead && tr.dataset.stRow === undefined) tr.dataset.stRow = String(this.rowSeq++);
        if (tr.cells.length !== this.n) { tr.dataset.stSkip = '1'; return false; }
        for (var i = 0; i < tr.cells.length; i++) {
            if (tr.cells[i].colSpan > 1) { tr.dataset.stSkip = '1'; return false; }
        }
        delete tr.dataset.stSkip;
        for (var j = 0; j < tr.cells.length; j++) {
            if (tr.cells[j].dataset.stCol === undefined) tr.cells[j].dataset.stCol = String(j);
        }
        return true;
    };

    SmartTable.prototype.refresh = function () {
        var self = this;
        // الصفحةُ أعادت بناءَ العناوين (أعمدةٌ أخرى): يُبدأ الجدولُ من جديد.
        var head = this.table.tHead && this.table.tHead.rows[0];
        if (!head || head !== this.headRow || head.cells.length !== this.n ||
                Array.prototype.some.call(head.cells, function (c) { return c.dataset.stCol === undefined; })) {
            this.destroy();
            setTimeout(function () { scan(document); }, 0);
            return;
        }
        // صارت فيه خلايا مدموجة (تقريرٌ مجمّع بُني بعد التحميل): يرجع جدولًا عاديًّا كما رسمته الصفحة.
        if (this.table.querySelector('tbody td[rowspan]:not([rowspan="1"]), tbody th[rowspan]:not([rowspan="1"])')) {
            this.disable();
            return;
        }
        this.busy = true;
        this.bodies().forEach(function (tr) { self.tagRow(tr); });
        this.busy = false;
        this.applyAll();
    };

    SmartTable.prototype.disable = function () {
        this.busy = true;
        this.order = range(this.n); this.pins = {}; this.sort = []; this.filters = {}; this.query = '';
        this.applyOrder();
        var t = this.table;
        Array.prototype.forEach.call(t.querySelectorAll('.st-hide'), function (r) { r.classList.remove('st-hide'); });
        Array.prototype.forEach.call(t.querySelectorAll('.st-pinned'), function (c) {
            c.classList.remove('st-pinned', 'st-pin-edge', 'st-pin-edge-end');
            c.style.insetInlineStart = c.style.insetInlineEnd = c.style.backgroundColor = '';
        });
        Array.prototype.forEach.call(t.querySelectorAll('.st-menu-btn, .st-resize'), function (x) { x.remove(); });
        Array.prototype.forEach.call(this.headRow.cells, function (th) {
            th.draggable = false;
            th.classList.remove('st-th', 'st-sort-asc', 'st-sort-desc', 'st-filtered', 'st-is-pinned');
        });
        t.classList.remove('st-table', 'st-has-pins');
        t.setAttribute('data-no-grid', 'auto');
        this.destroy();
    };

    SmartTable.prototype.applyAll = function () {
        this.busy = true;
        try {
            this.applyOrder();
            this.applySort();
            this.applyFilter();
            this.applyWidths();
            this.applyPins();
            this.updateHeaderState();
        } finally {
            var self = this;
            // ما سبّبناه نحن من تغييرات لا يُعاد عليه.
            setTimeout(function () { self.busy = false; }, 0);
        }
    };

    // ------------------------------------------------------------ تحريك الأعمدة

    // المثبَّتُ في البداية أوّلًا، والمثبَّتُ في النهاية آخرًا، والباقي بترتيبه.
    SmartTable.prototype.effectiveOrder = function () {
        var pins = this.pins;
        var s = this.order.filter(function (c) { return pins[c] === 'start'; });
        var m = this.order.filter(function (c) { return !pins[c]; });
        var e = this.order.filter(function (c) { return pins[c] === 'end'; });
        return s.concat(m, e);
    };

    SmartTable.prototype.applyOrder = function () {
        var order = this.effectiveOrder();
        var rows = [this.headRow].concat(this.bodies());
        if (this.table.tFoot) rows = rows.concat(Array.prototype.slice.call(this.table.tFoot.rows));
        rows.forEach(function (tr) {
            if (tr.dataset.stSkip) return;
            var byCol = {};
            Array.prototype.forEach.call(tr.cells, function (c) { byCol[c.dataset.stCol] = c; });
            var cur = Array.prototype.map.call(tr.cells, function (c) { return +c.dataset.stCol; });
            if (cur.join() === order.join()) return;
            order.forEach(function (col) { if (byCol[col]) tr.appendChild(byCol[col]); });
        });
    };

    SmartTable.prototype.moveColumn = function (from, to, after) {
        if (from === to) return;
        var o = this.order.filter(function (c) { return c !== from; });
        var idx = o.indexOf(to);
        o.splice(after ? idx + 1 : idx, 0, from);
        this.order = o;
        // يُفلَت بين مثبَّتين: يأخذ تثبيتَهم.
        if ((this.pins[to] || null) !== (this.pins[from] || null)) {
            if (this.pins[to]) this.pins[from] = this.pins[to]; else delete this.pins[from];
        }
        this.save();
        this.applyAll();
    };

    // ------------------------------------------------------------ الفرز

    SmartTable.prototype.toggleSort = function (col, additive) {
        var cur = this.sort.filter(function (s) { return s[0] === col; })[0];
        var next = !cur ? 'asc' : cur[1] === 'asc' ? 'desc' : null;
        if (!additive) this.sort = [];
        else this.sort = this.sort.filter(function (s) { return s[0] !== col; });
        if (next) this.sort.push([col, next]);
        this.save();
        this.applyAll();
    };

    SmartTable.prototype.setSort = function (col, dir) {
        this.sort = dir ? [[col, dir]] : [];
        this.save();
        this.applyAll();
    };

    // صفوفُ الجسم مجموعاتٍ: صفٌّ عاديّ ومعه ما بعده من صفوفٍ مدموجة (تفاصيلُه المطويّة
    // تحته، كما في كشف الرواتب) — تتحرّك وتُخفى معه. وما قبل أوّل صفٍّ عاديّ («لا
    // بيانات»، «جارٍ التحميل») يبقى في مكانه.
    function groupsOf(tb) {
        var lead = [], groups = [], cur = null;
        Array.prototype.forEach.call(tb.rows, function (tr) {
            if (!tr.dataset.stSkip) { cur = [tr]; groups.push(cur); }
            else if (cur) cur.push(tr);
            else lead.push(tr);
        });
        return { lead: lead, groups: groups };
    }

    SmartTable.prototype.applySort = function () {
        var sort = this.sort;
        Array.prototype.forEach.call(this.table.tBodies, function (tb) {
            var g = groupsOf(tb);
            if (g.groups.length < 2) return;
            var get = function (tr, col) {
                return cellText(tr.querySelector('[data-st-col="' + col + '"]'));
            };
            var sorted = g.groups.slice().sort(function (a, b) {
                for (var i = 0; i < sort.length; i++) {
                    var c = compareValues(get(a[0], sort[i][0]), get(b[0], sort[i][0]));
                    if (c) return sort[i][1] === 'desc' ? -c : c;
                }
                return (+a[0].dataset.stRow) - (+b[0].dataset.stRow);     // بلا فرز: كما جاءت
            });
            var same = sorted.every(function (grp, i) { return grp === g.groups[i]; });
            if (same) return;
            sorted.forEach(function (grp) { grp.forEach(function (tr) { tb.appendChild(tr); }); });
        });
    };

    // ------------------------------------------------------------ الفلترة

    SmartTable.prototype.applyFilter = function () {
        var self = this, q = this.query, f = this.filters;
        var cols = Object.keys(f).filter(function (k) { return f[k]; });
        var total = 0, shown = 0;
        Array.prototype.forEach.call(this.table.tBodies, function (tb) {
            groupsOf(tb).groups.forEach(function (grp) {
                var tr = grp[0];
                total++;
                var ok = true;
                // البحثُ العامّ يشمل تفاصيلَ الصفّ المطويّة تحته.
                if (q) ok = matchFilter(grp.map(function (r) { return r.textContent; }).join(' ').replace(/\s+/g, ' '), q);
                for (var i = 0; ok && i < cols.length; i++) {
                    ok = matchFilter(cellText(tr.querySelector('[data-st-col="' + cols[i] + '"]')), f[cols[i]]);
                }
                grp.forEach(function (r) { r.classList.toggle('st-hide', !ok); });
                if (ok) shown++;
            });
        });
        if (this.count) {
            var active = q || cols.length;
            this.count.textContent = active ? T.shown.replace('%s', shown).replace('%t', total) : '';
            this.clearBtn.classList.toggle('d-none', !active);
        }
    };

    // ------------------------------------------------------------ العرض

    SmartTable.prototype.cellsOf = function (col) {
        return this.table.querySelectorAll('[data-st-col="' + col + '"]');
    };

    SmartTable.prototype.applyWidths = function () {
        var self = this;
        Object.keys(this.widths).forEach(function (col) {
            var w = self.widths[col] + 'px';
            Array.prototype.forEach.call(self.cellsOf(col), function (c) {
                c.style.width = w; c.style.minWidth = w; c.style.maxWidth = w;
                c.classList.add('st-sized');
            });
        });
    };

    // ------------------------------------------------------------ التثبيت

    SmartTable.prototype.applyPins = function () {
        var self = this;
        // يُمسح القديم ثمّ يُحسب من جديد (العروضُ تتغيّر مع المحتوى).
        Array.prototype.forEach.call(this.table.querySelectorAll('.st-pinned'), function (c) {
            c.classList.remove('st-pinned', 'st-pin-edge', 'st-pin-edge-end');
            c.style.insetInlineStart = ''; c.style.insetInlineEnd = '';
            c.style.backgroundColor = '';
        });
        var order = this.effectiveOrder();
        var starts = order.filter(function (c) { return self.pins[c] === 'start'; });
        var ends = order.filter(function (c) { return self.pins[c] === 'end'; }).reverse();
        var place = function (cols, prop) {
            var off = 0;
            cols.forEach(function (col, i) {
                var th = self.headRow.querySelector('[data-st-col="' + col + '"]');
                var w = th ? th.getBoundingClientRect().width : 0;
                Array.prototype.forEach.call(self.cellsOf(col), function (c) {
                    // خلفيّةُ الخليّة كما هي (عنوانٌ أخضر، صفٌّ ملوّن) — وإلّا ظهر ما تحتها أثناء التمرير.
                    c.style.backgroundColor = effectiveBg(c);
                    c.classList.add('st-pinned');
                    if (i === cols.length - 1) c.classList.add(prop === 'insetInlineStart' ? 'st-pin-edge' : 'st-pin-edge-end');
                    c.style[prop] = off + 'px';
                });
                off += w;
            });
        };
        place(starts, 'insetInlineStart');
        place(ends, 'insetInlineEnd');
        this.table.classList.toggle('st-has-pins', starts.length + ends.length > 0);
    };

    function transparent(bg) {
        return !bg || bg === 'transparent' || /rgba\(\s*0,\s*0,\s*0,\s*0\s*\)/.test(bg);
    }
    function effectiveBg(el) {
        for (var n = el; n && n.nodeType === 1; n = n.parentElement) {
            var bg = getComputedStyle(n).backgroundColor;
            if (!transparent(bg)) return bg;
            if (n.tagName === 'TABLE') break;
        }
        var b = getComputedStyle(document.body).backgroundColor;
        return transparent(b) ? '#fff' : b;
    }

    SmartTable.prototype.setPin = function (col, where) {
        if (where) this.pins[col] = where; else delete this.pins[col];
        this.save();
        this.applyAll();
    };

    // ------------------------------------------------------------ العناوين

    SmartTable.prototype.decorateHeader = function (th) {
        var self = this;
        var col = +th.dataset.stCol;
        th.classList.add('st-th');
        var interactive = !th.querySelector('input, select, button') && th.textContent.trim() !== '';
        th.dataset.stSortable = interactive ? '1' : '0';

        if (interactive) {
            var btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'st-menu-btn';
            btn.setAttribute('aria-label', T.menu);
            btn.title = T.menu;
            btn.innerHTML = '<i class="fas fa-ellipsis-vertical"></i>';
            btn.addEventListener('click', function (e) {
                e.stopPropagation();
                openMenu(self, col, btn);
            });
            th.appendChild(btn);

            th.addEventListener('click', function (e) {
                if (self.dead) return;
                if (e.target.closest('.st-menu-btn, .st-resize, a, input, select, button')) return;
                self.toggleSort(col, e.shiftKey);
            });
        }

        // حافّةُ التكبير/التصغير.
        var h = document.createElement('span');
        h.className = 'st-resize';
        h.title = T.drag;
        h.addEventListener('mousedown', function (e) { startResize(self, th, e); });
        h.addEventListener('click', function (e) { e.stopPropagation(); });
        h.addEventListener('dblclick', function (e) {
            e.stopPropagation();
            delete self.widths[col];
            Array.prototype.forEach.call(self.cellsOf(col), function (c) {
                c.style.width = c.style.minWidth = c.style.maxWidth = '';
                c.classList.remove('st-sized');
            });
            self.save();
            self.applyPins();
        });
        th.appendChild(h);

        // السحبُ للتحريك.
        th.draggable = true;
        th.addEventListener('dragstart', function (e) {
            if (self.resizing || self.dead) { e.preventDefault(); return; }
            self.dragCol = col;
            th.classList.add('st-dragging');
            try { e.dataTransfer.setData('text/plain', String(col)); } catch (x) { /* IE */ }
            e.dataTransfer.effectAllowed = 'move';
        });
        th.addEventListener('dragend', function () {
            th.classList.remove('st-dragging');
            clearDrop(self);
            self.dragCol = null;
        });
        th.addEventListener('dragover', function (e) {
            if (self.dragCol === null || self.dragCol === undefined) return;
            e.preventDefault();
            clearDrop(self);
            th.classList.add(dropAfter(th, e) ? 'st-drop-after' : 'st-drop-before');
        });
        th.addEventListener('dragleave', function () { th.classList.remove('st-drop-before', 'st-drop-after'); });
        th.addEventListener('drop', function (e) {
            e.preventDefault();
            var from = self.dragCol;
            clearDrop(self);
            if (from === null || from === undefined) return;
            self.moveColumn(from, col, dropAfter(th, e));
        });
    };

    // «بعد» في اتّجاه القراءة: في العربيّ يسارُ العنوان.
    function dropAfter(th, e) {
        var r = th.getBoundingClientRect();
        var mid = r.left + r.width / 2;
        return RTL ? e.clientX < mid : e.clientX > mid;
    }
    function clearDrop(self) {
        Array.prototype.forEach.call(self.headRow.cells, function (c) {
            c.classList.remove('st-drop-before', 'st-drop-after');
        });
    }

    SmartTable.prototype.updateHeaderState = function () {
        var self = this;
        Array.prototype.forEach.call(this.headRow.cells, function (th) {
            var col = +th.dataset.stCol;
            var s = self.sort.filter(function (x) { return x[0] === col; })[0];
            th.classList.toggle('st-sort-asc', !!s && s[1] === 'asc');
            th.classList.toggle('st-sort-desc', !!s && s[1] === 'desc');
            th.setAttribute('aria-sort', s ? (s[1] === 'asc' ? 'ascending' : 'descending') : 'none');
            th.classList.toggle('st-filtered', !!self.filters[col]);
            th.classList.toggle('st-is-pinned', !!self.pins[col]);
        });
    };

    function startResize(self, th, e) {
        e.preventDefault();
        e.stopPropagation();
        self.resizing = true;
        th.draggable = false;
        var col = +th.dataset.stCol;
        var startX = e.clientX, startW = th.getBoundingClientRect().width;
        document.body.classList.add('st-resizing');
        function move(ev) {
            var dx = ev.clientX - startX;
            var w = Math.max(40, Math.round(startW + (RTL ? -dx : dx)));
            self.widths[col] = w;
            self.applyWidths();
        }
        function up() {
            document.removeEventListener('mousemove', move);
            document.removeEventListener('mouseup', up);
            document.body.classList.remove('st-resizing');
            th.draggable = true;
            setTimeout(function () { self.resizing = false; }, 0);
            self.save();
            self.applyPins();
        }
        document.addEventListener('mousemove', move);
        document.addEventListener('mouseup', up);
    }

    // ------------------------------------------------------------ شريط الأدوات

    SmartTable.prototype.buildToolbar = function () {
        var self = this;
        var bar = document.createElement('div');
        bar.className = 'st-toolbar';
        bar.innerHTML =
            '<div class="st-search"><i class="fas fa-search"></i>' +
            '<input type="search" class="form-control form-control-sm" placeholder="' + T.search + '"></div>' +
            '<span class="st-count small text-muted"></span>' +
            '<button type="button" class="btn btn-sm btn-link st-clear d-none">' + T.clear + '</button>' +
            '<button type="button" class="btn btn-sm btn-outline-secondary st-reset" title="' + T.reset + '">' +
            '<i class="fas fa-rotate-left"></i></button>';
        this.scroller.parentElement.insertBefore(bar, this.scroller);
        var input = bar.querySelector('input');
        var t = null;
        input.addEventListener('input', function () {
            clearTimeout(t);
            t = setTimeout(function () { self.query = input.value.trim(); self.applyFilter(); }, 120);
        });
        this.count = bar.querySelector('.st-count');
        this.clearBtn = bar.querySelector('.st-clear');
        this.clearBtn.addEventListener('click', function () {
            self.query = ''; input.value = ''; self.filters = {};
            self.applyAll();
        });
        bar.querySelector('.st-reset').addEventListener('click', function () {
            store(self.key, null);
            self.order = range(self.n); self.widths = {}; self.pins = {}; self.sort = [];
            self.filters = {}; self.query = ''; input.value = '';
            Array.prototype.forEach.call(self.table.querySelectorAll('[data-st-col]'), function (c) {
                c.style.width = c.style.minWidth = c.style.maxWidth = '';
                c.classList.remove('st-sized');
            });
            self.applyAll();
        });
        this.toolbar = bar;
    };

    // ------------------------------------------------------------ قائمة العمود

    var menuEl = null;
    function closeMenu() {
        if (menuEl) { menuEl.remove(); menuEl = null; }
    }
    function openMenu(st, col, anchor) {
        closeMenu();
        var m = document.createElement('div');
        m.className = 'st-menu shadow';
        m.setAttribute('dir', RTL ? 'rtl' : 'ltr');
        var pin = st.pins[col] || null;
        var s = st.sort.filter(function (x) { return x[0] === col; })[0];
        m.innerHTML =
            '<button type="button" data-a="asc"' + (s && s[1] === 'asc' ? ' class="on"' : '') + '><i class="fas fa-arrow-up-short-wide"></i>' + T.asc + '</button>' +
            '<button type="button" data-a="desc"' + (s && s[1] === 'desc' ? ' class="on"' : '') + '><i class="fas fa-arrow-down-wide-short"></i>' + T.desc + '</button>' +
            (s ? '<button type="button" data-a="nosort"><i class="fas fa-xmark"></i>' + T.nosort + '</button>' : '') +
            '<hr><label class="st-menu-label">' + T.filter + '</label>' +
            '<input type="search" class="form-control form-control-sm" placeholder="' + T.filterPh + '">' +
            '<hr>' +
            '<button type="button" data-a="pin-start"' + (pin === 'start' ? ' class="on"' : '') + '><i class="fas fa-thumbtack"></i>' + T.pinStart + '</button>' +
            '<button type="button" data-a="pin-end"' + (pin === 'end' ? ' class="on"' : '') + '><i class="fas fa-thumbtack"></i>' + T.pinEnd + '</button>' +
            (pin ? '<button type="button" data-a="unpin"><i class="fas fa-xmark"></i>' + T.unpin + '</button>' : '') +
            '<div class="st-menu-hint">' + T.drag + '</div>';
        document.body.appendChild(m);
        var input = m.querySelector('input');
        input.value = st.filters[col] || '';
        input.addEventListener('input', function () {
            st.filters[col] = input.value.trim();
            if (!st.filters[col]) delete st.filters[col];
            st.applyFilter();
            st.updateHeaderState();
        });
        input.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === 'Escape') closeMenu(); });
        m.addEventListener('click', function (e) {
            var b = e.target.closest('button[data-a]');
            if (!b) return;
            var a = b.dataset.a;
            if (a === 'asc' || a === 'desc') st.setSort(col, a);
            else if (a === 'nosort') st.setSort(col, null);
            else if (a === 'pin-start') st.setPin(col, 'start');
            else if (a === 'pin-end') st.setPin(col, 'end');
            else if (a === 'unpin') st.setPin(col, null);
            closeMenu();
        });
        m.addEventListener('mousedown', function (e) { e.stopPropagation(); });
        var r = anchor.getBoundingClientRect();
        var w = 230;
        var left = RTL ? r.left : r.right - w;
        left = Math.max(8, Math.min(left, window.innerWidth - w - 8));
        m.style.width = w + 'px';
        m.style.left = (left + window.scrollX) + 'px';
        m.style.top = (r.bottom + window.scrollY + 4) + 'px';
        menuEl = m;
        // تمريرُ الجدول أفقيًّا يُبعد العمودَ عن القائمة فتُغلق؛ وتمريرُ الصفحة لا (هي معها).
        var sc = st.scroller;
        var opened = Date.now();
        var onScroll = function () {
            if (Date.now() - opened < 250) return;      // تمريرٌ متأخّرٌ سبق فتحَها
            closeMenu(); sc.removeEventListener('scroll', onScroll);
        };
        sc.addEventListener('scroll', onScroll);
        setTimeout(function () { try { input.focus({ preventScroll: true }); } catch (e) { input.focus(); } }, 0);
    }
    document.addEventListener('mousedown', function (e) {
        if (menuEl && !menuEl.contains(e.target)) closeMenu();
    });

    // ------------------------------------------------------------ التشغيل

    function scan(root) {
        var tables = (root || document).querySelectorAll('.main-content table, table[data-grid]');
        Array.prototype.forEach.call(tables, function (t) {
            if (t.hasAttribute('data-grid') || eligible(t)) {
                if (t.dataset.stReady) return;
                try { instances.push(new SmartTable(t, instances.length)); }
                catch (e) { if (window.console) console.warn('smart table', e); }
            }
        });
    }

    function init() {
        if (window.SMART_TABLES_OFF) return;
        scan(document);
        // جداولُ تُضاف للصفحة لاحقًا (تقارير تُبنى بعد البحث).
        var mo = new MutationObserver(function (muts) {
            for (var i = 0; i < muts.length; i++) {
                for (var j = 0; j < muts[i].addedNodes.length; j++) {
                    var n = muts[i].addedNodes[j];
                    if (n.nodeType === 1 && (/^(TABLE|THEAD|TR)$/.test(n.tagName) ||
                                             (n.querySelector && n.querySelector('table')))) {
                        if (n.tagName === 'TR' && !(n.parentElement && n.parentElement.tagName === 'THEAD')) continue;
                        var tb = n.tagName === 'TABLE' ? n : (n.closest && n.closest('table'));
                        if (tb && tb.dataset.stReady && n.tagName !== 'TABLE') continue;
                        clearTimeout(init.t);
                        init.t = setTimeout(function () { scan(document); }, 60);
                        return;
                    }
                }
            }
        });
        var main = document.querySelector('.main-content') || document.body;
        mo.observe(main, { childList: true, subtree: true });
    }

    window.SmartTables = { scan: scan, instances: instances, _compare: compareValues, _match: matchFilter };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
