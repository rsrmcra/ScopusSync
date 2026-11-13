import sys
import time
import requests
import os
import subprocess

def update_app(exe_path, download_url):
    time.sleep(2) # รอให้โปรแกรมหลักปิดตัวสนิท
    try:
        # ดาวน์โหลดไฟล์เวอร์ชันใหม่มาทับไฟล์เดิม
        response = requests.get(download_url, stream=True)
        if response.status_code == 200:
            with open(exe_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            # เปิดโปรแกรมหลักตัวใหม่ขึ้นมาทันที
            subprocess.Popen([exe_path])
    except Exception as e:
        print(f"Update failed: {e}")

if __name__ == "__main__":
    if len(sys.argv) >= 3:
        update_app(sys.argv[1], sys.argv[2])