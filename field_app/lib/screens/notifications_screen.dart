/// الإشعارات: ما يعنيك ولم تطلبه.
///
/// ولا تُعلَّم مقروءةً بمجرّد فتح الشاشة: من فتحها ثم أُغلق هاتفه
/// قبل أن يقرأ يفقدها من عينه. فالتعليم بفعلٍ صريح — ضغطةٌ على
/// الإشعار، أو «علّم الكل».
library;

import 'package:flutter/material.dart';

import '../api.dart';
import '../portal_api.dart';
import 'widgets.dart';

class NotificationsScreen extends StatefulWidget {
  const NotificationsScreen({super.key, required this.api, this.onChanged});

  final Api api;

  /// يُنادى بعدد غير المقروء بعد كل تغيير، لتتبعه الشارة في الإطار.
  final void Function(int unread)? onChanged;

  @override
  State<NotificationsScreen> createState() => _NotificationsScreenState();
}

class _NotificationsScreenState extends State<NotificationsScreen> {
  List<dynamic> _items = [];
  int _unread = 0;
  bool _loading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    try {
      final j = await widget.api.notifications();
      if (!mounted) return;
      setState(() {
        _items = (j['items'] as List?) ?? [];
        _unread = (j['unread'] as num?)?.toInt() ?? 0;
        _error = null;
      });
      widget.onChanged?.call(_unread);
    } on ApiError catch (e) {
      if (mounted) setState(() => _error = e.message);
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _markRead({List<int>? ids}) async {
    try {
      final j = await widget.api.markNotificationsRead(ids: ids);
      if (!mounted) return;
      final unread = (j['unread'] as num?)?.toInt() ?? 0;
      widget.onChanged?.call(unread);
      await _load();
    } on ApiError catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text(e.message)));
    }
  }

  IconData _iconFor(String kind) {
    switch (kind) {
      case 'leave_requested':
        return Icons.event_note_outlined;
      case 'leave_decided':
        return Icons.task_alt;
      case 'excuse_requested':
        return Icons.directions_walk;
      default:
        return Icons.notifications_none;
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_loading && _items.isEmpty) {
      return const Center(child: CircularProgressIndicator());
    }
    if (_error != null && _items.isEmpty) {
      return ErrorPane(message: _error!, onRetry: _load);
    }

    return RefreshIndicator(
      onRefresh: _load,
      child: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          if (_unread > 0)
            Align(
              alignment: AlignmentDirectional.centerEnd,
              child: TextButton.icon(
                onPressed: () => _markRead(),
                icon: const Icon(Icons.done_all),
                label: Text('علّم الكل مقروءًا ($_unread)'),
              ),
            ),
          if (_items.isEmpty)
            const Padding(
              padding: EdgeInsets.only(top: 60),
              child: Column(
                children: [
                  Icon(Icons.notifications_none, size: 44),
                  SizedBox(height: 12),
                  Text('لا إشعارات'),
                ],
              ),
            )
          else
            ..._items.map((raw) {
              final n = (raw as Map).cast<String, dynamic>();
              final unread = (n['is_read'] as num?)?.toInt() == 0;
              final id = (n['id'] as num).toInt();
              return Card(
                // غير المقروء يُميَّز باللون لا بالنصّ: تُقرأ الشاشة
                // بلمحة.
                color: unread
                    ? Theme.of(context).colorScheme.primaryContainer
                    : null,
                child: ListTile(
                  leading: Icon(_iconFor('${n['kind']}')),
                  title: Text('${n['title']}'),
                  subtitle: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      if ((n['body'] ?? '').toString().isNotEmpty)
                        Text('${n['body']}'),
                      Text('${n['created_at']}',
                          style: Theme.of(context).textTheme.bodySmall),
                    ],
                  ),
                  trailing: unread
                      ? const Icon(Icons.circle, size: 10)
                      : null,
                  onTap: unread ? () => _markRead(ids: [id]) : null,
                ),
              );
            }),
          const SizedBox(height: 32),
        ],
      ),
    );
  }
}
