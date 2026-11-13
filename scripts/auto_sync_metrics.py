from datetime import datetime
import json
import os
import time
import requests
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

def clean_issn(value):
  if pd.isna(value):
    return ""
  text = str(value).strip().upper()
  if not text or text.lower() == "nan":
    return ""
  # ตัดเอาตัวแรกก่อนหากมีหลายตัวคั่นด้วย ช่องว่าง, คอมมา, หรือขีดทับ
  for sep in [" ", ",", "/"]:
    if sep in text:
      text = text.split(sep)[0].strip()
      break
  cleaned = text.replace("-", "").replace(" ", "")
  if len(cleaned) >= 8:
    return cleaned[:8]
  return ""

def percentile_to_quartile(p):
  try:
    val = float(p)
  except Exception:
    return ""
  if val >= 75: return "Q1"
  if val >= 50: return "Q2"
  if val >= 25: return "Q3"
  if val > 0: return "Q4"
  return "" # ถ้าเป็น 0 หรือค่าว่าง ให้เว้นว่างไว้ ไม่บังคับเป็น Q4

def parse_scopus_entry(entry):
    """ฟังก์ชันแกะค่า CiteScore, Percentile, Publisher และ Subject Area แบบสะอาดและถูกต้อง"""
    citescore_val = 0.0
    percentile = 0.0
    subject_areas = []
    ranks_list = []
    publisher = ""

    def to_float(val):
        try:
            return float(val)
        except:
            return 0.0

    # 1. ดึง Publisher จากคีย์ที่เป็นไปได้ทั้งหมด
    for pub_key in ["prism:publisher", "dc:publisher", "publisher"]:
        val = entry.get(pub_key)
        if val and str(val).strip() not in ["None", "nan", ""]:
            publisher = str(val).strip()
            break
    
    if not publisher:
        # ค้นหาแบบกวาดหาทั่วก้อน JSON หากหาไม่เจอตามคีย์หลัก
        pub_candidates = find_values_by_keys(entry, ["publisher", "prism:publisher"])
        for p in pub_candidates:
            if p and isinstance(p, str) and len(p.strip()) > 1:
                publisher = p.strip()
                break

    # 2. ฟังก์ชันกวาดหาค่าจาก JSON
    def find_values_by_keys(obj, target_keys):
        results = []
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in target_keys and v not in [None, ""]:
                    results.append(v)
                results.extend(find_values_by_keys(v, target_keys))
        elif isinstance(obj, list):
            for item in obj:
                results.extend(find_values_by_keys(item, target_keys))
        return results

    # 3. ค้นหา CiteScore และ Percentile
    cs_candidates = find_values_by_keys(entry, ["citeScore", "citescore", "citeScoreValue", "value"])
    for val in cs_candidates:
        f_val = to_float(val)
        if f_val > 0:
            citescore_val = max(citescore_val, f_val)

    p_candidates = find_values_by_keys(entry, ["percentile", "citeScorePercentile", "citeScore-percentile"])
    for val in p_candidates:
        f_val = to_float(val)
        if f_val > 0:
            percentile = max(percentile, f_val)

    # 4. แกะชื่อ Subject Area ให้เป็นข้อความอ่านง่าย (ดึงค่าจากคีย์ '$' หรือ string ตรงๆ)
    def extract_subjects(obj):
        subjects = []
        if isinstance(obj, dict):
            # โครงสร้างของ Scopus มักเก็บชื่อจริงไว้ในคีย์ '$'
            if "$" in obj and isinstance(obj["$"], str):
                subjects.append(obj["$"].strip())
            for k, v in obj.items():
                if k.lower() in ["subjectarea", "subject-area", "subject"] and isinstance(v, str):
                    subjects.append(v.strip())
                subjects.extend(extract_subjects(v))
        elif isinstance(obj, list):
            for item in obj:
                subjects.extend(extract_subjects(item))
        return subjects

    raw_subjects = extract_subjects(entry)
    for s in raw_subjects:
        if s and s not in subject_areas and len(s) < 100:  # กรองเฉพาะข้อความสั้นที่เป็นชื่อสาขา
            subject_areas.append(s)

    # 5. ค้นหา Rank
    rank_candidates = find_values_by_keys(entry, ["rank", "subjectRank"])
    for r in rank_candidates:
        if r and str(r).strip() not in ranks_list:
            ranks_list.append(str(r).strip())

    return publisher, citescore_val, percentile, subject_areas, ranks_list

