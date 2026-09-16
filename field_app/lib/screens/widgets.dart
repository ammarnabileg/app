/// قطعٌ صغيرة تتكرّر في شاشات البوابة.
library;

import 'package:flutter/material.dart';

class SectionTitle extends StatelessWidget {
  const SectionTitle(this.text, {super.key});

  final String text;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.only(bottom: 8),
        child: Text(text, style: Theme.of(context).textTheme.titleMedium),
      );
}

class StatBox extends StatelessWidget {
  const StatBox({super.key, required this.label, required this.value, this.hint});

  final String label;
  final String value;
  final String? hint;

  @override
  Widget build(BuildContext context) => Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(value, style: Theme.of(context).textTheme.titleLarge),
          Text(label, style: Theme.of(context).textTheme.bodySmall),
          if (hint != null)
            Text(hint!,
                style: Theme.of(context)
                    .textTheme
                    .bodySmall
                    ?.copyWith(color: Theme.of(context).disabledColor)),
        ],
      );
}

/// ألوان الحالة تتبع الدلالة لا الذوق: أخضر مقبول، أحمر مرفوض،
/// برتقالي منتظر.
Color statusColor(String s) {
  switch (s) {
    case 'approved':
      return Colors.green;
    case 'rejected':
      return Colors.red;
    default:
      return Colors.orange;
  }
}

String statusLabel(String s) {
  switch (s) {
    case 'approved':
      return 'معتمد';
    case 'rejected':
      return 'مرفوض';
    case 'pending':
      return 'قيد الاعتماد';
    default:
      return s;
  }
}

class StatusDot extends StatelessWidget {
  const StatusDot({super.key, required this.status});

  final String status;

  @override
  Widget build(BuildContext context) => Container(
        width: 12,
        height: 12,
        margin: const EdgeInsets.only(top: 6),
        decoration: BoxDecoration(
          color: statusColor(status),
          shape: BoxShape.circle,
        ),
      );
}

/// خطأٌ يُعرض مع طريق للخروج منه.
///
/// شاشةٌ تقول «تعذّر التحميل» ولا تعطي زرًّا تترك المستخدم يُغلق
/// التطبيق ويفتحه — وهذا ما يفعله فعلًا.
class ErrorPane extends StatelessWidget {
  const ErrorPane({super.key, required this.message, this.onRetry});

  final String message;
  final VoidCallback? onRetry;

  @override
  Widget build(BuildContext context) => Center(
        child: Padding(
          padding: const EdgeInsets.all(32),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(Icons.cloud_off,
                  size: 44, color: Theme.of(context).disabledColor),
              const SizedBox(height: 16),
              Text(message, textAlign: TextAlign.center),
              if (onRetry != null) ...[
                const SizedBox(height: 16),
                OutlinedButton.icon(
                  onPressed: onRetry,
                  icon: const Icon(Icons.refresh),
                  label: const Text('أعد المحاولة'),
                ),
              ],
            ],
          ),
        ),
      );
}

/// حسابٌ بلا سجلّ موظف: يقرأ ولا يكتب.
class NoEmployeePane extends StatelessWidget {
  const NoEmployeePane({super.key});

  @override
  Widget build(BuildContext context) => Center(
        child: Padding(
          padding: const EdgeInsets.all(32),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(Icons.badge_outlined,
                  size: 44, color: Theme.of(context).disabledColor),
              const SizedBox(height: 16),
              const Text(
                'حسابك غير مرتبط بسجلّ موظف، فلا يمكن التسجيل باسمك.\n'
                'راجع إدارة الموارد البشرية لربط الحساب.',
                textAlign: TextAlign.center,
              ),
            ],
          ),
        ),
      );
}
