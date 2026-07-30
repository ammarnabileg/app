from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from flask_babel import gettext
from werkzeug.security import check_password_hash
from utils.db import get_db_connection

auth_bp = Blueprint('auth', __name__)

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        remember = request.form.get('remember')
        
        conn = get_db_connection()
        user = conn.execute('SELECT * FROM users WHERE username = ? AND is_active = 1', 
                          (username,)).fetchone()
        pass # conn.close() removed to prevent leak in Flask g
        
        if user and check_password_hash(user['password'], password):
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['full_name'] = user['full_name']
            session['role'] = user['role']
            
            if remember:
                session.permanent = True
            
            flash(gettext('x.f_welcome_login') % {'p0': f'{user["full_name"]}'}, 'success')
            return redirect(url_for('main.index'))
        else:
            flash(gettext('x.f_bad_credentials'), 'error')
    
    return render_template('login.html')

@auth_bp.route('/logout')
def logout():
    session.clear()
    flash(gettext('x.f_logged_out'), 'success')
    return redirect(url_for('auth.login'))
