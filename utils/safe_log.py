# -*- coding: utf-8 -*-
"""ملفّات لوج ليها حدّ — وبتشتغل على ويندوز.

## اللي كان بيحصل

الكود كان كاتب `from logging import FileHandler as RotatingFileHandler`: الاسم بيقول
«بيتلفّ» والحقيقة FileHandler عادي، مالوش حدّ. (غالبًا اتعمل كده لأن RotatingFileHandler
الأصلي بيقع على ويندوز: بيحاول يعمل rename للملف وهو مفتوح في عملية تانية — سيرفر
ADMS والبرنامج — فيطلع PermissionError.) والنتيجة: `adms_protocol.log` وصل ٢٨٠ ميجا عند
عميل، لأن كل بصمة صباع ووش وصورة بتتكتب فيه كاملة base64.

## دلوقتي

- `SafeRotatingHandler`: بيفتح الملف ويقفله مع كل سطر (مفيش ملف ماسكه طول الوقت)، ولما يعدّي
  الحدّ بيلفّه (`.1` `.2` …). ولو اللفّ فشل لأن الملف مفتوح في حتّة تانية، بيكمّل كتابة
  ويحاول المرة الجاية — مابيوقعش ومابيضيّعش السطر.
- ملف قديم أكبر من الحدّ بكتير (زي الـ٢٨٠ ميجا): أوّل لفّ بيحتفظ بآخر جزء منه بس.
- `shrink`: أيّ قيمة طويلة (بصمة، وش، صورة) بتتكتب «<base64 12345 حرف>» بدل محتواها.
"""
import logging
import os
import re
import threading

MB = 1024 * 1024
_LONG = re.compile(r'(\b(?:TMP|Tmp|tmp|Template|Content|content|Photo|photo|FPTmp|Face)=)([A-Za-z0-9+/=]{80,})')


def shrink(text):
    """يشيل محتوى البصمات والصور من النص (بيفضل الاسم والطول)."""
    if not text:
        return text
    return _LONG.sub(lambda m: f'{m.group(1)}<base64 {len(m.group(2))} حرف>', str(text))


class SafeRotatingHandler(logging.Handler):
    def __init__(self, filename, max_bytes=20 * MB, backups=3, encoding='utf-8', level=logging.NOTSET):
        super().__init__(level)
        self.filename = os.path.abspath(filename)
        self.max_bytes = int(max_bytes)
        self.backups = int(backups)
        self.encoding = encoding
        self._lock = threading.Lock()
        try:
            os.makedirs(os.path.dirname(self.filename), exist_ok=True)
        except Exception:
            pass

    def _rotate(self):
        try:
            size = os.path.getsize(self.filename)
        except OSError:
            return
        if size < self.max_bytes:
            return
        try:
            for i in range(self.backups - 1, 0, -1):
                src, dst = f'{self.filename}.{i}', f'{self.filename}.{i + 1}'
                if os.path.exists(src):
                    if os.path.exists(dst):
                        os.remove(dst)
                    os.rename(src, dst)
            first = f'{self.filename}.1'
            if os.path.exists(first):
                os.remove(first)
            if size > 2 * self.max_bytes:
                # ملف ضخم من قبل الحدّ: آخر جزء بس يتحفظ.
                with open(self.filename, 'rb') as f:
                    f.seek(size - self.max_bytes)
                    tail = f.read()
                with open(first, 'wb') as f:
                    f.write(tail)
                os.remove(self.filename)
            else:
                os.rename(self.filename, first)
        except OSError:
            pass                    # مفتوح في عملية تانية — نحاول المرة الجاية

    def emit(self, record):
        try:
            msg = self.format(record)
            with self._lock:
                self._rotate()
                with open(self.filename, 'a', encoding=self.encoding) as f:
                    f.write(msg + '\n')
        except Exception:
            self.handleError(record)
