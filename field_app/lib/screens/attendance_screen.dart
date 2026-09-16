/// سجلّ الحضور: شهرٌ في كل مرّة.
library;

import 'package:flutter/material.dart';

import '../api.dart';
import '../portal_api.dart';
import 'widgets.dart';

class AttendanceScreen extends StatefulWidget {
  const AttendanceScreen({super.key, required this.api});

  final Api api;

  @override
  State<AttendanceScreen> createState() => _AttendanceScreenState();
}

class _AttendanceScreenState extends State<AttendanceScreen> {
  late DateTime _month = DateTime(DateTime.now().year, DateTime.now().month);
  List<dynamic> _records = [];
  bool _loading = true;
  String? _error;

  static const _monthNames = [
    'يناير', 'فبراير', 'مارس', 'أبريل', 'مايو', 'يونيو',
    'يوليو', 'أغسطس', 'سبتمبر', 'أكتوبر', 'نوفمبر', 'ديسمبر',
  ];

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    try {
      final j = await widget.api
          .attendance(month: _month.month, year: _month.year);
      if (mounted) {
        setState(() {
          _records = (j['records'] as List?) ?? [];
          _error = null;
        });
      }
    } on ApiError catch (e) {
      if (mounted) setState(() => _error = e.message);
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  void _shift(int months) {
    setState(() => _month = DateTime(_month.year, _month.month + months));
    _load();
  }

  @override
  Widget build(BuildContext context) {
    // الشهر القادم لا سجلّ له: يُعطَّل الزرّ بدل أن يُعرض شهرٌ فارغ
    // يبدو كأن السجلّ ضاع.
    final now = DateTime.now();
    final atLatest =
        _month.year == now.year && _month.month == now.month;

    return Column(
      children: [
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
          child: Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              IconButton(
                onPressed: () => _shift(-1),
                icon: const Icon(Icons.chevron_right),
                tooltip: 'الشهر السابق',
              ),
              Text('${_monthNames[_month.month - 1]} ${_month.year}',
                  style: Theme.of(context).textTheme.titleMedium),
              IconButton(
                onPressed: atLatest ? null : () => _shift(1),
                icon: const Icon(Icons.chevron_left),
                tooltip: 'الشهر التالي',
              ),
            ],
          ),
        ),
        Expanded(
          child: _loading && _records.isEmpty
              ? const Center(child: CircularProgressIndicator())
              : _error != null && _records.isEmpty
                  ? ErrorPane(message: _error!, onRetry: _load)
                  : RefreshIndicator(
                      onRefresh: _load,
                      child: _records.isEmpty
                          ? ListView(
                              children: const [
                                SizedBox(height: 80),
                                Center(child: Text('لا سجلّات في هذا الشهر')),
                              ],
                            )
                          : ListView.builder(
                              padding: const EdgeInsets.all(12),
                              itemCount: _records.length,
                              itemBuilder: (_, i) {
                                final r = (_records[i] as Map)
                                    .cast<String, dynamic>();
                                final status = '${r['status']}';
                                return Card(
                                  child: ListTile(
                                    dense: true,
                                    leading: Container(
                                      width: 10,
                                      height: 10,
                                      decoration: BoxDecoration(
                                        shape: BoxShape.circle,
                                        color: status == 'حاضر'
                                            ? Colors.green
                                            : status == 'بصمة واحدة'
                                                ? Colors.orange
                                                : Colors.red,
                                      ),
                                    ),
                                    title: Text('${r['date']}'),
                                    subtitle: Text(
                                        'دخول ${r['check_in']} · خروج ${r['check_out']}'),
                                    trailing: Text(status,
                                        style: Theme.of(context)
                                            .textTheme
                                            .bodySmall),
                                  ),
                                );
                              },
                            ),
                    ),
        ),
      ],
    );
  }
}
