def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in {'png', 'jpg', 'jpeg', 'pdf', 'doc', 'docx', 'xls', 'xlsx'}

def time_to_minutes(time_str):
    if not time_str: return 0
    try:
        h, m = map(int, time_str.split(':'))
        return h * 60 + m
    except:
        return 0

def format_currency(amount, settings=None):
    """تنسيق المبلغ المالي حسب إعدادات النظام"""
    if amount is None:
        amount = 0
    
    try:
        val = float(amount)
        # جلب الإعدادات إذا لم توفر
        if settings is None:
            # يمكن جلبها من قاعدة البيانات هنا إذا لزم الأمر، 
            # لكن يفضل تمريرها من الـ context processor لتجنب تكرار الاستعلامات
            symbol = 'د.ك'
            position = 'right'
            decimals = 2
        else:
            symbol = settings.get('currency_symbol', 'د.ك')
            position = settings.get('currency_position', 'right')
            decimals = int(settings.get('rounding_display_decimals', 2))
        
        formatted_val = f"{val:,.{decimals}f}"
        
        if position == 'left':
            return f"{symbol} {formatted_val}"
        else:
            return f"{formatted_val} {symbol}"
    except:
        return str(amount)
