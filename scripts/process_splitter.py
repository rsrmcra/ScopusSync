from datetime import datetime
import os
import pandas as pd

# กำหนด Path ของโฟลเดอร์ processed
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROCESSED_DIR = os.path.join(BASE_DIR, "data", "processed")


def process_and_split_data(df, log_callback=print):
    """ฟังก์ชันทำ Deduplication ด้วย EID และ Split ชื่อผู้วิจัย/สังกัด พร้อมบันทึกลงโฟลเดอร์ processed"""
    log_callback("INFO: Starting data deduplication and splitting process...")

    if df is None or df.empty:
        log_callback("WARNING: Input dataframe is empty.")
        return pd.DataFrame()

    try:
        # 1. ทำ Deduplication โดยอ้างอิงจาก EID (เก็บรายการแรกไว้)
        initial_count = len(df)
        if "eid" in df.columns:
            df = df.drop_duplicates(subset=["eid"], keep="first")
            log_callback(
                f"INFO: Deduplication completed. Removed {initial_count - len(df)} duplicate records. Remaining: {len(df)} records."
            )
        elif "EID" in df.columns:
            df = df.drop_duplicates(subset=["EID"], keep="first")
            log_callback(
                f"INFO: Deduplication completed. Removed {initial_count - len(df)} duplicate records. Remaining: {len(df)} records."
            )

        # 2. กระบวนการ Split ผู้วิจัยและสังกัด (Row Expansion)
        author_col = None
        for col in ["author-names", "authors", "Authors"]:
            if col in df.columns:
                author_col = col
                break

        if author_col:
            log_callback(f"INFO: Splitting authors and affiliations using column: {author_col}")
            expanded_rows = []
            for _, row in df.iterrows():
                authors_str = str(row.get(author_col, ""))
                authors_list = [a.strip() for a in authors_str.split(";") if a.strip()]

                if not authors_list:
                    expanded_rows.append(row)
                else:
                    for author in authors_list:
                        new_row = row.copy()
                        new_row["Parsed_Author"] = author
                        expanded_rows.append(new_row)

            df_expanded = pd.DataFrame(expanded_rows)
        else:
            log_callback("WARNING: Author column not found. Skipping row expansion, returning deduplicated data.")
            df_expanded = df.copy()

        # 3. เพิ่มการบันทึกไฟล์ผลลัพธ์ลงในโฟลเดอร์ processed อัตโนมัติที่นี่
        os.makedirs(PROCESSED_DIR, exist_ok=True)
        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_filename = f"scopus_processed_split_{timestamp_str}.xlsx"
        output_path = os.path.join(PROCESSED_DIR, output_filename)

        df_expanded.to_excel(output_path, index=False)
        log_callback(f"SUCCESS: Processed and split data successfully saved to {output_path}")
        log_callback(f"SUCCESS: Total records after processing: {len(df_expanded)}")

        return df_expanded

    except Exception as e:
        log_callback(f"ERROR: Failed during process and split: {e}")
        return df


if __name__ == "__main__":
    print("Testing process_splitter module...")