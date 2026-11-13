import calendar
from datetime import datetime, timedelta
import os
import queue
import threading
import tkinter as tk
from tkinter import messagebox, scrolledtext
import ttkbootstrap as ttk
from ttkbootstrap.constants import *
from version import APP_VERSION
import requests
import subprocess
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), "scripts"))
from run_pipeline import run_full_pipeline
from auto_sync_metrics import download_and_sync_metrics

class ScopusSyncApp(ttk.Window):
  def __init__(self):
    super(ScopusSyncApp, self).__init__(themename="litera")
    self.title(f"Scopus Sync - Research Data Pipeline ({APP_VERSION})")
    self.geometry("950x730")
    self.minsize(850, 600)

    self.log_queue = queue.Queue()
    self.is_running = False

    self.create_widgets()
    self.check_log_queue()
    
    # เริ่มต้นระบบนาฬิกาและ Countdown เรียลไทม์
    self.update_clock_and_countdown()

    # --- เพิ่มระบบตรวจสอบและอัปเดตอัตโนมัติเมื่อเปิดโปรแกรม ---
    self.check_and_auto_update()

  def check_and_auto_update(self):
    """ฟังก์ชันเช็คอัปเดตจาก GitHub Releases และสั่งรัน updater.exe หากมีเวอร์ชันใหม่"""
    def _run_update():
      try:
        repo_url = "https://api.github.com/repos/rsnmcra/ScopusSync/releases/latest"
        response = requests.get(repo_url, timeout=3)
        if response.status_code == 200:
          latest_data = response.json()
          latest_version = latest_data.get("tag_name")
          
          if latest_version and latest_version != APP_VERSION:
            assets = latest_data.get("assets", [])
            download_url = next((asset["browser_download_url"] for asset in assets if asset["name"].endswith(".exe")), None)
            
            if download_url:
              current_exe = sys.executable if getattr(sys, 'frozen', False) else os.path.abspath(__file__)
              updater_path = os.path.join(os.path.dirname(current_exe), "updater.exe")
              
              if os.path.exists(updater_path):
                self.write_log(f"INFO: Found new version {latest_version}. Updating automatically...")
                subprocess.Popen([updater_path, current_exe, download_url])
                sys.exit()
      except Exception:
        pass # ข้ามหากไม่มีอินเทอร์เน็ตหรือติดปัญหาการเชื่อมต่อ

    threading.Thread(target=_run_update, daemon=True).start()

  def create_widgets(self):
    header_frame = ttk.Frame(self, padding=20)
    header_frame.pack(fill=X)

    title_label = ttk.Label(
        header_frame,
        text="Scopus Sync Dashboard",
        font=("Helvetica", 18, "bold"),
    )
    title_label.pack(side=LEFT)

    # ปุ่มคำสั่งด้านขวาบน (Check & Sync Metrics และ Run Sync Pipeline)
    btn_frame = ttk.Frame(header_frame)
    btn_frame.pack(side=RIGHT)

    self.sync_metrics_btn = ttk.Button(
        btn_frame,
        text="Check & Sync Metrics",
        command=self.start_metrics_sync_thread,
        bootstyle="info-outline",
        width=20,
    )
    self.sync_metrics_btn.pack(side=LEFT, padx=(0, 10))

    self.run_btn = ttk.Button(
        btn_frame,
        text="Run Sync Pipeline",
        command=self.start_pipeline_thread,
        bootstyle="success-outline",
        width=18,
    )
    self.run_btn.pack(side=LEFT)

    # --- ส่วนเพิ่มใหม่: กรอบแสดงเวลาปัจจุบัน และ Countdown แบบเรียลไทม์ ---
    schedule_frame = ttk.Labelframe(self, text=" Auto-Sync & Schedule Status ", padding=15, bootstyle="success")
    schedule_frame.pack(fill=X, padx=20, pady=(0, 10))

    self.time_label = ttk.Label(
        schedule_frame,
        text="Current Time: --",
        font=("Helvetica", 11, "bold"),
        bootstyle="inverse-success"
    )
    self.time_label.pack(anchor=W, pady=(0, 5))

    self.countdown_label = ttk.Label(
        schedule_frame,
        text="Next Auto-Sync Countdown: Calculating...",
        font=("Helvetica", 10),
    )
    self.countdown_label.pack(anchor=W)
    # -------------------------------------------------------------

    status_frame = ttk.Frame(self, padding=(20, 0, 20, 10))
    status_frame.pack(fill=X)

    self.status_label = ttk.Label(
        status_frame,
        text="Status: Ready to synchronize data.",
        font=("Helvetica", 10),
        bootstyle="secondary",
    )
    self.status_label.pack(side=LEFT)

    console_frame = ttk.Labelframe(
        self, text=" Live Log Console ", padding=15, bootstyle="info"
    )
    console_frame.pack(fill=BOTH, expand=True, padx=20, pady=(0, 20))

    self.log_area = scrolledtext.ScrolledText(
        console_frame,
        wrap=tk.WORD,
        font=("Consolas", 10),
        bg="#1e1e1e",
        fg="#d4d4d4",
        insertbackground="white",
    )
    self.log_area.pack(fill=BOTH, expand=True)
    self.log_area.config(state=tk.DISABLED)

    self.write_log(f"INFO: [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Application initialized successfully.")
    self.write_log("INFO: Smart detection, auto-sync schedule, and modules loaded.")

  def get_last_day_of_current_month(self, dt):
    """คำนวณหาวันและเวลาสุดท้ายของเดือน (ตั้งเป้าหมายเวลา 14:00:00)"""
    last_day = calendar.monthrange(dt.year, dt.month)[1]
    return datetime(dt.year, dt.month, last_day, 14, 0, 0)

  def update_clock_and_countdown(self):
    """อัปเดตนาฬิกาปัจจุบัน, คำนวณ Countdown สู่รอบวันสิ้นเดือนเวลา 14:00:00 และเช็คเงื่อนไข Auto-Sync"""
    now = datetime.now()
    
    # หาวันสิ้นเดือนของรอบปัจจุบัน (เป้าหมาย 14:00:00)
    target_date = self.get_last_day_of_current_month(now)
    if now > target_date:
        next_month = now.replace(day=1) + timedelta(days=32)
        target_date = self.get_last_day_of_current_month(next_month)

    remaining = target_date - now
    days = remaining.days
    hours, remainder = divmod(remaining.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    self.time_label.config(text=f"Current Time: {now.strftime('%Y-%m-%d %H:%M:%S')}")
    self.countdown_label.config(
        text=f"Target Auto-Sync ({target_date.strftime('%Y-%m-%d %H:%M:%S')}): "
             f"Remaining {days} Days {hours} Hours {minutes} Minutes {seconds} Seconds"
    )

    if now.day == calendar.monthrange(now.year, now.month)[1] and now.hour == 14 and now.minute == 0 and not self.is_running:
        self.write_log(f"INFO: [{now.strftime('%Y-%m-%d %H:%M:%S' )}] Auto-sync triggered at 14:00 for the end of the month.")
        self.start_pipeline_thread()

    self.after(1000, self.update_clock_and_countdown)

  def write_log(self, message):
    self.log_queue.put(message + "\n")

  def check_log_queue(self):
    while not self.log_queue.empty():
      msg = self.log_queue.get()
      self.log_area.config(state=tk.NORMAL)
      self.log_area.insert(tk.END, msg)
      self.log_area.see(tk.END)
      self.log_area.config(state=tk.DISABLED)
    self.after(100, self.check_log_queue)

  def start_pipeline_thread(self):
    if self.is_running:
      return
    self.is_running = True
    self.run_btn.config(state=tk.DISABLED)
    self.sync_metrics_btn.config(state=tk.DISABLED)
    self.status_label.config(text="Status: Synchronizing research data in progress...", bootstyle="warning")
    threading.Thread(target=self.run_pipeline_task, daemon=True).start()

  def run_pipeline_task(self):
    try:
      success = run_full_pipeline(log_callback=self.write_log)
      if success:
        self.status_label.config(text="Status: Synchronization completed successfully.", bootstyle="success")
      else:
        self.status_label.config(text="Status: Synchronization failed. Check logs.", bootstyle="danger")
    except Exception as e:
      self.write_log(f"ERROR: Exception during pipeline execution: {e}")
      self.status_label.config(text="Status: Error encountered.", bootstyle="danger")
    finally:
      self.is_running = False
      self.run_btn.config(state=tk.NORMAL)
      self.sync_metrics_btn.config(state=tk.NORMAL)

  def start_metrics_sync_thread(self):
    if self.is_running:
      return
    self.is_running = True
    self.run_btn.config(state=tk.DISABLED)
    self.sync_metrics_btn.config(state=tk.DISABLED)
    self.status_label.config(text="Status: Checking and updating journal metrics...", bootstyle="warning")
    threading.Thread(target=self.run_metrics_sync_task, daemon=True).start()

  def run_metrics_sync_task(self):
    try:
      success = download_and_sync_metrics(log_callback=self.write_log)
      if success:
        self.status_label.config(text="Status: Journal metrics updated successfully.", bootstyle="success")
      else:
        self.status_label.config(text="Status: Metrics update failed. Check logs.", bootstyle="danger")
    except Exception as e:
      self.write_log(f"ERROR: Exception during metrics sync: {e}")
      self.status_label.config(text="Status: Error encountered during metrics sync.", bootstyle="danger")
    finally:
      self.is_running = False
      self.run_btn.config(state=tk.NORMAL)
      self.sync_metrics_btn.config(state=tk.NORMAL)

if __name__ == "__main__":
  app = ScopusSyncApp()
  app.mainloop()