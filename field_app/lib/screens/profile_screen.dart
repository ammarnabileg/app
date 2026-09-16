/// حسابي: البيانات الوظيفية والمالية.
///
/// هي بعينها تبويبة «حسابي» في البوابة — كانت القسم الوحيد الذي
/// نسيتُه حين نقلت البقيّة، وأمسكه جردٌ لتبويبات الصفحة لا ذاكرتي.
///
/// والراتب يظهر هنا. فالشاشة تُقرأ في الميدان وبين الناس، ويُخفى
/// افتراضًا ويُكشف بضغطة — لا لأن الموظف لا يملك رؤيته، بل لأنه لا
/// يملك اختيار من يراه فوق كتفه.
library;

import 'package:flutter/material.dart';

import '../api.dart';
import '../portal_api.dart';
import 'widgets.dart';

class ProfileScreen extends StatefulWidget {
  const ProfileScreen({super.key, required this.api, required this.boot});

  final Api api;
  final Map<String, dynamic> boot;

  @override
  State<ProfileScreen> createState() => _ProfileScreenState();
}

class _ProfileScreenState extends State<ProfileScreen> {
  Map<String, dynamic>? _emp;
  bool _loading = true;
  bool _showSalary = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    try {
      // الراتب والهاتف والرقم المدني في my-data لا في bootstrap.
      final j = await widget.api.myData();
      if (mounted) {
        setState(() {
          _emp = (j['employee'] as Map?)?.cast<String, dynamic>();
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
    if (widget.boot['has_employee_record'] != true) {
      return const NoEmployeePane();
    }
    if (_loading && _emp == null) {
      return const Center(child: CircularProgressIndicator());
    }
    final e = _emp;
    if (e == null) {
      return ErrorPane(message: _error ?? 'تعذّر التحميل', onRetry: _load);
    }

    final manager = (widget.boot['employee'] as Map?)?['manager_name'];

    return RefreshIndicator(
      onRefresh: _load,
      child: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          Card(
            child: Padding(
              padding: const EdgeInsets.symmetric(vertical: 24, horizontal: 16),
              child: Column(
                children: [
                  CircleAvatar(
                    radius: 36,
                    child: Text(
                      (e['name'] ?? '?').toString().characters.firstOrNull ?? '?',
                      style: const TextStyle(fontSize: 28),
                    ),
                  ),
                  const SizedBox(height: 12),
                  Text((e['arabic_name'] ?? e['name'] ?? '').toString(),
                      style: Theme.of(context).textTheme.titleLarge,
                      textAlign: TextAlign.center),
                  const SizedBox(height: 4),
                  Text(
                    [e['position'], e['department']]
                        .where((x) => x != null && '$x'.isNotEmpty)
                        .join(' • '),
                    style: Theme.of(context).textTheme.bodySmall,
                    textAlign: TextAlign.center,
                  ),
                  const SizedBox(height: 10),
                  Chip(
                    label: Text('رقم الموظف: ${e['employee_number'] ?? '-'}'),
                    visualDensity: VisualDensity.compact,
                  ),
                ],
              ),
            ),
          ),
          const SizedBox(height: 16),

          const SectionTitle('البيانات الوظيفية والمالية'),
          Card(
            child: Column(
              children: [
                ListTile(
                  leading: const Icon(Icons.payments_outlined),
                  title: const Text('الراتب الأساسي'),
                  subtitle: const Text('المعتمد في العقد'),
                  trailing: Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Text(
                        _showSalary ? '${e['salary'] ?? 0} د.ك' : '••••',
                        style: Theme.of(context).textTheme.titleMedium?.copyWith(
                              color: Colors.green.shade700,
                            ),
                      ),
                      IconButton(
                        onPressed: () =>
                            setState(() => _showSalary = !_showSalary),
                        icon: Icon(_showSalary
                            ? Icons.visibility_off_outlined
                            : Icons.visibility_outlined),
                        tooltip: _showSalary ? 'إخفاء' : 'إظهار',
                      ),
                    ],
                  ),
                ),
                const Divider(height: 1),
                ListTile(
                  leading: const Icon(Icons.supervisor_account_outlined),
                  title: const Text('المدير المباشر'),
                  subtitle: const Text('مسؤول الاعتماد'),
                  trailing: Text('${manager ?? 'الموارد البشرية'}'),
                ),
                const Divider(height: 1),
                ListTile(
                  leading: const Icon(Icons.event_available_outlined),
                  title: const Text('تاريخ التعيين'),
                  subtitle: const Text('بداية الخدمة'),
                  trailing: Text('${e['hire_date'] ?? '-'}'),
                ),
                if ((e['phone'] ?? '').toString().isNotEmpty) ...[
                  const Divider(height: 1),
                  ListTile(
                    leading: const Icon(Icons.phone_outlined),
                    title: const Text('الهاتف'),
                    trailing: Text('${e['phone']}',
                        textDirection: TextDirection.ltr),
                  ),
                ],
                if ((e['civil_id'] ?? '').toString().isNotEmpty) ...[
                  const Divider(height: 1),
                  ListTile(
                    leading: const Icon(Icons.badge_outlined),
                    title: const Text('الرقم المدني'),
                    trailing: Text('${e['civil_id']}',
                        textDirection: TextDirection.ltr),
                  ),
                ],
              ],
            ),
          ),
          const SizedBox(height: 32),
        ],
      ),
    );
  }
}
