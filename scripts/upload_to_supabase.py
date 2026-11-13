import json
import os
import pandas as pd
from supabase import create_client

CONFIG_PATH = os.path.join(
    os.path.dirname(__file__), "..", "config", "settings.json"
)


def load_config():
  if not os.path.exists(CONFIG_PATH):
    raise FileNotFoundError(f"Config file not found at: {CONFIG_PATH}")
  with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    return json.load(f)


def upload_quartile_data(file_path):
  """ฟังก์ชันอ่านไฟล์ Excel/CSV ข้อมูลจัดอันดับวารสาร และอัปโหลดขึ้น Supabase แบบอัตโนมัติ (Upsert)"""
  print(f"INFO: Loading data from {file_path}...")

  try:
    # รองรับทั้งไฟล์ .xlsx และ .csv
    if file_path.endswith(".csv"):
      df = pd.read_csv(file_path)
    else:
      df = pd.read_excel(file_path)

    # ตรวจสอบคอลัมน์จำเป็นพื้นฐาน
    required_cols = ["issn", "journal_name", "percentile", "quartile", "year"]
    for col in required_cols:
      if col not in df.columns:
        print(f"ERROR: Missing required column '{col}' in data file.")
        return False

    # แปลงข้อมูล DataFrame เป็น List of Dictionaries
    records = df.to_dict(orient="records")

    # โหลดค่าเชื่อมต่อ Supabase จาก settings.json
    config = load_config()
    supabase_url = config.get("supabase_url")
    supabase_key = config.get("supabase_key")

    if not supabase_url or not supabase_key:
      print("ERROR: Supabase URL or Key not found in config/settings.json")
      return False

    supabase = create_client(supabase_url, supabase_key)

    print(f"INFO: Uploading {len(records)} records to Supabase (journal_metrics)...")

    # ทำการ Upsert โดยอ้างอิงเงื่อนไขจาก issn และ year (ป้องกันข้อมูลซ้ำและรองรับการอัปเดตปีใหม่)
    response = (
        supabase.table("journal_metrics")
        .upsert(records, on_conflict="issn,year")
        .execute()
    )

    print("SUCCESS: Quartile and Percentile data successfully synced to Supabase!")
    return True

  except Exception as e:
    print(f"ERROR: Failed to upload data to Supabase: {e}")
    return False


if __name__ == "__main__":
  # ตัวอย่างการเรียกใช้งานผ่าน Terminal
  # (คุณสามารถเปลี่ยนชื่อไฟล์ path ให้ตรงกับไฟล์ข้อมูลจัดอันดับวารสารของคุณได้เลยครับ)
  target_file = os.path.join(
      os.path.dirname(__file__), "..", "reference_data", "journal_ranking_data.xlsx"
  )
  
  if os.path.exists(target_file):
    upload_quartile_data(target_file)
  else:
    print(f"INFO: Please place your ranking file at {target_file} or specify path.")