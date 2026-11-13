from datetime import datetime
import json
import os
import pandas as pd
from supabase import Client, create_client

# นำเข้าฟังก์ชันจัดการโฟลเดอร์และตั้งชื่อไฟล์ตามมาตรฐานใหม่
from file_organizer import get_formatted_filename, get_target_dir

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "config", "settings.json")


def load_config():
    if not os.path.exists(CONFIG_PATH):
        raise FileNotFoundError(f"Config file not found at: {CONFIG_PATH}")
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


config = load_config()
SUPABASE_URL = config.get("supabase_url")
SUPABASE_KEY = config.get("supabase_key")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

RAW_DIR = os.path.join(BASE_DIR, "data", "yearly_raw")
PROCESSED_DIR = os.path.join(BASE_DIR, "data", "processed")


def fetch_journal_metrics_from_supabase(log_callback=print):
    """ดึงข้อมูล CiteScore, Percentile, Quartile, Publisher, Subject Area จาก Supabase"""
    try:
        response = supabase.table("journal_metrics").select("*").execute()
        data = response.data
        if data:
            log_callback(f"Successfully fetched {len(data)} rows from Supabase.")
            return pd.DataFrame(data)
    except Exception as e:
        log_callback(f"Error fetching metrics from Supabase: {e}")
    return pd.DataFrame()


def apply_quartile_mapping(df, log_callback=print):
    """ฟังก์ชันทำ Mapping ค่า Metric กับ Supabase พร้อมระบบเติมชื่อวารสารให้อัตโนมัติหากไม่พบ"""
    if df is None or df.empty:
        return df

    metrics_df = fetch_journal_metrics_from_supabase(log_callback)
    
    # ดึงรายชื่อ Source title ทั้งหมดจากข้อมูลที่ดึงมา
    excel_title_col = "Source title" if "Source title" in df.columns else None
    if excel_title_col:
        df["Clean_Title"] = df[excel_title_col].astype(str).str.lower().str.strip()
        
        # ตรวจสอบวารสารที่ยังไม่มีใน Supabase เพื่อทำการ Add เพิ่มอัตโนมัติ (Auto-Upsert)
        existing_titles = set(metrics_df["Source title"].astype(str).str.lower().str.strip()) if not metrics_df.empty and "Source title" in metrics_df.columns else set()
        
        new_journals_to_add = []
        for title_val in df[excel_title_col].dropna().unique():
            clean_t = str(title_val).lower().strip()
            if clean_t and clean_t not in existing_titles:
                new_journals_to_add.append({
                    "Source title": str(title_val),
                    "Scopus Quartile": "No Quartile",
                    "Publisher": "",
                    "CiteScore": None,
                    "CiteScore Percentile": None,
                    "Subject Area": "",
                    "Rank": ""
                })
                existing_titles.add(clean_t) # ป้องกันการแอดซ้ำในรอบเดียวกัน

        # หากพบวารสารใหม่ ให้บันทึกเพิ่มลง Supabase ทันที
        if new_journals_to_add:
            log_callback(f"INFO: Found {len(new_journals_to_add)} new journals. Auto-adding to Supabase...")
            try:
                supabase.table("journal_metrics").upsert(new_journals_to_add).execute()
                log_callback("SUCCESS: New journals successfully added to Supabase database.")
                # โหลด metrics_df ใหม่เพื่อให้ข้อมูลอัปเดต
                metrics_df = fetch_journal_metrics_from_supabase(log_callback)
            except Exception as e:
                log_callback(f"WARNING: Could not auto-add new journals to Supabase: {e}")

    if metrics_df.empty:
        log_callback("Warning: No journal metrics found from Supabase.")
        return df

    db_title_col = "Source title" if "Source title" in metrics_df.columns else None

    if excel_title_col and db_title_col:
        metrics_df["Clean_Title"] = metrics_df[db_title_col].astype(str).str.lower().str.strip()

        metric_cols_mapping = {
            "Publisher": "Publisher",
            "CiteScore": "CiteScore",
            "CiteScore Percentile": "CiteScore Percentile",
            "Scopus Quartile": "Scopus Quartile",
            "Subject Area": "Subject Area",
            "Rank": "Rank"
        }

        cols_to_merge = ["Clean_Title"]
        rename_dict = {}
        for db_col, new_name in metric_cols_mapping.items():
            if db_col in metrics_df.columns:
                cols_to_merge.append(db_col)
                rename_dict[db_col] = new_name

        if len(cols_to_merge) > 1:
            subset_metrics = metrics_df[cols_to_merge].rename(columns=rename_dict)
            subset_metrics = subset_metrics.drop_duplicates(subset=["Clean_Title"], keep="last")
            df = pd.merge(df, subset_metrics, on="Clean_Title", how="left")
            log_callback("Mapping with Supabase metrics completed successfully.")

        df.drop(columns=["Clean_Title"], inplace=True, errors="ignore")
    return df


