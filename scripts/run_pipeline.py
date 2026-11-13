from datetime import datetime
import os
import sys
import pandas as pd

# เพิ่ม path ให้ Python หาโมดูลในโฟลเดอร์ scripts เจอ
sys.path.append(os.path.dirname(__file__))

# นำเข้าฟังก์ชันจากโมดูลต่างๆ
from generate_reports import generate_advanced_annual_report
from map_quartile import apply_quartile_mapping, fetch_journal_metrics_from_supabase
from scopus_fetch import main as run_scopus_sync
from file_organizer import get_target_dir, get_formatted_filename, explode_authors_data


def run_full_pipeline(log_callback=print):
    """ฟังก์ชันรันกระบวนการทั้งหมดของ ScopusSync Pipeline ตามเงื่อนไขครบถ้วนทุกโฟลเดอร์"""
    log_callback("INFO: ========================================")
    log_callback("INFO: Starting ScopusSync Pipeline Execution")
    log_callback("INFO: ========================================")

    try:
        # 1. รันดึงข้อมูลดิบจาก Scopus API (บันทึก yearly_raw และ master_5_years อัตโนมัติ)
        log_callback("INFO: Step 1: Fetching data from Scopus API...")
        run_scopus_sync(log_callback)

        # 2. ดึงข้อมูลดิบล่าสุดมาทำการ Mapping ค่า Metric จาก Supabase
        log_callback("INFO: ========================================")
        log_callback("INFO: Step 2: Applying Journal Metrics Mapping from Supabase...")
        
        data_dir = os.path.join(os.path.dirname(__file__), "..", "data")
        raw_dir = os.path.join(data_dir, "yearly_raw")
        
        if os.path.exists(raw_dir):
            raw_files = []
            for root, dirs, files in os.walk(raw_dir):
                for file in files:
                    if file.endswith(".xlsx"):
                        raw_files.append(os.path.join(root, file))
            
            if raw_files:
                latest_raw_file = max(raw_files, key=os.path.getmtime)
                df_latest = pd.read_excel(latest_raw_file)
                
                # ทำการ Mapping ข้อมูล Metric จาก Supabase เข้ากับข้อมูลดิบ
                df_mapped = apply_quartile_mapping(df_latest, log_callback)

                # --- 2.1 บันทึกลงโฟลเดอร์ periodic_pulls (ไฟล์ใหม่รายรอบ พร้อมค่า Metric) ---
                log_callback("INFO: Saving periodic pull snapshot with metrics...")
                timestamp_file = datetime.now().strftime("%Y%m%d_%H%M%S")
                if "Year" in df_mapped.columns:
                    for year_val, group in df_mapped.groupby("Year"):
                        if pd.isna(year_val) or str(year_val).strip() == "":
                            continue
                        sub_dir_p = get_target_dir("periodic_pulls", year_val)
                        periodic_filename = f"periodic_pull_{year_val}_{timestamp_file}.xlsx"
                        periodic_path = os.path.join(sub_dir_p, periodic_filename)
                        group.to_excel(periodic_path, index=False)
                        log_callback(f"SUCCESS: Saved periodic pull snapshot to {periodic_path}")

                # --- 2.2 บันทึกลงโฟลเดอร์ split_cumulative_yearly (Mapping Metric + Explode ผู้แต่งรายบุคคล) ---
                log_callback("INFO: Processing and saving split cumulative yearly data (Exploding authors)...")
                df_exploded = explode_authors_data(df_mapped)
                
                if "Year" in df_exploded.columns:
                    for year, group_df in df_exploded.groupby("Year"):
                        if pd.isna(year) or str(year).strip() == "":
                            continue
                        sub_dir_s = get_target_dir("split_cumulative_yearly", year)
                        split_filename = f"scopus_split_cumulative_{year}.xlsx"
                        split_path = os.path.join(sub_dir_s, split_filename)

                        # Smart Append สำหรับ Split Cumulative (คง Timestamp เก่าด้วย keep='first')
                        if os.path.exists(split_path):
                            df_old_split = pd.read_excel(split_path)
                            group_combined = pd.concat([df_old_split, group_df], ignore_index=True)
                        else:
                            group_combined = group_df

                        if "EID" in group_combined.columns and "Authors" in group_combined.columns:
                            group_combined = group_combined.drop_duplicates(subset=["EID", "Authors"], keep="first")

                        group_combined.to_excel(split_path, index=False)
                        log_callback(f"SUCCESS: Saved/Updated split cumulative data for Year {year} to {split_path}")

        # 3. รัน Mapping Pipeline สำหรับโฟลเดอร์ processed (Smart Append วารสารประจำปี)
        log_callback("INFO: ========================================")
        log_callback("INFO: Step 3: Starting Quartile & CiteScore Mapping for Processed folder...")
        from map_quartile import run_mapping_pipeline
        run_mapping_pipeline(log_callback)

        # 4. สร้างรายงานสรุปสถิติ 3 Sheets ลงโฟลเดอร์ reports อัตโนมัติ
        log_callback("INFO: ========================================")
        log_callback("INFO: Step 4: Generating comprehensive summary reports...")
        generate_advanced_annual_report(log_callback)

        log_callback("INFO: ========================================")
        log_callback("SUCCESS: ScopusSync Pipeline Completed Successfully!")
        log_callback("INFO: ========================================")
        return True

    except Exception as e:
        log_callback(f"ERROR: Pipeline failed during execution: {e}")
        return False


if __name__ == "__main__":
    run_full_pipeline(print)