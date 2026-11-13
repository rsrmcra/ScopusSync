from datetime import datetime
import os
import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")


def get_target_dir(folder_name, year_val):
    """สร้างและคืนค่า path โฟลเดอร์ย่อยตามปี (ยกเว้น master_5_years และ reports)"""
    base_path = os.path.join(DATA_DIR, folder_name)
    if folder_name in ["master_5_years", "reports"]:
        os.makedirs(base_path, exist_ok=True)
        return base_path

    try:
        year_str = str(int(float(str(year_val)[:4])))
        if len(year_str) != 4:
            year_str = str(datetime.now().year)
    except:
        year_str = str(datetime.now().year)

    year_path = os.path.join(base_path, year_str)
    os.makedirs(year_path, exist_ok=True)
    return year_path


def get_formatted_filename(prefix, year_val):
    """สร้างชื่อไฟล์ตามรูปแบบ [ชื่อกระบวนการ]_[ปี]_[YYYYMMDD].xlsx"""
    try:
        year_str = str(int(float(str(year_val)[:4])))
        if len(year_str) != 4:
            year_str = str(datetime.now().year)
    except:
        year_str = str(datetime.now().year)

    date_tag = datetime.now().strftime("%Y%m%d")
    return f"{prefix}_{year_str}_{date_tag}.xlsx"


def get_data_directories():
    dirs = {
        "master_5_years": os.path.join(DATA_DIR, "master_5_years"),
        "yearly_raw": os.path.join(DATA_DIR, "yearly_raw"),
        "split_cumulative": os.path.join(DATA_DIR, "split_cumulative_yearly"),
        "periodic_pulls": os.path.join(DATA_DIR, "periodic_pulls"),
        "reports": os.path.join(DATA_DIR, "reports"),
    }
    for path in dirs.values():
        os.makedirs(path, exist_ok=True)
    return dirs


