import os
import sys
import threading
import time
import json
import webbrowser
import shutil
import customtkinter as ctk
from PIL import Image
from tkinter import filedialog
import pystray
from pystray import MenuItem as item
import socket
import urllib.request

from utils.db import DB_PATH, DATA_DIR, init_db, get_db_connection, get_setting, set_setting
from app import main as start_flask
from utils.update_manager import check_for_updates, download_and_install_update
from utils.version_info import CURRENT_VERSION

# ================== CONFIG ==================
DEFAULT_PORT = 5000
DEFAULT_ADMS_PORT = 8081
DEFAULT_HOST = "127.0.0.1"
CONFIG_PATH = os.path.join(DATA_DIR, "launcher_config.json")

# Colors
PRIMARY = "#3b82f6"       # Blue-500
PRIMARY_HOVER = "#2563eb" # Blue-600
SUCCESS = "#10b981"       # Emerald-500
DANGER = "#ef4444"        # Red-500
DANGER_DARK = "#450a0a"   # Red-950
WARNING = "#f59e0b"       # Amber-500
BG_DARK = "#0f172a"       # Slate-900
BG_SIDEBAR = "#1e293b"    # Slate-800
BG_CARD = "#1e293b"       # Slate-800
TEXT_MAIN = "#f8fafc"     # Slate-50
TEXT_MUTED = "#94a3b8"    # Slate-400

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("dark-blue")

# ================== TRANSLATIONS ==================
TRANSLATIONS = {
    "ar": {
        "app_title": "ON point HR System Launcher",
        "sidebar_title": "HR SYSTEM",
        "sidebar_subtitle": "لوحة التحكم الإدارية",
        "btn_home": "🏠 الرئيسية",
        "btn_adms": "📡 خادم البصمة (ADMS)",
        "btn_open": "🚀 فتح النظام",
        "btn_backup": "💾 نسخة احتياطية",
        "btn_port": "🔧 إعدادات المنفذ",
        "btn_hide": "🙈 إخفاء في الخلفية",
        "btn_exit": "🚪 خروج نهائي",
        "btn_oracle": "🗄️ إعدادات أوراكل",
        "btn_logs_view": "📜 سجل النظام",
        "logs_title": "سجل النظام",
        "btn_lang": "🇺🇸 English",
        "hero_title": "لوحة المعلومات",
        "adms_title": "خادم ADMS",
        "status_starting": "جاري بدء التشغيل...",
        "status_starting_desc": "يرجى الانتظار بينما يتم تهيئة الخادم وقاعدة البيانات",
        "status_running": "النظام يعمل بكفاءة",
        "status_running_desc": "يمكنك الوصول للنظام عبر المتصفح على المنفذ {}",
        "status_stopped": "الخادم متوقف",
        "status_stopped_desc": "اضغط على زر التشغيل للبدء",
        "status_error": "خطأ في التشغيل",
        "label_port": "المنفذ (Port)",
        "label_adms_port": "منفذ ADMS",
        "label_ip": "عنوان IP المحلي",
        "label_public_ip": "عنوان IP الخارجي",
        "label_logs": "سجل العمليات السريع",
        "log_init": "تهيئة قاعدة البيانات...",
        "log_success": "تم تشغيل الخادم بنجاح على http://0.0.0.0:{}",
        "log_adms_success": "تم تشغيل خادم ADMS بنجاح على http://0.0.0.0:{}",
        "log_fail": "فشل تفعيل الخادم: {}",
        "log_browser": "فتح المتصفح: {}",
        "log_backup_fail": "فشل النسخ الاحتياطي: {}",
        "log_backup_success": "تم حفظ النسخة: {}",
        "log_minimized": "تم التصغير إلى شريط المهام.",
        "log_restored": "تمت الاستعادة من شريط المهام.",
        "log_shutdown": "إغلاق النظام...",
        "dialog_port_title": "تغيير المنفذ",
        "dialog_port_text": "أدخل رقم المنفذ الجديد (يتطلب إعادة تشغيل):",
        "dialog_backup_title": "حفظ نسخة احتياطية",
        "msg_port_changed": "تم تغيير المنفذ إلى {}. يرجى إعادة التشغيل.",
        "btn_start_adms": "تشغيل ADMS",
        "btn_stop_adms": "إيقاف ADMS",
        "tray_restore": "استعادة الواجهة",
        "tray_open": "فتح الموقع",
        "tray_exit": "خروج",
        "btn_sync_queue": "📊 طابور المزامنة",
        "sync_queue_title": "طابور مزامنة أوراكل"
    },
    "en": {
        "app_title": "ON point HR System Launcher",
        "sidebar_title": "HR SYSTEM",
        "sidebar_subtitle": "Management Console",
        "btn_home": "🏠 Home",
        "btn_adms": "📡 ADMS Server",
        "btn_open": "🚀 Open System",
        "btn_backup": "💾 Backup Database",
        "btn_port": "🔧 Port Settings",
        "btn_hide": "🙈 Hide to Tray",
        "btn_exit": "🚪 Exit",
        "btn_oracle": "🗄️ Oracle Settings",
        "btn_logs_view": "📜 System Logs",
        "logs_title": "System Logs",
        "btn_lang": "🇸🇦 العربية",
        "hero_title": "Dashboard",
        "adms_title": "ADMS Server",
        "status_starting": "Starting up...",
        "status_starting_desc": "Please wait while server and database are initializing",
        "status_running": "System Running",
        "status_running_desc": "Access the system via browser on port {}",
        "status_stopped": "Server Stopped",
        "status_stopped_desc": "Click Start to run",
        "status_error": "Startup Error",
        "label_port": "Port",
        "label_adms_port": "ADMS Port",
        "label_ip": "Local IP",
        "label_public_ip": "Public IP",
        "label_logs": "Live Logs",
        "log_init": "Initializing database...",
        "log_success": "Server started successfully on http://0.0.0.0:{}",
        "log_adms_success": "ADMS Server started successfully on http://0.0.0.0:{}",
        "log_fail": "Failed to start server: {}",
        "log_browser": "Opening browser: {}",
        "log_backup_fail": "Backup failed: {}",
        "log_backup_success": "Backup saved: {}",
        "log_minimized": "Minimized to system tray.",
        "log_restored": "Restored from tray.",
        "log_shutdown": "Shutting down...",
        "dialog_port_title": "Change Port",
        "dialog_port_text": "Enter new port number (Restart required):",
        "dialog_backup_title": "Save Backup",
        "msg_port_changed": "Port changed to {}. Please restart.",
        "btn_start_adms": "Start ADMS",
        "btn_stop_adms": "Stop ADMS",
        "tray_restore": "Restore Window",
        "tray_open": "Open Site",
        "tray_exit": "Exit",
        "btn_sync_queue": "📊 Sync Queue",
        "sync_queue_title": "Oracle Sync Queue"
    }
}