# เพิ่ม helper function ภายนอกเพื่อให้เรียกใช้งานได้
def find_values_by_keys(obj, target_keys):
    results = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in target_keys and v not in [None, ""]:
                results.append(v)
            results.extend(find_values_by_keys(v, target_keys))
    elif isinstance(obj, list):
        for item in obj:
            results.extend(find_values_by_keys(item, target_keys))
    return results

def download_and_sync_metrics(source_url=None, log_callback=print):
  """ดึงข้อมูลเมตริกวารสารและซิงค์ตรงเข้า Supabase โดยไม่ใช้ไฟล์ Excel ปลายทาง"""
  log_callback("INFO: Starting smart incremental metrics synchronization...")

  config = load_config()
  api_key = config.get("api_key")
  supabase_url = config.get("supabase_url")
  supabase_key = config.get("supabase_key")

  if not api_key or api_key == "YOUR_SCOPUS_API_KEY_HERE":
    log_callback("ERROR: Please provide a valid Scopus API Key in config/settings.json")
    return False

  if not supabase_url or not supabase_key:
    log_callback("ERROR: Supabase URL or Key not found in config/settings.json")
    return False

  try:
    # 1. ค้นหาไฟล์บทความวิจัยล่าสุดเพื่อดึง ISSN
    data_dir = os.path.join(os.path.dirname(__file__), "..", "data")
    search_dirs = [
        os.path.join(data_dir, "periodic_pulls"),
        os.path.join(data_dir, "processed")
    ]
    
    pub_files = []
    for d in search_dirs:
      if os.path.exists(d):
        pub_files.extend([os.path.join(d, f) for f in os.listdir(d) if f.endswith(".xlsx")])

    if not pub_files:
      log_callback("WARNING: No publication files found. Please run 'Run Sync Pipeline' first.")
      return False

    latest_pub = max(pub_files, key=os.path.getmtime)
    df_pub = pd.read_excel(latest_pub)
    
    df_pub.columns = [str(col).strip() for col in df_pub.columns]
    issn_col = None
    for col in df_pub.columns:
      if col.lower() in ["issn", "prism:issn"]:
        issn_col = col
        break

    if not issn_col:
      log_callback("ERROR: Column 'ISSN' not found in publication file.")
      return False

    file_issns = set()
    for val in df_pub[issn_col].dropna():
      raw_str = str(val).strip()
      for comma_part in raw_str.split(","):
        for space_part in comma_part.split():
          cleaned = clean_issn(space_part)
          if cleaned:
            file_issns.add(cleaned)

    log_callback(f"INFO: Found {len(file_issns)} unique clean ISSNs in publication files.")

    # 2. เชื่อมต่อ Supabase เช็ก ISSN ที่มีอยู่แล้ว
    supabase = create_client(supabase_url, supabase_key)
    
    existing_issns = set()
    try:
      response = supabase.table("journal_metrics").select("ISSN_clean").execute()
      if response.data:
        existing_issns = {item["ISSN_clean"] for item in response.data if item.get("ISSN_clean")}
    except Exception as e:
      log_callback(f"DEBUG: Could not fetch existing ISSNs: {e}")

    new_issns = file_issns - existing_issns
    log_callback(f"INFO: Found {len(new_issns)} new ISSNs to fetch from Scopus API.")

    if not new_issns:
      log_callback("SUCCESS: All journal metrics are already up to date! No new ISSNs to fetch.")
      return True

    headers = {"X-ELS-APIKey": api_key, "Accept": "application/json"}
    current_year = datetime.now().year
    records = []

    for idx, issn in enumerate(sorted(new_issns), 1):
      url = f"https://api.elsevier.com/content/serial/title/issn/{issn}"
      params = {"view": "CITESCORE"}
      
      try:
        res = requests.get(url, headers=headers, params=params, timeout=30)
        if res.status_code != 200:
          log_callback(f"DEBUG: ISSN {issn} returned status code {res.status_code}")
          time.sleep(0.3)
          continue

        data = res.json()
        if isinstance(data, list):
          data = data[0] if data else {}

        meta_resp = data.get("serial-metadata-response", {})
        if isinstance(meta_resp, list):
          meta_resp = meta_resp[0] if meta_resp else {}

        entries = meta_resp.get("entry", [])
        if isinstance(entries, dict): entries = [entries]
        elif not isinstance(entries, list): entries = []

        if not entries:
          time.sleep(0.3)
          continue
            
        entry = entries[0]
        title = entry.get("dc:title") or entry.get("prism:publicationName") or ""
        
        source_id = str(entry.get("dc:identifier", "")).replace("SCOPUS_ID:", "").strip()
        if not source_id:
          source_id = str(entry.get("source-id") or "").strip()

        publisher = entry.get("prism:publisher") or ""

        # ใช้ฟังก์ชันแกะ CiteScore และ Percentile ตัวใหม่ที่แม่นยำ
        publisher, citescore_val, percentile, subject_areas, ranks_list = parse_scopus_entry(entry)

        percentile_int = int(round(percentile, 0)) if percentile > 0 else 0
        quartile = percentile_to_quartile(percentile_int)
        
        subject_str = "; ".join(subject_areas) if subject_areas else ""
        rank_str_combined = "; ".join(ranks_list) if ranks_list else ""

        records.append({
            "ISSN": issn,
            "ISSN_clean": issn,
            "Source title": str(title).strip(),
            "Source ID": str(source_id),
            "Publisher": publisher,  # ใช้ตัวแปร publisher ที่ดึงมาได้
            "CiteScore": citescore_val if citescore_val > 0 else None,
            "CiteScore Percentile": percentile_int if percentile_int > 0 else None,
            "Scopus Quartile": quartile if quartile else None,
            "Subject Area": "; ".join(subject_areas) if subject_areas else "",
            "Rank": rank_str_combined,
            "Metric Year": current_year
        })
        
        log_callback(f"SYNCED [{idx}/{len(new_issns)}]: ISSN: {issn} - {title} (CS: {citescore_val}, Q: {quartile})")
        time.sleep(0.3)
      except Exception as e:
        log_callback(f"DEBUG: Exception for ISSN {issn}: {e}")
        continue

    if records:
      # ป้องกันข้อมูลซ้ำด้วย Source ID
      seen_source_ids = set()
      unique_records = []
      for rec in records:
        s_id = rec["Source ID"]
        if s_id and s_id not in seen_source_ids:
          seen_source_ids.add(s_id)
          unique_records.append(rec)

      log_callback(f"INFO: Upserting {len(unique_records)} unique journal records into Supabase...")
      batch_size = 500
      for i in range(0, len(unique_records), batch_size):
        batch = unique_records[i:i + batch_size]
        supabase.table("journal_metrics").upsert(batch, on_conflict="ISSN_clean,Metric Year").execute()
      
      log_callback("SUCCESS: New journal metrics successfully synced with full details!")
      return True
    else:
      log_callback("INFO: No new records fetched.")
      return True

  except Exception as e:
    log_callback(f"ERROR: Exception during incremental sync: {e}")
    return False

if __name__ == "__main__":
  download_and_sync_metrics(print)