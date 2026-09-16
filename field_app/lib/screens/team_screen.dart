/// فريقي: من حضر اليوم، وما ينتظر اعتمادي.
///
/// الاعتماد يكتب في سجلّ إجازات موظف، فالخادم يتحقّق من أن الطالب
/// مديرُه فعلًا. ولا يُفترض هنا شيء: الزرّ يُعرض، والخادم يحكم.
library;

import 'package:flutter/material.dart';

import '../api.dart';
import '../portal_api.dart';
import 'widgets.dart';

class TeamScreen extends StatefulWidget {
  const TeamScreen({super.key, required this.api});

  final Api api;

  @override
  State<TeamScreen> createState() => _TeamScreenState();
}

class _TeamScreenState extends State<TeamScreen> {
  Map<String, dynamic>? _summary;
  List<dynamic> _approvals = [];
  bool _loading = true;
  String? _error;
  final _working = <int>{};

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    try {
      final s = await widget.api.teamSummary();
      final a = await widget.api.teamApprovals();
      if (mounted) {
        setState(() {
          _summary = s;
          _approvals = (a['requests'] as List?) ?? [];
          _error = null;
        });
      }
    } on ApiError catch (e) {
      if (mounted) setState(() => _error = e.message);
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _decide(int id, String action) async {
    final label = action == 'approve' ? 'اعتماد' : 'رفض';
    final yes = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: Text('$label الطلب'),
        content: Text('سيُسجَّل $label هذا الطلب باسمك. أمتأكّد؟'),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(c, false),
              child: const Text('تراجع')),
          FilledButton(
              onPressed: () => Navigator.pop(c, true), child: Text(label)),
        ],
      ),
    );
    if (yes != true) return;

    setState(() => _working.add(id));
    try {
      final j = await widget.api.approveRequest(id, action);
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
        content: Text((j['message'] ?? 'تمّ').toString()),
        backgroundColor: Colors.green.shade700,
      ));
      await _load();
    } on ApiError catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text(e.message)));
    } finally {
      if (mounted) setState(() => _working.remove(id));
    }
  }

  @override
  Widget build(BuildContext context) {
    final s = _summary;
    if (_loading && s == null) {
      return const Center(child: CircularProgressIndicator());
    }
    if (s == null) {
      return ErrorPane(message: _error ?? 'تعذّر التحميل', onRetry: _load);
    }

    final team = (s['team_members'] as List?) ?? [];

    return RefreshIndicator(
      onRefresh: _load,
      child: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          Card(
            child: Padding(
              padding: const EdgeInsets.all(14),
              child: Wrap(
                spacing: 24,
                runSpacing: 12,
                children: [
                  StatBox(label: 'الفريق', value: '${s['total_team'] ?? 0}'),
                  StatBox(label: 'حاضر', value: '${s['present_today'] ?? 0}'),
                  StatBox(label: 'غائب', value: '${s['absent_today'] ?? 0}'),
                  StatBox(
                      label: 'بانتظار اعتمادك',
                      value: '${s['pending_approvals_count'] ?? 0}'),
                ],
              ),
            ),
          ),
          const SizedBox(height: 20),

          const SectionTitle('بانتظار اعتمادك'),
          if (_approvals.isEmpty)
            const Card(
              child: ListTile(
                leading: Icon(Icons.done_all),
                title: Text('لا طلبات معلّقة'),
              ),
            )
          else
            ..._approvals.map((raw) {
              final r = (raw as Map).cast<String, dynamic>();
              final id = (r['id'] as num).toInt();
              final busy = _working.contains(id);
              return Card(
                child: Padding(
                  padding: const EdgeInsets.all(12),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text('${r['employee_name']} (${r['employee_number']})',
                          style: Theme.of(context).textTheme.titleSmall),
                      const SizedBox(height: 4),
                      Text('${r['leave_type_name']} · ${r['days_count']} يوم'),
                      Text('من ${r['start_date']} إلى ${r['end_date']}',
                          style: Theme.of(context).textTheme.bodySmall),
                      if ((r['reason'] ?? '').toString().isNotEmpty)
                        Padding(
                          padding: const EdgeInsets.only(top: 4),
                          child: Text('السبب: ${r['reason']}',
                              style: Theme.of(context).textTheme.bodySmall),
                        ),
                      const SizedBox(height: 10),
                      Row(
                        children: [
                          Expanded(
                            child: FilledButton(
                              onPressed:
                                  busy ? null : () => _decide(id, 'approve'),
                              child: const Text('اعتماد'),
                            ),
                          ),
                          const SizedBox(width: 8),
                          Expanded(
                            child: OutlinedButton(
                              onPressed:
                                  busy ? null : () => _decide(id, 'reject'),
                              child: const Text('رفض'),
                            ),
                          ),
                        ],
                      ),
                    ],
                  ),
                ),
              );
            }),

          const SizedBox(height: 20),
          const SectionTitle('الفريق اليوم'),
          ...team.map((raw) {
            final m = (raw as Map).cast<String, dynamic>();
            final present = m['is_present'] == true;
            return Card(
              child: ListTile(
                dense: true,
                leading: CircleAvatar(
                  backgroundColor:
                      present ? Colors.green.shade100 : Colors.red.shade100,
                  child: Icon(present ? Icons.check : Icons.close,
                      size: 18,
                      color: present
                          ? Colors.green.shade800
                          : Colors.red.shade800),
                ),
                title: Text('${m['name']}'),
                subtitle: Text(present
                    ? 'من ${m['first_punch'] ?? '—'} · ${m['punches_count']} بصمة'
                    : 'لا بصمات اليوم'),
                trailing: Text('${m['position'] ?? ''}',
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