# ================== CONFIG UTILS ==================
def load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except: return {}
    return {}

def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)

def get_setting(key, default):
    return load_config().get(key, default)

def set_setting(key, value):
    cfg = load_config()
    cfg[key] = value
    save_config(cfg)

def get_ip_address():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def get_public_ip():
    try:
        with urllib.request.urlopen('https://api.ipify.org', timeout=2) as response:
            return response.read().decode('utf-8')
    except:
        return "N/A"

# ================== SERVER ==================
def start_server(port, status_cb):
    try:
        init_db()
        os.environ["APP_PORT"] = str(port)
        os.environ["APP_HOST"] = "0.0.0.0"
        t = threading.Thread(target=start_flask, daemon=True)
        t.start()
        status_cb(True)
    except Exception as e:
        status_cb(False, str(e))

def start_adms_process(port, status_cb):
    try:
        # Load Oracle config to Env so ADMS reads it properly
        cfg = load_config()
        os.environ["ORACLE_HOST"] = str(cfg.get("oracle_host", "localhost"))
        os.environ["ORACLE_PORT"] = str(cfg.get("oracle_port", "1521"))
        os.environ["ORACLE_SERVICE"] = str(cfg.get("oracle_service", "XEPDB1"))
        os.environ["ORACLE_USER"] = str(cfg.get("oracle_user", "HR"))
        os.environ["ORACLE_PASSWORD"] = str(cfg.get("oracle_password", "password"))
        os.environ["ORACLE_TABLE_NAME"] = str(cfg.get("oracle_table", "ATTENDANCE_LOGS"))
        
        # Import dynamically to avoid early init issues
        from adms_server import app as adms_app
        os.environ["ADMS_PORT"] = str(port)
        
        def run_adms():
            try:
                adms_app.run(host='0.0.0.0', port=port, use_reloader=False)
            except Exception as e:
                status_cb(False, str(e))
        
        t = threading.Thread(target=run_adms, daemon=True)
        t.start()
        status_cb(True)
    except Exception as e:
        status_cb(False, str(e))

