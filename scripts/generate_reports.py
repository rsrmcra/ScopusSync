from datetime import datetime
import os
import pandas as pd

# นำเข้าฟังก์ชันจัดการโฟลเดอร์และตั้งชื่อไฟล์ตามมาตรฐานใหม่
from file_organizer import get_formatted_filename, get_target_dir

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROCESSED_DIR = os.path.join(BASE_DIR, "data", "processed")
REPORTS_DIR = os.path.join(BASE_DIR, "data", "reports")


def generate_advanced_annual_report(log_callback=print):
    """สร้างรายงานสรุปสถิติแบบหลาย Sheet พร้อมกรอง Article/Review, เพิ่มคอลัมน์ No Quartile และทำ Monthly Log"""
    log_callback("INFO: Generating advanced multi-sheet summary reports...")

    if not os.path.exists(PROCESSED_DIR):
        log_callback("WARNING: Processed directory not found. Please run mapping first.")
        return

    # ค้นหาไฟล์ที่แมปแล้วทั้งหมดในทุกโฟลเดอร์ย่อยรายปีของ processed (รองรับโครงสร้างใหม่)
    processed_files = []
    for root, dirs, files in os.walk(PROCESSED_DIR):
        for file in files:
            if file.startswith("scopus_mapped_") and file.endswith(".xlsx"):
                processed_files.append(os.path.join(root, file))

    if not processed_files:
        log_callback("WARNING: No processed mapped files found in subdirectories.")
        return

    # รวบรวมข้อมูลจากทุกไฟล์ย่อยรายปีมารวมกันเป็น DataFrame เดียวเพื่อทำรายงานภาพรวม
    log_callback(f"INFO: Found {len(processed_files)} processed files. Combining data for reports...")
    dfs = []
    for fpath in processed_files:
        try:
            temp_df = pd.read_excel(fpath)
            if not temp_df.empty:
                dfs.append(temp_df)
        except Exception as e:
            log_callback(f"WARNING: Could not read {fpath}: {e}")

    if not dfs:
        log_callback("ERROR: All processed files are empty or unreadable.")
        return

    df = pd.concat(dfs, ignore_index=True)

    # 1. ทำ Deduplication ด้วย EID ทั้งหมดก่อนนำมาประมวลผล
    eid_col = None
    for col in ["EID", "eid"]:
        if col in df.columns:
            eid_col = col
            break
            
    if eid_col:
        initial_count = len(df)
        df = df.drop_duplicates(subset=[eid_col], keep="last")
        log_callback(f"INFO: Deduplicated by {eid_col}. Removed duplicates. Remaining: {len(df)} records.")

    # ตรวจสอบคอลัมน์สำคัญ
    year_col = "Year" if "Year" in df.columns else ("publication_year" if "publication_year" in df.columns else None)
    quartile_col = "Scopus Quartile" if "Scopus Quartile" in df.columns else ("quartile" if "quartile" in df.columns else None)
    doc_type_col = None
    for col in ["Document Type", "document_type", "Type", "Doc Type"]:
        if col in df.columns:
            doc_type_col = col
            break

    if not year_col or not quartile_col:
        log_callback("ERROR: Required columns ('Year' or 'Scopus Quartile') not found.")
        return

    # จัดการข้อมูล Quartile ให้สะอาด (ถ้าไม่มีค่า ให้ระบุเป็น 'No Quartile')
    df[quartile_col] = df[quartile_col].fillna("").astype(str).str.strip()
    df.loc[df[quartile_col] == "", quartile_col] = "No Quartile"

    # ดึงเดือนที่ดึงข้อมูล
    current_month_str = datetime.now().strftime("%Y-%m")
    if "Fetch Timestamp" in df.columns:
        try:
            df["Month_Tag"] = pd.to_datetime(df["Fetch Timestamp"], errors="coerce").dt.strftime("%Y-%m")
            df["Month_Tag"] = df["Month_Tag"].fillna(current_month_str)
        except:
            df["Month_Tag"] = current_month_str
    else:
        df["Month_Tag"] = current_month_str

    # เตรียมบันทึกไฟล์รายงานลงในโฟลเดอร์ reports (โฟลเดอร์นี้ไม่มีโฟลเดอร์ย่อยรายปีตามเงื่อนไข)
    os.makedirs(REPORTS_DIR, exist_ok=True)
    
    # ใช้ชื่อมาตรฐานใหม่ เช่น scopus_comprehensive_report_[YYYYMMDD].xlsx
    current_year_str = str(datetime.now().year)
    report_filename = get_formatted_filename("scopus_comprehensive_report", current_year_str)
    report_path = os.path.join(REPORTS_DIR, report_filename)

    with pd.ExcelWriter(report_path, engine="openpyxl") as writer:
        
        # --- SHEET 1: Summary by Year & Quartile (เฉพาะ Article & Review + Total) ---
        log_callback("INFO: Building Sheet 1: Annual Summary (Article & Review)...")
        if doc_type_col:
            df_art_rev = df[df[doc_type_col].astype(str).str.lower().isin(["article", "review"])].copy()
        else:
            log_callback("WARNING: Document Type column not found. Sheet 1 will use all records.")
            df_art_rev = df.copy()

        pivot_s1 = df_art_rev.pivot_table(
            index=year_col,
            columns=quartile_col,
            values=eid_col if eid_col else df.columns[0],
            aggfunc="count",
            fill_value=0
        )
        
        desired_cols = ["Q1", "Q2", "Q3", "Q4", "No Quartile"]
        existing_cols = [c for c in desired_cols if c in pivot_s1.columns]
        other_cols = [c for c in pivot_s1.columns if c not in desired_cols]
        pivot_s1 = pivot_s1[existing_cols + other_cols]

        pivot_s1.loc["Total"] = pivot_s1.sum(numeric_only=True)
        pivot_s1["Total"] = pivot_s1.sum(axis=1, numeric_only=True)
        
        pivot_s1.reset_index().to_excel(writer, sheet_name="Annual_Art_Rev_Summary", index=False)

        # --- SHEET 2: Monthly & Annual Log (ทุกประเภทผลงาน + Total) ---
        log_callback("INFO: Building Sheet 2: Monthly & Annual Log (All Document Types)...")
        pivot_s2 = df.pivot_table(
            index=[year_col, "Month_Tag"],
            columns=quartile_col,
            values=eid_col if eid_col else df.columns[0],
            aggfunc="count",
            fill_value=0
        )
        
        existing_cols_s2 = [c for c in desired_cols if c in pivot_s2.columns]
        other_cols_s2 = [c for c in pivot_s2.columns if c not in desired_cols]
        pivot_s2 = pivot_s2[existing_cols_s2 + other_cols_s2]

        pivot_s2.loc[("Total", "")] = pivot_s2.sum(numeric_only=True)
        pivot_s2["Total"] = pivot_s2.sum(axis=1, numeric_only=True)

        pivot_s2.reset_index().to_excel(writer, sheet_name="Monthly_Log_All_Types", index=False)

        # --- SHEET 3: Monthly & Annual Log (เฉพาะ Article & Review + Total) ---
        log_callback("INFO: Building Sheet 3: Monthly & Annual Log (Article & Review)...")
        pivot_s3 = df_art_rev.pivot_table(
            index=[year_col, "Month_Tag"],
            columns=quartile_col,
            values=eid_col if eid_col else df.columns[0],
            aggfunc="count",
            fill_value=0
        )
        
        existing_cols_s3 = [c for c in desired_cols if c in pivot_s3.columns]
        other_cols_s3 = [c for c in pivot_s3.columns if c not in desired_cols]
        pivot_s3 = pivot_s3[existing_cols_s3 + other_cols_s3]

        pivot_s3.loc[("Total", "")] = pivot_s3.sum(numeric_only=True)
        pivot_s3["Total"] = pivot_s3.sum(axis=1, numeric_only=True)

        pivot_s3.reset_index().to_excel(writer, sheet_name="Monthly_Log_Art_Rev", index=False)

    log_callback(f"SUCCESS: Comprehensive report successfully saved to {report_path}")
    return report_path


if __name__ == "__main__":
    generate_advanced_annual_report(print)