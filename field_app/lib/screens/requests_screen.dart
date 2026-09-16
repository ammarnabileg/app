/// الطلبات: إجازة أو استئذان/مهمة.
///
/// الخادم يفحص التواريخ والتداخل ووجود نوع الإجازة — وكل ذلك جاء
/// بعد أن قُبلت فيه طلباتٌ لا معنى لها. فلا يُكرَّر فحصه هنا، إنما
/// تُمنع الأخطاء التي يمنعها منتقي التاريخ أصلًا، وتُعرض رسائله كما
/// هي.
library;

import 'package:flutter/material.dart';
import 'package:intl/intl.dart';

import '../api.dart';
import '../portal_api.dart';
import 'widgets.dart';

final _ymd = DateFormat('yyyy-MM-dd');

class RequestsScreen extends StatefulWidget {
  const RequestsScreen({super.key, required this.api, required this.boot});

  final Api api;
  final Map<String, dynamic> boot;

  @override
  State<RequestsScreen> createState() => _RequestsScreenState();
}

class _RequestsScreenState extends State<RequestsScreen> {
  @override
  Widget build(BuildContext context) {
    if (widget.boot['has_employee_record'] != true) {
      return const NoEmployeePane();
    }
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        const SectionTitle('طلب إجازة'),
        _LeaveForm(api: widget.api, boot: widget.boot),
        const SizedBox(height: 24),
        const SectionTitle('استئذان / مهمة'),
        _ExcuseForm(api: widget.api),
        const SizedBox(height: 32),
      ],
    );
  }
}

class _LeaveForm extends StatefulWidget {
  const _LeaveForm({required this.api, required this.boot});

  final Api api;
  final Map<String, dynamic> boot;

  @override
  State<_LeaveForm> createState() => _LeaveFormState();
}

class _LeaveFormState extends State<_LeaveForm> {
  int? _typeId;
  DateTime? _from;
  DateTime? _to;
  final _reason = TextEditingController();
  bool _busy = false;

  List<Map<String, dynamic>> get _types =>
      ((widget.boot['leave_types'] as List?) ?? [])
          .map((e) => (e as Map).cast<String, dynamic>())
          .toList();

  int get _days {
    if (_from == null || _to == null) return 0;
    return _to!.difference(_from!).inDays + 1;
  }

  Future<void> _pick(bool isStart) async {
    final now = DateTime.now();
    final initial = isStart ? (_from ?? now) : (_to ?? _from ?? now);
    final d = await showDatePicker(
      context: context,
      initialDate: initial,
      firstDate: now.subtract(const Duration(days: 365)),
      lastDate: now.add(const Duration(days: 365 * 2)),
    );
    if (d == null) return;
    setState(() {
      if (isStart) {
        _from = d;
        // نهايةٌ قبل بداية: يمنعها المنتقي بدل أن يردّها الخادم بعد
        // أن يملأ الموظف النموذج كله.
        if (_to != null && _to!.isBefore(d)) _to = d;
      } else {
        _to = d.isBefore(_from ?? d) ? (_from ?? d) : d;
      }
    });
  }