# ================== UI APP ==================
class HRLauncher:

    def __init__(self):
        self.root = ctk.CTk()
        self.root.geometry("1000x600")
        self.root.minsize(900, 550)
        self.root.configure(fg_color=BG_DARK)
        
        self.local_ip = get_ip_address()
        self.public_ip = "..." 
        try:
           self.public_ip = get_public_ip()
        except: pass
        
        self.port = int(get_setting("port", DEFAULT_PORT))
        self.adms_port = int(get_setting("adms_port", DEFAULT_ADMS_PORT))
        self.lang = get_setting("lang", "ar")
        self.server_running = False
        self.adms_running = False
        self.log_history = []
        self.log_textbox = None
        
        self.current_view = "home" # home or adms

        self.root.title(self.t("app_title"))
        self.root.protocol("WM_DELETE_WINDOW", self.hide_to_tray)

        self.setup_ui()
        self.setup_oracle_logger_hook()
        self.startup()
        self.startup_adms() # Auto-start ADMS if preferred, or manual? Let's auto-start for convenience.

    def t(self, key):
        return TRANSLATIONS.get(self.lang, TRANSLATIONS["ar"]).get(key, key)

    def toggle_language(self):
        self.lang = "en" if self.lang == "ar" else "ar"
        set_setting("lang", self.lang)
        self.refresh_ui()

    def refresh_ui(self):
        for widget in self.root.winfo_children():
            widget.destroy()
        self.setup_ui()
        
        # Restore status
        if self.server_running:
            self.update_status_ui(True)
        else:
            self.update_status_ui(False, "Restarting UI...")
            
        if self.adms_running:
            self.update_adms_status_ui(True)

    def switch_view(self, view):
        self.current_view = view
        self.refresh_ui()

    def setup_ui(self):
        self.root.title(self.t("app_title"))
        pack_side = "left" if self.lang == "en" else "right"
        
        # --- Sidebar ---
        self.sidebar = ctk.CTkFrame(self.root, width=260, corner_radius=0, fg_color=BG_SIDEBAR)
        self.sidebar.pack(side=pack_side, fill="y")

        # Logo
        title_frame = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        title_frame.pack(pady=(40, 20), padx=20)
        ctk.CTkLabel(title_frame, text=self.t("sidebar_title"), font=("Segoe UI", 28, "bold"), text_color=TEXT_MAIN).pack(anchor="center")
        ctk.CTkLabel(title_frame, text=self.t("sidebar_subtitle"), font=("Segoe UI", 12), text_color=TEXT_MUTED).pack(anchor="center")
        ctk.CTkFrame(self.sidebar, height=2, fg_color=TEXT_MUTED).pack(fill="x", padx=30, pady=(0, 20))

        # Nav
        self.nav_btn(self.t("btn_home"), lambda: self.switch_view("home"), primary=(self.current_view == "home"))
        self.nav_btn(self.t("btn_adms"), lambda: self.switch_view("adms"), primary=(self.current_view == "adms"))
        self.nav_btn(self.t("btn_logs_view"), lambda: self.switch_view("logs"), primary=(self.current_view == "logs"))
        self.nav_btn(self.t("btn_sync_queue"), lambda: self.switch_view("sync_queue"), primary=(self.current_view == "sync_queue"))
        
        ctk.CTkFrame(self.sidebar, height=1, fg_color=TEXT_MUTED).pack(fill="x", padx=30, pady=10)
        
        if self.current_view == "home":
            self.nav_btn(self.t("btn_open"), self.open_site)
            self.nav_btn(self.t("btn_backup"), self.backup_db)
            self.nav_btn(self.t("btn_port"), lambda: self.change_port('main'))
            
        elif self.current_view == "adms":
            self.nav_btn(self.t("btn_port"), lambda: self.change_port('adms'))
            self.nav_btn(self.t("btn_oracle"), self.open_oracle_settings)

        self.nav_btn(self.t("btn_lang"), self.toggle_language)
        ctk.CTkFrame(self.sidebar, fg_color="transparent").pack(expand=True, fill="y")
        self.nav_btn(self.t("btn_hide"), self.hide_to_tray)
        self.nav_btn(self.t("btn_exit"), self.exit_app, danger=True)
        
        ctk.CTkLabel(self.sidebar, text=f"v{CURRENT_VERSION} | {self.local_ip}", font=("Segoe UI", 10), text_color=TEXT_MUTED).pack(side="bottom", pady=10)

        # --- Main Content ---
        self.content = ctk.CTkFrame(self.root, fg_color="transparent")
        self.content.pack(side=pack_side, fill="both", expand=True, padx=30, pady=30)
        
        if self.current_view == "home":
            self.setup_home_view()
        elif self.current_view == "adms":
            self.setup_adms_view()
        elif self.current_view == "logs":
            self.setup_logs_view()
        elif self.current_view == "sync_queue":
            self.setup_sync_queue_view()
            
    def setup_home_view(self):
        # Header
        side = "right" if self.lang == "ar" else "left"
        anchor = "ne" if self.lang == "ar" else "nw"
        ctk.CTkLabel(self.content, text=self.t("hero_title"), font=("Segoe UI", 32, "bold"), text_color=TEXT_MAIN).pack(side="top", anchor=anchor, pady=(0, 20))
        
        # Status Card
        self.status_card_frame = ctk.CTkFrame(self.content, fg_color=BG_CARD, corner_radius=15)
        self.status_card_frame.pack(fill="x", pady=10)
        
        self.status_indicator = ctk.CTkLabel(self.status_card_frame, text="●", font=("Arial", 64), text_color=WARNING)
        self.status_indicator.pack(side="left", padx=(30, 20), pady=30)
        
        status_text_frame = ctk.CTkFrame(self.status_card_frame, fg_color="transparent")
        status_text_frame.pack(side="left", fill="y", pady=20)
        
        self.status_title = ctk.CTkLabel(status_text_frame, text=self.t("status_starting"), font=("Segoe UI", 24, "bold"), text_color=TEXT_MAIN, anchor="w")
        self.status_title.pack(anchor="w")
        self.status_desc = ctk.CTkLabel(status_text_frame, text=self.t("status_starting_desc"), font=("Segoe UI", 14), text_color=TEXT_MUTED, anchor="w")
        self.status_desc.pack(anchor="w")
        
        # Grid
        grid_frame = ctk.CTkFrame(self.content, fg_color="transparent")
        grid_frame.pack(fill="both", pady=20)
        grid_frame.grid_columnconfigure(0, weight=1)
        grid_frame.grid_columnconfigure(1, weight=1)
        grid_frame.grid_columnconfigure(2, weight=1)
        
        self.info_card(grid_frame, 0, 0, self.t("label_port"), str(self.port), "🔌")
        self.info_card(grid_frame, 0, 1, self.t("label_ip"), self.local_ip, "🌐")
        self.info_card(grid_frame, 0, 2, self.t("label_public_ip"), self.public_ip, "🌍")

    def setup_adms_view(self):
        side = "right" if self.lang == "ar" else "left"
        anchor = "ne" if self.lang == "ar" else "nw"
        ctk.CTkLabel(self.content, text=self.t("adms_title"), font=("Segoe UI", 32, "bold"), text_color=TEXT_MAIN).pack(side="top", anchor=anchor, pady=(0, 20))
        
        # Status Card
        self.adms_card_frame = ctk.CTkFrame(self.content, fg_color=BG_CARD, corner_radius=15)
        self.adms_card_frame.pack(fill="x", pady=10)
        
        self.adms_status_indicator = ctk.CTkLabel(self.adms_card_frame, text="●", font=("Arial", 64), text_color=DANGER)
        self.adms_status_indicator.pack(side="left", padx=(30, 20), pady=30)
        
        status_text_frame = ctk.CTkFrame(self.adms_card_frame, fg_color="transparent")
        status_text_frame.pack(side="left", fill="y", pady=20)
        
        self.adms_status_title = ctk.CTkLabel(status_text_frame, text=self.t("status_stopped"), font=("Segoe UI", 24, "bold"), text_color=TEXT_MAIN, anchor="w")
        self.adms_status_title.pack(anchor="w")
        self.adms_status_desc = ctk.CTkLabel(status_text_frame, text=self.t("status_stopped_desc"), font=("Segoe UI", 14), text_color=TEXT_MUTED, anchor="w")
        self.adms_status_desc.pack(anchor="w")
        
        # Grid
        grid_frame = ctk.CTkFrame(self.content, fg_color="transparent")
        grid_frame.pack(fill="both", pady=20)
        grid_frame.grid_columnconfigure(0, weight=1)
        grid_frame.grid_columnconfigure(1, weight=1)
        
        self.info_card(grid_frame, 0, 0, self.t("label_adms_port"), str(self.adms_port), "📡")
        self.info_card(grid_frame, 0, 1, self.t("label_ip"), self.local_ip, "🌐")
        
        # Update UI if running
        if self.adms_running:
            self.update_adms_status_ui(True)

    def setup_logs_view(self):
        side = "right" if self.lang == "ar" else "left"
        anchor = "ne" if self.lang == "ar" else "nw"
        ctk.CTkLabel(self.content, text=self.t("logs_title"), font=("Segoe UI", 32, "bold"), text_color=TEXT_MAIN).pack(side="top", anchor=anchor, pady=(0, 20))
        
        # Logs Textbox
        self.log_textbox = ctk.CTkTextbox(self.content, corner_radius=10, fg_color=BG_SIDEBAR, text_color="#d1d5db")
        self.log_textbox.pack(fill="both", expand=True)
        
        # Populate history
        for msg in self.log_history:
            self.log_textbox.insert("end", msg)
        self.log_textbox.see("end")

    def setup_sync_queue_view(self):
        side = "right" if self.lang == "ar" else "left"
        anchor = "ne" if self.lang == "ar" else "nw"
        header_frame = ctk.CTkFrame(self.content, fg_color="transparent")
        header_frame.pack(side="top", fill="x", pady=(0, 20))
        
        ctk.CTkLabel(header_frame, text=self.t("sync_queue_title"), font=("Segoe UI", 32, "bold"), text_color=TEXT_MAIN).pack(side=side, anchor=anchor)
        
        self.btn_refresh_queue = ctk.CTkButton(header_frame, text="🔄", width=40, font=("Arial", 20), fg_color=BG_CARD, command=self.refresh_sync_queue)
        self.btn_refresh_queue.pack(side="left" if self.lang == "ar" else "right", padx=10)

        # Stats Card
        self.sync_stats_label = ctk.CTkLabel(self.content, text="Total PENDING: 0", font=("Segoe UI", 16, "bold"), text_color=WARNING)
        self.sync_stats_label.pack(pady=(0, 10), anchor=anchor)

        # Container for the list
        self.queue_container = ctk.CTkScrollableFrame(self.content, fg_color=BG_CARD, corner_radius=12)
        self.queue_container.pack(fill="both", expand=True)
        
        self.refresh_sync_queue()

    def refresh_sync_queue(self):
        if not hasattr(self, 'queue_container') or not self.queue_container.winfo_exists(): return
        
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("SELECT user_id, check_time, check_type, status FROM oracle_sync_queue WHERE status = 'PENDING' ORDER BY id DESC LIMIT 50")
            rows = cur.fetchall()
            
            # Update Count
            cur.execute("SELECT count(*) FROM oracle_sync_queue WHERE status = 'PENDING'")
            count = cur.fetchone()[0]
            self.sync_stats_label.configure(text=f"Total PENDING: {count}")
            
            # Clear container
            for widget in self.queue_container.winfo_children():
                widget.destroy()
            
            if not rows:
                ctk.CTkLabel(self.queue_container, text="No pending records." if self.lang == 'en' else "لا توجد حركات منتظرة.", font=("Segoe UI", 16), text_color=TEXT_MUTED).pack(pady=50)
            else:
                # Header row
                h_frame = ctk.CTkFrame(self.queue_container, fg_color="transparent")
                h_frame.pack(fill="x", pady=5)
                ctk.CTkLabel(h_frame, text="User ID", font=("Segoe UI", 12, "bold"), width=100).pack(side="left", padx=10)
                ctk.CTkLabel(h_frame, text="Time", font=("Segoe UI", 12, "bold"), width=150).pack(side="left", padx=10)
                ctk.CTkLabel(h_frame, text="Type", font=("Segoe UI", 12, "bold"), width=50).pack(side="left", padx=10)
                ctk.CTkLabel(h_frame, text="Status", font=("Segoe UI", 12, "bold")).pack(side="left", padx=10)
                
                for row in rows:
                    r_frame = ctk.CTkFrame(self.queue_container, fg_color=BG_DARK, corner_radius=5)
                    r_frame.pack(fill="x", pady=2, padx=5)
                    ctk.CTkLabel(r_frame, text=str(row['user_id']), width=100).pack(side="left", padx=10)
                    ctk.CTkLabel(r_frame, text=str(row['check_time']), width=150).pack(side="left", padx=10)
                    ctk.CTkLabel(r_frame, text=str(row['check_type']), width=50).pack(side="left", padx=10)
                    ctk.CTkLabel(r_frame, text=str(row['status']), text_color=WARNING).pack(side="left", padx=10)
            
            pass # conn.close() removed to prevent leak in Flask g
        except Exception as e:
            self.log(f"Failed to refresh sync queue: {e}", "ERROR")
        
        # Auto-refresh every 5 seconds if still on this view
        if self.current_view == "sync_queue":
            self.root.after(5000, self.refresh_sync_queue)

    def nav_btn(self, text, cmd, danger=False, primary=False):
        color = "transparent"
        hover = "#334155"
        text_col = TEXT_MAIN
        if danger:
            color = DANGER_DARK
            hover = DANGER
            text_col = "#fca5a5"
        elif primary:
            color = PRIMARY
            hover = PRIMARY_HOVER
            text_col = "#ffffff"

        btn = ctk.CTkButton(self.sidebar, text=text, anchor="w" if self.lang == "en" else "e", font=("Segoe UI", 14, "bold"), fg_color=color, hover_color=hover, text_color=text_col, height=45, corner_radius=8, command=cmd)
        if danger: btn.pack(fill="x", padx=20, pady=(20, 10), side="bottom")
        else: btn.pack(fill="x", padx=20, pady=5)

    def info_card(self, parent, row, col, title, value, icon):
        frame = ctk.CTkFrame(parent, fg_color=BG_CARD, corner_radius=12)
        frame.grid(row=row, column=col, padx=10, pady=10, sticky="nsew")
        ctk.CTkLabel(frame, text=icon, font=("Segoe UI", 32)).pack(side="left", padx=20)
        info_frame = ctk.CTkFrame(frame, fg_color="transparent")
        info_frame.pack(side="left", fill="y", pady=15)
        ctk.CTkLabel(info_frame, text=title, font=("Segoe UI", 14), text_color=TEXT_MUTED).pack(anchor="w")
        ctk.CTkLabel(info_frame, text=value, font=("Segoe UI", 18, "bold"), text_color=TEXT_MAIN).pack(anchor="w")

    def log(self, message, level="INFO"):
        try:
            timestamp = time.strftime("%H:%M:%S")
            log_line = f"[{timestamp}] [{level}] {message}\n"
            
            self.log_history.append(log_line)
            if len(self.log_history) > 1000:
                self.log_history.pop(0)
                
            if self.current_view == "logs" and hasattr(self, 'log_textbox') and self.log_textbox.winfo_exists():
                self.log_textbox.insert("end", log_line)
                self.log_textbox.see("end")
        except: pass

    def setup_oracle_logger_hook(self):
        """Hook into the oracle_db logger to stream messages to the UI textbox"""
        import logging
        try:
            logger = logging.getLogger('oracle_sync')
            
            # Create a custom handler that writes to our log() function
            class UILogHandler(logging.Handler):
                def __init__(self, ui_log_fn):
                    super().__init__()
                    self.ui_log_fn = ui_log_fn
                
                def emit(self, record):
                    msg = self.format(record)
                    # We pass the formatted message directly, so we use empty level 
                    # since our UI log function prefixes manually, or we let UI log function handle it.
                    # Since our log function adds timestamp and level, let's just pass the record info.
                    self.ui_log_fn(record.getMessage(), record.levelname)
            
            ui_handler = UILogHandler(self.log)
            ui_handler.setLevel(logging.INFO)
            logger.addHandler(ui_handler)
        except Exception as e:
            self.log(f"Failed to hook Oracle logger: {e}", "ERROR")

    # ========== ACTIONS ==========
    def startup(self):
        if self.server_running: return
        self.log(self.t("log_init"), "INFO")
        def cb(ok, err=None):
            if ok:
                self.server_running = True
                if self.current_view == "home": self.root.after(0, lambda: self.update_status_ui(True))
                self.log(self.t("log_success").format(self.port), "SUCCESS")
            else:
                self.server_running = False
                if self.current_view == "home": self.root.after(0, lambda: self.update_status_ui(False, err))
                self.log(self.t("log_fail").format(err), "ERROR")
        threading.Timer(0.5, lambda: start_server(self.port, cb)).start()
        threading.Thread(target=self.run_update_check, daemon=True).start()

    def startup_adms(self):
        if self.adms_running: return
        def cb(ok, err=None):
            if ok:
                self.adms_running = True
                if self.current_view == "adms": self.root.after(0, lambda: self.update_adms_status_ui(True))
                self.log(self.t("log_adms_success").format(self.adms_port), "SUCCESS")
            else:
                self.adms_running = False
                if self.current_view == "adms": self.root.after(0, lambda: self.update_adms_status_ui(False, err))
                self.log(self.t("log_fail").format(err), "ERROR")
        threading.Timer(0.8, lambda: start_adms_process(self.adms_port, cb)).start()

    def run_update_check(self):
        self.log(self.t("status_starting").replace("...", "") + " (Update Check)...", "INFO")
        is_avail, url, notes, mandatory = check_for_updates()
        if is_avail: self.root.after(0, lambda: self.show_update_dialog(url, notes, mandatory))

    def show_update_dialog(self, url, notes, mandatory):
        title = "تحديث جديد متوفر" if self.lang == "ar" else "New Update Available"
        msg = f"يوجد إصدار جديد. هل تريد التحديث الآن؟\n\n{notes}"
        if mandatory: msg = f"تحديث إجباري مطلوب.\n\n{notes}"
        
        dialog = ctk.CTkToplevel(self.root)
        dialog.title(title)
        dialog.geometry("400x250")
        dialog.attributes("-topmost", True)
        ctk.CTkLabel(dialog, text=title, font=("Segoe UI", 18, "bold")).pack(pady=10)
        ctk.CTkLabel(dialog, text=msg, wraplength=350).pack(pady=10)
        
        def do_update():
            self.log("Starting update download...", "WARN")
            dialog.destroy()
            if download_and_install_update(url): self.root.destroy()
            else: self.log("Update failed.", "ERROR")

        ctk.CTkButton(dialog, text="تحديث الآن" if self.lang=="ar" else "Update Now", command=do_update).pack(pady=10)
        if not mandatory: ctk.CTkButton(dialog, text="لاحقاً" if self.lang=="ar" else "Later", fg_color="transparent", border_width=1, command=dialog.destroy).pack(pady=5)
        else: dialog.protocol("WM_DELETE_WINDOW", lambda: None)

    def update_status_ui(self, running, err=None):
        try:
            if not self.current_view == "home": return
            if running:
                self.status_indicator.configure(text_color=SUCCESS)
                self.status_title.configure(text=self.t("status_running"))
                self.status_desc.configure(text=self.t("status_running_desc").format(self.port))
            else:
                self.status_indicator.configure(text_color=DANGER)
                self.status_title.configure(text=self.t("status_error"))
                self.status_desc.configure(text=str(err))
        except: pass

    def update_adms_status_ui(self, running, err=None):
        try:
            if not self.current_view == "adms": return
            if running:
                self.adms_status_indicator.configure(text_color=SUCCESS)
                self.adms_status_title.configure(text=self.t("status_running"))
                self.adms_status_desc.configure(text=self.t("status_running_desc").format(self.adms_port))
            else:
                self.adms_status_indicator.configure(text_color=DANGER)
                self.adms_status_title.configure(text=self.t("status_error"))
                self.adms_status_desc.configure(text=str(err))
        except: pass

    def open_site(self):
        url = f"http://{DEFAULT_HOST}:{self.port}"
        self.log(self.t("log_browser").format(url))
        webbrowser.open(url)

    def backup_db(self):
        if not os.path.exists(DB_PATH): return
        path = filedialog.asksaveasfilename(defaultextension=".db", filetypes=[("Database", "*.db")], title=self.t("dialog_backup_title"))
        if path:
            try:
                shutil.copy2(DB_PATH, path)
                self.log(self.t("log_backup_success").format(path), "SUCCESS")
            except Exception as e:
                self.log(self.t("log_backup_fail").format(e), "ERROR")

    def change_port(self, type='main'):
        dialog = ctk.CTkInputDialog(title=self.t("dialog_port_title"), text=self.t("dialog_port_text"))
        val = dialog.get_input()
        if val and val.isdigit():
            if type == 'main':
                set_setting("port", int(val))
                self.log(self.t("msg_port_changed").format(val), "WARN")
                if self.server_running and self.current_view == 'home': self.status_desc.configure(text=self.t("msg_port_changed").format(val))
            else:
                set_setting("adms_port", int(val))
                self.log("ADMS " + self.t("msg_port_changed").format(val), "WARN")
                if self.adms_running and self.current_view == 'adms': self.adms_status_desc.configure(text=self.t("msg_port_changed").format(val))

    def open_oracle_settings(self):
        title = "إعدادات قاعدة بيانات أوراكل" if self.lang == "ar" else "Oracle Database Settings"
        
        dialog = ctk.CTkToplevel(self.root)
        dialog.title(title)
        dialog.geometry("450x550")
        dialog.attributes("-topmost", True)
        
        ctk.CTkLabel(dialog, text=title, font=("Segoe UI", 18, "bold")).pack(pady=10)
        
        # Load existing settings
        curr_host = get_setting("oracle_host", "localhost")
        curr_port = get_setting("oracle_port", "1521")
        curr_service = get_setting("oracle_service", "XEPDB1")
        curr_user = get_setting("oracle_user", "HR")
        curr_pass = get_setting("oracle_password", "password")
        curr_table = get_setting("oracle_table", "ATTENDANCE_LOGS")
        
        base_dir = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
        default_instant = os.path.join(base_dir, "instantclient")
        curr_lib_dir = get_setting("oracle_lib_dir", default_instant) # Path to instant client

        # Inputs
        frame = ctk.CTkFrame(dialog, fg_color="transparent")
        frame.pack(fill="both", expand=True, padx=20, pady=10)
        
        def add_input(label_text, default_val, is_password=False):
            ctk.CTkLabel(frame, text=label_text, anchor="w").pack(fill="x")
            entry = ctk.CTkEntry(frame, show="*" if is_password else "")
            entry.insert(0, default_val)
            entry.pack(fill="x", pady=(0, 10))
            return entry

        host_lbl = "الخادم (Host):" if self.lang == "ar" else "Host:"
        port_lbl = "المنفذ (Port):" if self.lang == "ar" else "Port:"
        srv_lbl = "اسم الخدمة (Service Name):" if self.lang == "ar" else "Service Name:"
        user_lbl = "المستخدم (User):" if self.lang == "ar" else "User:"
        pass_lbl = "كلمة المرور (Password):" if self.lang == "ar" else "Password:"
        table_lbl = "اسم الجدول (Table Name):" if self.lang == "ar" else "Table Name:"
        
        entry_host = add_input(host_lbl, curr_host)
        entry_port = add_input(port_lbl, curr_port)
        entry_service = add_input(srv_lbl, curr_service)
        entry_user = add_input(user_lbl, curr_user)
        entry_pass = add_input(pass_lbl, curr_pass, True)
        entry_table = add_input(table_lbl, curr_table)
        
        # Add Instant Client path selector
        instant_lbl = "مجلد Instant Client (اختياري للصيغة السريعة):" if self.lang == "ar" else "Instant Client Dir (Optional for Thick mode):"
        ctk.CTkLabel(frame, text=instant_lbl, anchor="w").pack(fill="x", pady=(10, 0))
        
        lib_dir_frame = ctk.CTkFrame(frame, fg_color="transparent")
        lib_dir_frame.pack(fill="x", pady=(0, 10))
        
        entry_lib = ctk.CTkEntry(lib_dir_frame)
        entry_lib.insert(0, curr_lib_dir)
        entry_lib.pack(side="left", fill="x", expand=True, padx=(0, 5))
        
        def browse_lib_dir():
            dir_path = filedialog.askdirectory(title="Select Oracle Instant Client Directory")
            if dir_path:
                entry_lib.delete(0, 'end')
                entry_lib.insert(0, dir_path)
                
        browse_btn_text = "تصفح" if self.lang == "ar" else "Browse"
        ctk.CTkButton(lib_dir_frame, text=browse_btn_text, width=60, command=browse_lib_dir).pack(side="right")

        def save_oracle_settings():
            set_setting("oracle_host", entry_host.get())
            set_setting("oracle_port", entry_port.get())
            set_setting("oracle_service", entry_service.get())
            set_setting("oracle_user", entry_user.get())
            set_setting("oracle_password", entry_pass.get())
            set_setting("oracle_table", entry_table.get())
            set_setting("oracle_lib_dir", entry_lib.get())
            self.log("تم حفظ إعدادات أوراكل" if self.lang == "ar" else "Oracle settings saved", "SUCCESS")
            dialog.destroy()
            
        def test_oracle_connection():
            self.log("جاري اختبار الاتصال بأوراكل..." if self.lang == "ar" else "Testing Oracle connection...", "INFO")
            def _test():
                try:
                    import oracledb
                    try:
                        lib_dir = entry_lib.get().strip()
                        if lib_dir:
                            import os
                            lib_dir_abs = os.path.abspath(lib_dir)
                            oracledb.init_oracle_client(lib_dir=lib_dir_abs)
                        else:
                            oracledb.init_oracle_client()
                    except Exception as client_err:
                        self.log(f"ملاحظة (Thick Mode): {client_err}", "WARN")
                        
                    dsn = f"{entry_host.get()}:{entry_port.get()}/{entry_service.get()}"
                    conn = oracledb.connect(user=entry_user.get(), password=entry_pass.get(), dsn=dsn)
                    pass # conn.close() removed to prevent leak in Flask g
                    self.log("نجاح: تم الاتصال بقاعدة بيانات أوراكل بنجاح" if self.lang == "ar" else "Success: Successfully connected to Oracle", "SUCCESS")
                except Exception as e:
                    self.log(f"فشل الاتصال: {e}" if self.lang == "ar" else f"Connection Failed: {e}", "ERROR")

            import threading
            threading.Thread(target=_test, daemon=True).start()
            
        save_lbl = "حفظ" if self.lang == "ar" else "Save"
        cancel_lbl = "إلغاء" if self.lang == "ar" else "Cancel"
        test_lbl = "تجربة الاتصال" if self.lang == "ar" else "Test Connection"
        
        btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_frame.pack(fill="x", padx=20, pady=20)
        
        ctk.CTkButton(btn_frame, text=save_lbl, fg_color=PRIMARY, command=save_oracle_settings).pack(side="left", expand=True, padx=5)
        ctk.CTkButton(btn_frame, text=test_lbl, fg_color="#475569", command=test_oracle_connection).pack(side="left", expand=True, padx=5)
        ctk.CTkButton(btn_frame, text=cancel_lbl, fg_color="transparent", border_width=1, command=dialog.destroy).pack(side="right", expand=True, padx=5)

    def hide_to_tray(self):
        self.root.withdraw()
        self.log(self.t("log_minimized"))
        threading.Thread(target=self.tray).start()

    def tray(self):
        image = Image.new("RGB", (64, 64), (59, 130, 246)) 
        menu = (item(self.t("tray_restore"), self.restore), item(self.t("tray_open"), lambda: self.open_site()))
        self.tray_icon = pystray.Icon("HR System", image, "HR System Launcher", menu)
        self.tray_icon.run()

    def restore(self, icon=None, item=None):
        self.tray_icon.stop()
        self.root.after(0, self.root.deiconify)
        self.log(self.t("log_restored"))

    def exit_app(self):
        self.log("إغلاق النظام غير مسموح لحماية المزامنة، سيستمر العمل في الخلفية", "WARN")
        if self.root.winfo_viewable():
            self.hide_to_tray()

    def run(self):
        self.root.mainloop()

# ================== START ==================
if __name__ == "__main__":
    os.makedirs(DATA_DIR, exist_ok=True)
    HRLauncher().run()
