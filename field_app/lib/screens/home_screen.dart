/// الصفحة الرئيسية: ما يراه الموظف عن نفسه.
///
/// الأقسام هي أقسام `portal/index.html` نفسها، ومصادرها المسارات
/// نفسها — فما يظهر هنا هو ما يظهر هناك، لا نسخةٌ تفترق عنه.
library;

import 'package:flutter/material.dart';

import '../api.dart';
import '../portal_api.dart';
import 'widgets.dart';

class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key, required this.api, required this.boot});

  final Api api;
  final Map<String, dynamic> boot;

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  Map<String, dynamic>? _data;
  String? _error;
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    try {
      final j = await widget.api.myData();
      if (mounted) {
        setState(() {
          _data = j;
          _error = null;
        });
      }
    } on ApiError catch (e) {
      if (mounted) setState(() => _error = e.message);
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final d = _data;
    if (_loading && d == null) {
      return const Center(child: CircularProgressIndicator());
    }
    if (d == null) {
      return ErrorPane(message: _error ?? 'تعذّر التحميل', onRetry: _load);
    }

    final emp = (d['employee'] as Map?)?.cast<String, dynamic>() ?? {};
    final today = (d['today'] as Map?)?.cast<String, dynamic>() ?? {};
    final stats = (d['stats'] as Map?)?.cast<String, dynamic>() ?? {};
    final leaves = (d['recent_leaves'] as List?) ?? [];

    return RefreshIndicator(
      onRefresh: _load,
      child: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          Card(
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Row(
                children: [
                  CircleAvatar(
                    radius: 26,
                    child: Text(
                      (emp['name'] ?? '?').toString().characters.firstOrNull ?? '?',
                      style: const TextStyle(fontSize: 22),
                    ),
                  ),
                  const SizedBox(width: 14),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text((emp['arabic_name'] ?? emp['name'] ?? '').toString(),
                            style: Theme.of(context).textTheme.titleMedium),
                        const SizedBox(height: 2),
                        Text(
                          [
                            emp['position'],
                            emp['department'],
                          ].where((x) => x != null && '$x'.isNotEmpty).join(' · '),
                          style: Theme.of(context).textTheme.bodySmall,
                        ),
                        if ((emp['employee_number'] ?? '').toString().isNotEmpty)
                          Text('الرقم الوظيفي: ${emp['employee_number']}',
                              style: Theme.of(context).textTheme.bodySmall),
                      ],
                    ),
                  ),
                ],
              ),
            ),
          ),
          const SizedBox(height: 16),

          const SectionTitle('يومك'),
          Card(
            child: Padding(
              padding: const EdgeInsets.all(14),
              child: Column(
                children: [
                  Row(
                    mainAxisAlignment: MainAxisAlignment.spaceAround,
                    children: [
                      _Slot(label: 'حضور', time: today['check_in']),
                      _Slot(label: 'تواجد', time: today['presence']),
                      _Slot(label: 'انصراف', time: today['check_out']),
                    ],
                  ),
                  if (((today['punches'] as List?) ?? []).isNotEmpty) ...[
                    const Divider(height: 24),
                    Wrap(
                      spacing: 6,
                      runSpacing: 6,
                      children: [
                        for (final t in (today['punches'] as List))
                          Chip(
                            label: Text('$t'),
                            visualDensity: VisualDensity.compact,
                          ),
                      ],
                    ),
                  ],
                ],
              ),
            ),
          ),
          const SizedBox(height: 16),

          const SectionTitle('هذا الشهر'),
          Card(
            child: Padding(
              padding: const EdgeInsets.all(14),
              child: Wrap(
                spacing: 24,
                runSpacing: 12,
                children: [
                  StatBox(label: 'أيام العمل', value: '${stats['days_worked'] ?? 0}'),
                  StatBox(
                      label: 'رصيد الإجازات',
                      value: '${stats['annual_balance'] ?? 0}',
                      hint: 'يوم'),
                  StatBox(
                      label: 'المستهلك',
                      value: '${stats['annual_taken'] ?? 0}',
                      hint: 'يوم'),
                  if (((stats['loan_remaining'] as num?) ?? 0) > 0)
                    StatBox(
                        label: 'متبقّي السلف',
                        value: '${stats['loan_remaining']}',
                        hint: 'قسط ${stats['monthly_installment']}'),
                ],
              ),
            ),
          ),
          const SizedBox(height: 16),

          const SectionTitle('آخر الطلبات'),
          if (leaves.isEmpty)
            const Card(
              child: ListTile(
                leading: Icon(Icons.inbox_outlined),
                title: Text('لا طلبات سابقة'),
              ),
            )
          else
            ...leaves.map((raw) {
              final r = (raw as Map).cast<String, dynamic>();
              return Card(
                child: ListTile(
                  leading: StatusDot(status: '${r['status']}'),
                  title: Text('${r['leave_type_name'] ?? 'إجازة'}'
                      ' · ${r['days_count'] ?? ''} يوم'),
                  subtitle: Text('من ${r['start_date']} إلى ${r['end_date']}'),
                  trailing: Text(statusLabel('${r['status']}'),
                      style: Theme.of(context).textTheme.bodySmall),
                ),
              );
            }),
          const SizedBox(height: 32),
        ],
      ),
    );
  }
}

class _Slot extends StatelessWidget {
  const _Slot({required this.label, required this.time});

  final String label;
  final Object? time;

  @override
  Widget build(BuildContext context) {
    final t = time?.toString();
    final has = t != null && t.isNotEmpty;
    return Column(
      children: [
        Text(has ? t : '—',
            style: Theme.of(context).textTheme.titleLarge?.copyWith(
                  color: has ? null : Theme.of(context).disabledColor,
                )),
        const SizedBox(height: 2),
        Text(label, style: Theme.of(context).textTheme.bodySmall),
      ],
    );
  }
}