def get_5year_period(year_val):
    try:
        y = int(str(year_val)[:4])
    except:
        y = datetime.now().year
    start_year = (y // 5) * 5
    end_year = start_year + 4
    return f"{start_year}-{end_year}"


def explode_authors_data(df):
    """ฟังก์ชันแตกแถวผู้แต่งรายบุคคล (1 แถวต่อ 1 คน) สำหรับ split_cumulative_yearly"""
    if df is None or df.empty:
        return df
    expanded_rows = []
    for _, row in df.iterrows():
        authors_str = str(row.get("Authors", ""))
        full_names_str = str(row.get("Author full names", ""))
        author_ids_str = str(row.get("Author(s) ID", ""))
        affils_str = str(row.get("Authors with affiliations", ""))

        authors_list = [x.strip() for x in authors_str.split(";") if x.strip()]
        full_names_list = [x.strip() for x in full_names_str.split(";") if x.strip()]
        author_ids_list = [x.strip() for x in author_ids_str.split(";") if x.strip()]
        affils_list = [x.strip() for x in affils_str.split(";") if x.strip()]

        num_authors = max(len(authors_list), len(full_names_list), len(author_ids_list), len(affils_list), 1)

        for i in range(num_authors):
            new_row = row.copy()
            new_row["Authors"] = authors_list[i] if i < len(authors_list) else ""
            new_row["Author full names"] = full_names_list[i] if i < len(full_names_list) else ""
            new_row["Author(s) ID"] = author_ids_list[i] if i < len(author_ids_list) else ""
            new_row["Authors with affiliations"] = affils_list[i] if i < len(affils_list) else ""
            expanded_rows.append(new_row)
    return pd.DataFrame(expanded_rows)


def save_processed_files(df_raw, df_split, log_callback=print):
    """ฟังก์ชันจัดการบันทึกไฟล์พร้อมระบบ Smart Append (ไม่เปลี่ยน Timestamp ของข้อมูลเก่า)"""
    log_callback("INFO: Starting file organization and saving process...")
    dirs = get_data_directories()
    current_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    current_year = str(datetime.now().year)

    try:
        if df_raw is not None and not df_raw.empty:
            # ใส่ Timestamp เฉพาะข้อมูลที่เข้ามาใหม่
            if "Fetch Timestamp" not in df_raw.columns:
                df_raw["Fetch Timestamp"] = current_timestamp

            if "Year" in df_raw.columns:
                df_raw["Temp_Year"] = df_raw["Year"].fillna(current_year)
            else:
                df_raw["Temp_Year"] = current_year

            # 1. โฟลเดอร์ master_5_years (Smart Append + คง Timestamp ของเก่าด้วย keep='first')
            for period, group in df_raw.groupby(df_raw["Temp_Year"].apply(get_5year_period)):
                master_filename = f"scopus_master_5_years_{period}.xlsx"
                master_path = os.path.join(dirs["master_5_years"], master_filename)

                if os.path.exists(master_path):
                    df_old = pd.read_excel(master_path)
                    df_combined = pd.concat([df_old, group], ignore_index=True)
                else:
                    df_combined = group

                if "EID" in df_combined.columns:
                    df_combined = df_combined.drop_duplicates(subset=["EID"], keep="first")
                elif "DOI" in df_combined.columns:
                    df_combined = df_combined.drop_duplicates(subset=["DOI"], keep="first")

                if "Temp_Year" in df_combined.columns:
                    df_combined = df_combined.drop(columns=["Temp_Year"], errors="ignore")

                df_combined.to_excel(master_path, index=False)
                log_callback(f"SUCCESS: Saved Master 5-years data ({period}) to {master_path}")

            # 2. โฟลเดอร์ yearly_raw (Smart Append รายปี + คง Timestamp เดิม)
            for year_val, group in df_raw.groupby("Temp_Year"):
                sub_dir = get_target_dir("yearly_raw", year_val)
                filename = f"scopus_raw_{year_val}.xlsx"  # ใช้ชื่อไฟล์ประจำปีหลักเพื่อให้ Smart Append ได้
                raw_path = os.path.join(sub_dir, filename)

                if os.path.exists(raw_path):
                    df_old = pd.read_excel(raw_path)
                    group_combined = pd.concat([df_old, group], ignore_index=True)
                else:
                    group_combined = group

                if "EID" in group_combined.columns:
                    group_combined = group_combined.drop_duplicates(subset=["EID"], keep="first")

                group_clean = group_combined.drop(columns=["Temp_Year"], errors="ignore")
                group_clean.to_excel(raw_path, index=False)
                log_callback(f"SUCCESS: Saved/Updated yearly raw data for year {year_val} to {raw_path}")

        # 3. โฟลเดอร์ split_cumulative_yearly (Mapping Metric แล้ว Explode ผู้แต่งรายบุคคล)
        if df_split is not None and not df_split.empty:
            if "Year" in df_split.columns:
                split_year_col = "Year"
            else:
                split_year_col = None

            if split_year_col:
                for year, group_df in df_split.groupby(split_year_col):
                    if pd.isna(year) or str(year).strip() == "":
                        continue
                    
                    # Explode ผู้แต่งรายบุคคล
                    group_exploded = explode_authors_data(group_df)
                    
                    sub_dir = get_target_dir("split_cumulative_yearly", year)
                    filename = f"scopus_split_cumulative_{year}.xlsx"
                    split_path = os.path.join(sub_dir, filename)

                    if os.path.exists(split_path):
                        df_old = pd.read_excel(split_path)
                        group_combined = pd.concat([df_old, group_exploded], ignore_index=True)
                    else:
                        group_combined = group_exploded

                    # ตัดซ้ำโดยยึดรายการแรกเพื่อรักษา Timestamp เดิม
                    if "EID" in group_combined.columns and "Authors" in group_combined.columns:
                        group_combined = group_combined.drop_duplicates(subset=["EID", "Authors"], keep="first")

                    group_combined.to_excel(split_path, index=False)
                    log_callback(f"SUCCESS: Saved/Updated split cumulative data for Year {year} to {split_path}")

        # 4. โฟลเดอร์ periodic_pulls (เก็บเป็นไฟล์ใหม่ตามรอบการรัน ทันทีในโฟลเดอร์ปีนั้นๆ)
        if df_split is not None and not df_split.empty:
            timestamp_file = datetime.now().strftime("%Y%m%d_%H%M%S")
            if "Year" in df_split.columns:
                for year_val, group in df_split.groupby("Year"):
                    if pd.isna(year_val) or str(year_val).strip() == "":
                        continue
                    sub_dir = get_target_dir("periodic_pulls", year_val)
                    filename = f"periodic_pull_{year_val}_{timestamp_file}.xlsx"
                    periodic_path = os.path.join(sub_dir, filename)
                    group.to_excel(periodic_path, index=False)
                    log_callback(f"SUCCESS: Saved periodic pull snapshot for year {year_val} to {periodic_path}")

        log_callback("SUCCESS: All data files organized and saved successfully.")
        return True

    except Exception as e:
        log_callback(f"ERROR: Failed to organize and save files: {e}")
        return False