  Future<void> _submit() async {
    if (_typeId == null || _from == null || _to == null) {
      _say('اختر نوع الإجازة وتاريخَي البداية والنهاية', false);
      return;
    }
    setState(() => _busy = true);
    try {
      final j = await widget.api.requestLeave(
        leaveTypeId: _typeId!,
        startDate: _ymd.format(_from!),
        endDate: _ymd.format(_to!),
        reason: _reason.text.trim(),
      );
      _say((j['message'] ?? 'أُرسل الطلب').toString(), true);
      setState(() {
        _from = null;
        _to = null;
        _reason.clear();
      });
    } on ApiError catch (e) {
      _say(e.message, false);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  void _say(String m, bool ok) {
    if (!mounted) return;
    ScaffoldMessenger.of(context)
      ..clearSnackBars()
      ..showSnackBar(SnackBar(
        content: Text(m),
        backgroundColor: ok ? Colors.green.shade700 : null,
      ));
  }

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            DropdownButtonFormField<int>(
              initialValue: _typeId,
              decoration: const InputDecoration(
                  labelText: 'نوع الإجازة', border: OutlineInputBorder()),
              items: [
                for (final t in _types)
                  DropdownMenuItem(
                    value: (t['id'] as num).toInt(),
                    child: Text('${t['name']}'),
                  ),
              ],
              onChanged: (v) => setState(() => _typeId = v),
            ),
            const SizedBox(height: 12),
            Row(
              children: [
                Expanded(
                  child: OutlinedButton(
                    onPressed: () => _pick(true),
                    child: Text(_from == null ? 'من' : _ymd.format(_from!)),
                  ),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: OutlinedButton(
                    onPressed: () => _pick(false),
                    child: Text(_to == null ? 'إلى' : _ymd.format(_to!)),
                  ),
                ),
              ],
            ),
            if (_days > 0)
              Padding(
                padding: const EdgeInsets.only(top: 8),
                child: Text('المدّة: $_days يوم',
                    style: Theme.of(context).textTheme.bodySmall),
              ),
            const SizedBox(height: 12),
            TextField(
              controller: _reason,
              maxLines: 2,
              decoration: const InputDecoration(
                  labelText: 'السبب (اختياري)', border: OutlineInputBorder()),
            ),
            const SizedBox(height: 14),
            FilledButton(
              onPressed: _busy ? null : _submit,
              child: Text(_busy ? 'يُرسَل…' : 'أرسل الطلب'),
            ),
          ],
        ),
      ),
    );
  }
}

class _ExcuseForm extends StatefulWidget {
  const _ExcuseForm({required this.api});

  final Api api;

  @override
  State<_ExcuseForm> createState() => _ExcuseFormState();
}

class _ExcuseFormState extends State<_ExcuseForm> {
  DateTime _date = DateTime.now();
  String _type = 'mission';
  final _reason = TextEditingController();
  bool _busy = false;

  // القيم التي تكتبها شاشة الويب حرفيًّا. كنتُ كتبتُ `permission`،
  // والشاشة تكتب `personal` — والخادم يُدخل ما يصله بلا فحص، فكان
  // التطبيق سيملأ الجدول بقيمةٍ لا ينتجها شيءٌ آخر في النظام، وتسقط
  // من كل تصفيةٍ أو تقرير مبنيّ على قيم الشاشة.
  static const _kinds = {
    'mission': 'مهمة عمل خارجية',
    'personal': 'استئذان شخصي',
    'manual_override': 'تصحيح بصمة',
  };

  Future<void> _submit() async {
    setState(() => _busy = true);
    try {
      final j = await widget.api.requestExcuse(
        date: _ymd.format(_date),
        type: _type,
        reason: _reason.text.trim(),
      );
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
        content: Text((j['message'] ?? 'سُجّل الطلب').toString()),
        backgroundColor: Colors.green.shade700,
      ));
      _reason.clear();
    } on ApiError catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text(e.message)));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            DropdownButtonFormField<String>(
              initialValue: _type,
              decoration: const InputDecoration(
                  labelText: 'نوع الطلب', border: OutlineInputBorder()),
              items: [
                for (final e in _kinds.entries)
                  DropdownMenuItem(value: e.key, child: Text(e.value)),
              ],
              onChanged: (v) => setState(() => _type = v ?? _type),
            ),
            const SizedBox(height: 12),
            OutlinedButton.icon(
              onPressed: () async {
                final now = DateTime.now();
                final d = await showDatePicker(
                  context: context,
                  initialDate: _date,
                  firstDate: now.subtract(const Duration(days: 90)),
                  lastDate: now.add(const Duration(days: 90)),
                );
                if (d != null) setState(() => _date = d);
              },
              icon: const Icon(Icons.event),
              label: Text(_ymd.format(_date)),
            ),
            const SizedBox(height: 12),
            TextField(
              controller: _reason,
              maxLines: 2,
              decoration: const InputDecoration(
                  labelText: 'السبب', border: OutlineInputBorder()),
            ),
            const SizedBox(height: 14),
            FilledButton(
              onPressed: _busy ? null : _submit,
              child: Text(_busy ? 'يُرسَل…' : 'أرسل'),
            ),
          ],
        ),
      ),
    );
  }
}
