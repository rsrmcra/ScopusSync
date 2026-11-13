from datetime import datetime
import json
import os
import sys
import time
import pandas as pd
import requests

# นำเข้าฟังก์ชันจัดการโฟลเดอร์และตั้งชื่อไฟล์ตามมาตรฐานใหม่
from file_organizer import get_formatted_filename, get_target_dir

# กำหนด Path ของ config ให้รองรับทั้ง Python Script และ .exe
if getattr(sys, 'frozen', False):
    # ถ้าเป็นไฟล์ .exe ให้มองหาโฟลเดอร์ config ที่อยู่ "ข้างๆ" ไฟล์ .exe
    BASE_DIR = os.path.dirname(sys.executable)
else:
    # ถ้าเป็นการรันโค้ด Python ปกติ ให้ถอยจาก scripts ไป 1 ชั้น
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CONFIG_PATH = os.path.join(BASE_DIR, "config", "settings.json")


def load_config():
    if not os.path.exists(CONFIG_PATH):
        raise FileNotFoundError(f"Config file not found at: {CONFIG_PATH}")
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


config = load_config()
API_KEY = config.get("api_key")

if not API_KEY or API_KEY == "YOUR_SCOPUS_API_KEY_HERE":
    raise ValueError("กรุณาระบุ Scopus API Key ที่ถูกต้องในไฟล์ config/settings.json")

MASTER_5Y_DIR = os.path.join(BASE_DIR, "data", "master_5_years")
CONFIG_DIR = os.path.join(BASE_DIR, "config")
LOG_DIR = os.path.join(BASE_DIR, "logs")

STATE_FILE = os.path.join(CONFIG_DIR, "sync_state.json")
LOG_FILE = os.path.join(LOG_DIR, "pipeline_log.txt")

COUNT = 25
MAX_RETRIES = 5
RETRY_SLEEP_SECONDS = 10
REQUEST_SLEEP_SECONDS = 2
ABSTRACT_REQUEST_SLEEP_SECONDS = 1

# 45 คอลัมน์มาตรฐาน Scopus + Fetch Timestamp ไว้ท้ายสุด
SCOPUS_COLUMNS = [
    "Authors", "Author full names", "Author(s) ID", "Title", "Year",
    "Source title", "Volume", "Issue", "Art. No.", "Page start", "Page end",
    "Cited by", "DOI", "Link", "Affiliations", "Authors with affiliations",
    "Abstract", "Author Keywords", "Index Keywords", "Molecular Sequence Numbers",
    "Chemicals/CAS", "Tradenames", "Manufacturers", "Funding Details",
    "Funding Texts", "References", "Correspondence Address", "Editors",
    "Publisher", "Sponsors", "Conference name", "Conference date",
    "Conference location", "Conference code", "ISSN", "ISBN", "CODEN",
    "PubMed ID", "Language of Original Document", "Abbreviated Source Title",
    "Document Type", "Publication Stage", "Open Access", "Source", "EID",
    "Fetch Timestamp"
]

# ตัวแปรสำหรับจัดการ Log ให้แสดงผ่าน GUI เท่านั้น
_current_log_callback = print


def set_log_callback(callback):
    global _current_log_callback
    _current_log_callback = callback


def log(message):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    text = f"[{now}] {message}"
    
    if _current_log_callback:
        _current_log_callback(text)
        
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(text + "\n")


def load_state():
    if not os.path.exists(STATE_FILE) or os.path.getsize(STATE_FILE) == 0:
        return {"first_full_sync_completed": False, "start_year": 2010}
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(state):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def safe_get(data, *keys, default=""):
    current = data
    for key in keys:
        if isinstance(current, dict):
            current = current.get(key, default)
        else:
            return default
    return current if current is not None else default


def as_list(value):
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    return [value]


def normalize_text(value):
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    text = text.replace("|", ";")
    text = " ".join(text.split())
    while ";;" in text:
        text = text.replace(";;", ";")
    text = text.replace("; ;", ";")
    return text.strip(" ;")


def join_clean(values, separator="; "):
    parts = []
    seen = set()
    for value in values:
        text = normalize_text(value)
        if not text:
            continue
        if text in seen:
            continue
        seen.add(text)
        parts.append(text)
    return separator.join(parts)


