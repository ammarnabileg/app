import sqlite3

DB_PATH = r'C:\ProgramData\HRSystem\hr_system.db'

try:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    
    # Check if password_reset_tokens table exists
    row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='password_reset_tokens'").fetchone()
    print('password_reset_tokens table exists:', bool(row))
    
    if row:
        cols = conn.execute('PRAGMA table_info(password_reset_tokens)').fetchall()
        print('Columns:')
        for c in cols:
            print(' ', dict(c))
    
    # Check users table
    u_row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'").fetchone()
    print('users table exists:', bool(u_row))
    
    if u_row:
        cnt = conn.execute('SELECT COUNT(*) FROM users').fetchone()[0]
        print('Total users:', cnt)
        
        emp_users = conn.execute("SELECT id, username, role, employee_id, is_active FROM users WHERE role='employee' LIMIT 5").fetchall()
        print('Employee users (first 5):')
        for u in emp_users:
            print(' ', dict(u))
    
    # Check employees
    emp_cnt = conn.execute('SELECT COUNT(*) FROM employees WHERE is_active=1').fetchone()[0]
    print('Active employees:', emp_cnt)
    
except Exception as e:
    print('ERROR:', e)