def map_quartile_for_file(file_path, log_callback=print):
    log_callback(f"Processing file: {file_path}")

    try:
        df = pd.read_excel(file_path)
    except Exception as e:
        log_callback(f"Error reading excel: {e}. Make sure the file is closed in Excel.")
        return

    if df.empty:
        log_callback("File is empty.")
        return

    # พยายามดึงปีจากข้อมูลในไฟล์ หรือจากชื่อไฟล์ เพื่อนำไปจัดเก็บในโฟลเดอร์ย่อยตามปี
    target_year = str(datetime.now().year)
    if "Year" in df.columns and not df["Year"].dropna().empty:
        try:
            target_year = str(int(float(str(df["Year"].dropna().iloc[0])[:4])))
        except:
            pass
    else:
        basename = os.path.basename(file_path)
        parts = basename.split("_")
        for p in parts:
            if p.isdigit() and len(p) == 4:
                target_year = p
                break

    # เรียกใช้ฟังก์ชัน Mapping Metric
    df_mapped = apply_quartile_mapping(df, log_callback)

    # --- ส่วนบันทึกไฟล์แบบ Cumulative Append ประจำปี (Smart Append + คง Timestamp ของเก่า) ---
    sub_dir = get_target_dir("processed", target_year)
    new_filename = f"scopus_mapped_{target_year}.xlsx"
    processed_path = os.path.join(sub_dir, new_filename)

    try:
        if os.path.exists(processed_path):
            df_old = pd.read_excel(processed_path)
            log_callback(f"Found existing processed file for year {target_year} ({len(df_old)} records). Merging new data...")
            
            df_combined = pd.concat([df_old, df_mapped], ignore_index=True)
            
            if "EID" in df_combined.columns:
                # keep='first' จะรักษารายการแรกสุดและ Timestamp ของเก่าไว้ไม่ให้เปลี่ยน
                df_combined = df_combined.drop_duplicates(subset=["EID"], keep="first")
            
            df_mapped = df_combined
            log_callback(f"Merged successfully. Total records after deduplication/append: {len(df_mapped)}")

        df_mapped.to_excel(processed_path, index=False)
        log_callback(f"SUCCESS: Mapped file updated and saved to {processed_path}\n")
        
    except PermissionError:
        log_callback(f"PermissionError: Please close the file {processed_path} in Excel before running.\n")


def run_mapping_pipeline(log_callback=print):
    log_callback("Processing mapping from Supabase...")
    
    if not os.path.exists(RAW_DIR):
        log_callback(f"Raw directory not found: {RAW_DIR}")
        return

    files_found = []
    for root, dirs, files in os.walk(RAW_DIR):
        for file in files:
            if file.endswith(".xlsx"):
                files_found.append(os.path.join(root, file))

    if not files_found:
        log_callback(f"No Excel files found in raw directory structure: {RAW_DIR}")
        return

    for file_path in files_found:
        map_quartile_for_file(file_path, log_callback)


if __name__ == "__main__":
    run_mapping_pipeline(print)