def clean_scopus_text(value):
    if value is None:
        return ""
    if isinstance(value, (str, int, float)):
        return normalize_text(str(value))
    if isinstance(value, list):
        parts = [clean_scopus_text(v) for v in value]
        return join_clean(parts, separator="; ")
    if isinstance(value, dict):
        preferred_keys = [
            "$", "text", "ce:text", "ce:indexed-name", "indexed-name",
            "affilname", "affiliation-name", "organization", "dc:title",
            "prism:publicationName", "ref-titletext"
        ]
        for key in preferred_keys:
            if key in value and value.get(key) not in [None, ""]:
                return clean_scopus_text(value.get(key))
        ignored_prefixes = ("@",)
        ignored_keys = {
            "@_fa", "@seq", "@id", "@href", "@type", "@idtype",
            "@auid", "@afid", "@country", "@xml:lang"
        }
        parts = []
        for key, subvalue in value.items():
            if key in ignored_keys or str(key).startswith(ignored_prefixes):
                continue
            text = clean_scopus_text(subvalue)
            if text:
                parts.append(text)
        return join_clean(parts, separator="; ")
    return normalize_text(str(value))


def extract_text(value):
    return clean_scopus_text(value)


def clean_row_values(row):
    return {column: clean_scopus_text(row.get(column, "")) for column in SCOPUS_COLUMNS}


def search_scopus_by_year(affiliation_id, year, start=0):
    url = "https://api.elsevier.com/content/search/scopus"
    headers = {"X-ELS-APIKey": API_KEY, "Accept": "application/json"}
    params = {
        "query": f"AF-ID({affiliation_id}) AND PUBYEAR = {year}",
        "start": start,
        "count": COUNT,
        "view": "STANDARD"
    }
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.get(url, headers=headers, params=params, timeout=60)
            if response.status_code in [429, 500, 502, 503, 504]:
                log(f"Scopus search error {response.status_code}. Retry {attempt}/{MAX_RETRIES}")
                time.sleep(RETRY_SLEEP_SECONDS)
                continue
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            log(f"Search request error. Retry {attempt}/{MAX_RETRIES}: {e}")
            time.sleep(RETRY_SLEEP_SECONDS)
    raise Exception(f"Scopus Search API failed: AF-ID({affiliation_id}) / {year} / start={start}")


def fetch_abstract_by_eid(eid):
    url = f"https://api.elsevier.com/content/abstract/eid/{eid}"
    headers = {"X-ELS-APIKey": API_KEY, "Accept": "application/json"}
    for view in ["FULL", "META_ABS"]:
        params = {"view": view}
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = requests.get(url, headers=headers, params=params, timeout=60)
                if response.status_code in [401, 403] and view == "FULL":
                    break
                if response.status_code in [429, 500, 502, 503, 504]:
                    time.sleep(RETRY_SLEEP_SECONDS)
                    continue
                response.raise_for_status()
                return response.json()
            except requests.exceptions.RequestException:
                time.sleep(RETRY_SLEEP_SECONDS)
    return {}


def parse_authors_from_detail(detail):
    response = safe_get(detail, "abstracts-retrieval-response", default={})
    authors = safe_get(response, "authors", "author", default=[])
    names, full_names, ids = [], [], []
    for author in as_list(authors):
        if not isinstance(author, dict):
            continue
        indexed = author.get("ce:indexed-name") or author.get("indexed-name") or ""
        surname = author.get("ce:surname") or author.get("surname") or ""
        given = author.get("ce:given-name") or author.get("given-name") or ""
        auid = author.get("@auid", "")
        if indexed:
            names.append(indexed)
        full = " ".join([x for x in [given, surname] if x]).strip()
        if full:
            full_names.append(full)
        if auid:
            ids.append(auid)
    return {
        "Authors": "; ".join(names),
        "Author full names": "; ".join(full_names),
        "Author(s) ID": "; ".join(ids)
    }


def parse_affiliations_from_detail(detail):
    response = safe_get(detail, "abstracts-retrieval-response", default={})
    affiliations = safe_get(response, "affiliation", default=[])
    output = []
    for aff in as_list(affiliations):
        if not isinstance(aff, dict):
            continue
        org_parts = []
        for org in as_list(aff.get("organization", [])):
            org_text = clean_scopus_text(org)
            if org_text:
                org_parts.append(org_text)
        if not org_parts:
            name = aff.get("affilname") or aff.get("affiliation-name") or ""
            if name:
                org_parts.append(name)
        city = aff.get("city") or aff.get("affiliation-city", "")
        country = aff.get("country") or aff.get("affiliation-country", "")
        text = ", ".join([x for x in org_parts + [city, country] if x])
        if text:
            output.append(text)
    return "; ".join(output)


def parse_authors_with_affiliations(detail):
    response = safe_get(detail, "abstracts-retrieval-response", default={})
    author_groups = as_list(safe_get(response, "item", "bibrecord", "head", "author-group", default=[]))
    auid_to_affils = {}

    def extract_full_affil(aff_dict):
        if not isinstance(aff_dict, dict):
            return clean_scopus_text(aff_dict)
        org_parts = []
        for org in as_list(aff_dict.get("organization", [])):
            o_text = clean_scopus_text(org)
            if o_text and o_text not in org_parts:
                org_parts.append(o_text)
        if not org_parts:
            name = clean_scopus_text(aff_dict.get("affilname") or aff_dict.get("affiliation-name") or "")
            if name:
                org_parts.append(name)
        city = clean_scopus_text(aff_dict.get("city") or aff_dict.get("affiliation-city", ""))
        country = clean_scopus_text(aff_dict.get("country") or aff_dict.get("affiliation-country", ""))
        return ", ".join([x for x in org_parts + [city, country] if x])

    for group in author_groups:
        if not isinstance(group, dict):
            continue
        group_affs = [extract_full_affil(g) for g in as_list(group.get("affiliation", [])) if isinstance(g, dict)]
        for author in as_list(group.get("author", [])):
            if isinstance(author, dict):
                auid = normalize_text(author.get("@auid", ""))
                if auid:
                    if auid not in auid_to_affils:
                        auid_to_affils[auid] = []
                    for aff_t in group_affs:
                        if aff_t and aff_t not in auid_to_affils[auid]:
                            auid_to_affils[auid].append(aff_t)

    authors = safe_get(response, "authors", "author", default=[])
    results = []
    for author in as_list(authors):
        if not isinstance(author, dict):
            continue
        indexed_name = normalize_text(author.get("ce:indexed-name") or author.get("indexed-name") or "")
        auid = normalize_text(author.get("@auid", ""))
        if not indexed_name:
            continue
        affs = auid_to_affils.get(auid, [])
        if affs:
            results.append(f"{indexed_name}, {', '.join(affs)}")
        else:
            results.append(indexed_name)
    
    return "; ".join(results)


def parse_keywords_from_detail(detail):
    response = safe_get(detail, "abstracts-retrieval-response", default={})
    authkeywords = safe_get(response, "authkeywords", "author-keyword", default=[])
    idxterms = safe_get(response, "idxterms", "mainterm", default=[])
    return {
        "Author Keywords": extract_text(authkeywords),
        "Index Keywords": extract_text(idxterms)
    }


def parse_funding_from_detail(detail):
    response = safe_get(detail, "abstracts-retrieval-response", default={})
    funding_list = safe_get(response, "item", "xocs:meta", "xocs:funding-list", default="") or safe_get(response, "funding-list", default="")
    funding_text = safe_get(response, "item", "bibrecord", "head", "grantlist", default="") or safe_get(response, "funding-text", default="")
    return {
        "Funding Details": extract_text(funding_list),
        "Funding Texts": extract_text(funding_text)
    }


def parse_references_clean(detail):
    response = safe_get(detail, "abstracts-retrieval-response", default={})
    references = safe_get(response, "item", "bibrecord", "tail", "bibliography", "reference", default=[])
    results = []
    for ref in as_list(references):
        if isinstance(ref, dict):
            ref_info = ref.get("ref-info", {})
            title = safe_get(ref_info, "ref-title", "ref-titletext", default="")
            source_title = safe_get(ref_info, "ref-sourcetitle", default="")
            year = safe_get(ref_info, "ref-publicationyear", "@first", default="")
            text = join_clean([extract_text(title), extract_text(source_title), extract_text(year)], separator="; ")
            if text:
                results.append(text)
    return join_clean(results, separator="; ")


def parse_detail(detail, search_item, unit_code, unit_name, year):
    response = safe_get(detail, "abstracts-retrieval-response", default={})
    core = safe_get(response, "coredata", default={})

    author_data = parse_authors_from_detail(detail)
    keyword_data = parse_keywords_from_detail(detail)
    funding_data = parse_funding_from_detail(detail)

    title = core.get("dc:title") or search_item.get("dc:title", "")
    source_title = core.get("prism:publicationName") or search_item.get("prism:publicationName", "")
    cover_date = core.get("prism:coverDate") or search_item.get("prism:coverDate", "")
    page_start = core.get("prism:startingPage") or search_item.get("prism:startingPage", "")
    page_end = core.get("prism:endingPage") or search_item.get("prism:endingPage", "")

    row = {
        "Authors": author_data.get("Authors") or search_item.get("dc:creator", ""),
        "Author full names": author_data.get("Author full names", ""),
        "Author(s) ID": author_data.get("Author(s) ID", ""),
        "Title": title,
        "Year": str(cover_date)[:4] if cover_date else year,
        "Source title": source_title,
        "Volume": core.get("prism:volume") or search_item.get("prism:volume", ""),
        "Issue": core.get("prism:issueIdentifier") or search_item.get("prism:issueIdentifier", ""),
        "Art. No.": core.get("article-number") or search_item.get("article-number", ""),
        "Page start": page_start,
        "Page end": page_end,
        "Cited by": core.get("citedby-count") or search_item.get("citedby-count", ""),
        "DOI": core.get("prism:doi") or search_item.get("prism:doi", ""),
        "Link": core.get("prism:url") or search_item.get("prism:url", ""),
        "Affiliations": parse_affiliations_from_detail(detail),
        "Authors with affiliations": parse_authors_with_affiliations(detail),
        "Abstract": core.get("dc:description") or "",
        "Author Keywords": keyword_data.get("Author Keywords", ""),
        "Index Keywords": keyword_data.get("Index Keywords", ""),
        "Molecular Sequence Numbers": "",
        "Chemicals/CAS": "",
        "Tradenames": "",
        "Manufacturers": "",
        "Funding Details": funding_data.get("Funding Details", ""),
        "Funding Texts": funding_data.get("Funding Texts", ""),
        "References": parse_references_clean(detail),
        "Correspondence Address": "",
        "Editors": "",
        "Publisher": core.get("dc:publisher", ""),
        "Sponsors": "",
        "Conference name": "",
        "Conference date": "",
        "Conference location": "",
        "Conference code": "",
        "ISSN": core.get("prism:issn") or search_item.get("prism:issn", ""),
        "ISBN": core.get("prism:isbn") or search_item.get("prism:isbn", ""),
        "CODEN": "",
        "PubMed ID": core.get("pubmed-id", ""),
        "Language of Original Document": "",
        "Abbreviated Source Title": source_title,
        "Document Type": core.get("subtypeDescription") or search_item.get("subtypeDescription", ""),
        "Publication Stage": "",
        "Open Access": core.get("openaccess") or search_item.get("openaccess", ""),
        "Source": "Scopus",
        "EID": core.get("eid") or search_item.get("eid", ""),
        "Fetch Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    return clean_row_values(row)


def parse_entry(item, unit_code, unit_name, year):
    eid = item.get("eid", "")
    if not eid:
        row = {column: "" for column in SCOPUS_COLUMNS}
        row["Fetch Timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return clean_row_values(row)
    detail = fetch_abstract_by_eid(eid)
    if not detail:
        row = {
            "Authors": item.get("dc:creator", ""),
            "Title": item.get("dc:title", ""),
            "Year": year,
            "Source title": item.get("prism:publicationName", ""),
            "EID": eid,
            "Fetch Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        return clean_row_values(row)
    time.sleep(ABSTRACT_REQUEST_SLEEP_SECONDS)
    return parse_detail(detail, item, unit_code, unit_name, year)


def fetch_year(unit_code, unit_name, affiliation_id, year):
    log(f"INFO: Fetching {unit_name} / AF-ID({affiliation_id}) / Year {year}")
    
    first_data = search_scopus_by_year(affiliation_id, year, start=0)
    search_results = first_data.get("search-results", {})
    total_results = int(search_results.get("opensearch:totalResults", 0))
    
    if total_results == 0:
        log(f"No records found for Year {year}")
        return []

    log(f"Found total {total_results} records for Year {year}")

    start = 0
    rows = []
    current_index = 0

    while True:
        if start > 0:
            data = search_scopus_by_year(affiliation_id, year, start=start)
            search_results = data.get("search-results", {})
        else:
            data = first_data

        entries = search_results.get("entry", [])
        if not entries:
            break

        for item in entries:
            current_index += 1
            eid = item.get("eid", "")
            log(f"YEAR [{year}] ({current_index}/{total_results}) | EID: {eid}")
            rows.append(parse_entry(item, unit_code, unit_name, year))
            
        start += COUNT
        if start >= total_results:
            break
        time.sleep(REQUEST_SLEEP_SECONDS)
        
    return rows


def get_5year_period(year_val):
    try:
        y = int(str(year_val)[:4])
    except:
        y = datetime.now().year
    start_year = (y // 5) * 5
    end_year = start_year + 4
    return f"{start_year}-{end_year}"


def save_to_master_5_years(df_new):
    os.makedirs(MASTER_5Y_DIR, exist_ok=True)
    if "Year" not in df_new.columns:
        df_new["Year"] = datetime.now().year

    df_new["Temp_Year"] = df_new["Year"]
    for period, group in df_new.groupby(df_new["Temp_Year"].apply(get_5year_period)):
        master_filename = f"scopus_master_5_years_{period}.xlsx"
        master_path = os.path.join(MASTER_5Y_DIR, master_filename)

        if os.path.exists(master_path):
            df_old = pd.read_excel(master_path)
            df_combined = pd.concat([df_old, group], ignore_index=True)
        else:
            df_combined = group

        if "EID" in df_combined.columns:
            df_combined = df_combined.drop_duplicates(subset=["EID"], keep="last")

        if "Temp_Year" in df_combined.columns:
            df_combined = df_combined.drop(columns=["Temp_Year"], errors="ignore")

        for col in SCOPUS_COLUMNS:
            if col not in df_combined.columns:
                df_combined[col] = ""
        df_combined = df_combined[SCOPUS_COLUMNS]

        df_combined.to_excel(master_path, index=False)
        log(f"SUCCESS: Saved Master 5-years ({period}) to {master_path} (Total: {len(df_combined)} records)")


def save_yearly_raw(df, year):
    sub_dir = get_target_dir("yearly_raw", year)
    filename = get_formatted_filename("scopus_raw", year)
    filepath = os.path.join(sub_dir, filename)
    df.to_excel(filepath, index=False)
    log(f"SUCCESS: Saved yearly raw data for {year} to {filepath} (Total: {len(df)} records)")


def save_periodic_pull(df, year):
    sub_dir = get_target_dir("periodic_pulls", year)
    filename = get_formatted_filename("periodic_pull", year)
    filepath = os.path.join(sub_dir, filename)
    df.to_excel(filepath, index=False)
    log(f"SUCCESS: Saved periodic pull snapshot for {year} to {filepath}")


def main(log_callback=print):
    set_log_callback(log_callback)

    if not API_KEY:
        raise ValueError("ไม่พบ SCOPUS_API_KEY ในไฟล์ config/settings.json")

    state = load_state()
    current_year = datetime.now().year
    start_year = int(state.get("start_year", 2010))

    if state.get("first_full_sync_completed") is False:
        years = list(range(start_year, current_year + 1))
        sync_mode = "FULL_SYNC"
    else:
        years = [current_year]
        sync_mode = "CURRENT_YEAR_SYNC"

    log(f"Sync mode: {sync_mode}")
    log(f"Years: {years}")

    aff_df = pd.DataFrame([
        {
            "unit_code": "CRA",
            "unit_name": config.get("affiliation_name", "Chulabhorn Royal Academy"),
            "scopus_affiliation_id": str(config.get("affiliation_id", "60022464"))
        }
    ])

    for year in years:
        all_rows = []
        for _, row in aff_df.iterrows():
            unit_code = row.get("unit_code", "")
            unit_name = row.get("unit_name", "")
            affiliation_id = str(row.get("scopus_affiliation_id", "")).strip()

            if not affiliation_id or affiliation_id.lower() == "nan":
                continue

            all_rows.extend(fetch_year(unit_code, unit_name, affiliation_id, year))

        if all_rows:
            df = pd.DataFrame(all_rows)
            save_yearly_raw(df, year)
            save_periodic_pull(df, year)
            save_to_master_5_years(df)
        else:
            log(f"No records found for {year}")

    if sync_mode == "FULL_SYNC":
        state["first_full_sync_completed"] = True
        state["last_full_sync_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    state["last_sync_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    state["last_sync_mode"] = sync_mode
    save_state(state)
    log("Sync completed successfully.")


if __name__ == "__main__":
    main(print)