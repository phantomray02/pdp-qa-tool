import argparse
import html
import re
import time
import zipfile
from pathlib import Path

import pandas as pd
import requests

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
}

EXCEL_CELL_LIMIT = 30000
CONTEXT_WINDOW = 1200

TITLE_PATTERNS = [
    r"<title[^>]*>.*?</title>",
    r"<h1[^>]*>.*?</h1>",
    r'"title"\s*:',
    r'"name"\s*:',
    r'"productName"\s*:',
]

VENDOR_PATTERNS = [
    r'"brand"\s*:',
    r'"vendor"\s*:',
    r'"manufacturer"\s*:',
    r'>\s*From\s+[^<]+<',
    r'from\s+[A-Za-z0-9 &._-]+',
]

DESCRIPTION_PATTERNS = [
    r'"description"\s*:',
    r'"longDescription"\s*:',
    r'"shortDescription"\s*:',
    r'"productDescription"\s*:',
    r'description',
]

FEATURES_PATTERNS = [
    r'"features"\s*:',
    r'"feature"\s*:',
    r'"benefits"\s*:',
    r'"bullets"\s*:',
    r'"bulletText"\s*:',
    r'"keyFeatures"\s*:',
    r'features',
]


def safe_name(text: str) -> str:
    text = str(text or "").strip()
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", text)
    return text[:120] or "row"


def clean_snippet(text: str) -> str:
    text = html.unescape(str(text or ""))
    text = text.replace("\x00", "")
    return text.strip()


def find_first_context(source: str, patterns: list, window: int = CONTEXT_WINDOW):
    source = str(source or "")
    if not source:
        return "", ""

    for pattern in patterns:
        match = re.search(pattern, source, flags=re.IGNORECASE | re.DOTALL)
        if match:
            start = max(0, match.start() - window)
            end = min(len(source), match.end() + window)
            anchor = match.group(0)
            context = source[start:end]
            return clean_snippet(anchor), clean_snippet(context)

    return "", ""


def extract_source_contexts(source: str) -> dict:
    title_anchor, title_context = find_first_context(source, TITLE_PATTERNS)
    vendor_anchor, vendor_context = find_first_context(source, VENDOR_PATTERNS)
    description_anchor, description_context = find_first_context(source, DESCRIPTION_PATTERNS)
    features_anchor, features_context = find_first_context(source, FEATURES_PATTERNS)

    return {
        "title_anchor": title_anchor,
        "title_source_context": title_context,
        "vendor_anchor": vendor_anchor,
        "vendor_source_context": vendor_context,
        "description_anchor": description_anchor,
        "description_source_context": description_context,
        "features_anchor": features_anchor,
        "features_source_context": features_context,
    }


def find_url_column(df: pd.DataFrame) -> str:
    normalized = {str(c).strip().lower(): c for c in df.columns}
    for candidate in ["retail url", "retail_url", "url", "product url"]:
        if candidate in normalized:
            return normalized[candidate]
    raise ValueError("Could not find a retail URL column.")


def load_input(path: str) -> pd.DataFrame:
    p = Path(path)

    if p.suffix.lower() == ".csv":
        df = pd.read_csv(p)

    elif p.suffix.lower() in [".xlsx", ".xlsm", ".xls"]:
        try:
            xls = pd.ExcelFile(p, engine="openpyxl")
            sheet = "Summary" if "Summary" in xls.sheet_names else xls.sheet_names[0]
            df = pd.read_excel(p, sheet_name=sheet, engine="openpyxl")
        except Exception:
            df = pd.read_excel(p, engine="openpyxl")

    else:
        raise ValueError("Unsupported input type. Use CSV or XLSX.")

    return df


def chunk_text(text: str, chunk_size: int = EXCEL_CELL_LIMIT) -> list:
    text = text if isinstance(text, str) else str(text or "")
    if not text:
        return [""]
    return [text[i:i + chunk_size] for i in range(0, len(text), chunk_size)]


def fetch_all(input_path: str, out_dir: str, sleep_seconds: float = 0.5):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    txt_dir = out / "raw_txt"
    txt_dir.mkdir(exist_ok=True)

    df = load_input(input_path)
    url_col = find_url_column(df)

    cols_lower = {str(c).strip().lower(): c for c in df.columns}
    sku_col = cols_lower.get("sku") or cols_lower.get("sku id") or cols_lower.get("product sku")
    rpc_col = cols_lower.get("cvs rpc")
    brand_col = cols_lower.get("brand")

    session = requests.Session()
    session.headers.update(HEADERS)

    summary_rows = []
    raw_rows = []

    for idx, row in df.iterrows():
        url = str(row[url_col]).strip() if pd.notna(row[url_col]) else ""
        sku = str(row.get(sku_col, "")).strip() if sku_col else ""
        rpc = str(row.get(rpc_col, "")).strip() if rpc_col else ""
        brand = str(row.get(brand_col, "")).strip() if brand_col else ""

        if not url:
            summary_rows.append({
                "row_number": idx + 1,
                "sku": sku,
                "cvs_rpc": rpc,
                "brand": brand,
                "retail_url": "",
                "final_url": "",
                "status_code": "",
                "source_capture_status": "missing_url",
                "source_capture_error": "missing_url",
                "source_file": "",
                "source_bytes": 0,
                "source_length": 0,
                "title_anchor": "",
                "title_source_context": "",
                "vendor_anchor": "",
                "vendor_source_context": "",
                "description_anchor": "",
                "description_source_context": "",
                "features_anchor": "",
                "features_source_context": "",
            })

            raw_rows.append({
                "row_number": idx + 1,
                "sku": sku,
                "cvs_rpc": rpc,
                "brand": brand,
                "retail_url": "",
                "raw_source_1": "",
            })
            continue

        base_name = safe_name(rpc if rpc else f"row_{idx + 1}")
        txt_path = txt_dir / f"{base_name}_RAW.txt"

        status_code = ""
        final_url = ""
        capture_status = "failed"
        capture_error = ""
        source = ""

        contexts = {
            "title_anchor": "",
            "title_source_context": "",
            "vendor_anchor": "",
            "vendor_source_context": "",
            "description_anchor": "",
            "description_source_context": "",
            "features_anchor": "",
            "features_source_context": "",
        }

        try:
            resp = session.get(url, timeout=30, allow_redirects=True)
            status_code = resp.status_code
            final_url = resp.url

            if resp.status_code == 200:
                source = resp.text
                txt_path.write_text(source, encoding="utf-8")
                capture_status = "success"
            else:
                capture_error = f"http_{resp.status_code}"

        except Exception as exc:
            capture_error = f"{type(exc).__name__}: {exc}"

        contexts = extract_source_contexts(source)

        source_bytes = len(source.encode("utf-8", errors="ignore")) if source else 0
        source_length = len(source) if source else 0

        summary_rows.append({
            "row_number": idx + 1,
            "sku": sku,
            "cvs_rpc": rpc,
            "brand": brand,
            "retail_url": url,
            "final_url": final_url,
            "status_code": status_code,
            "source_capture_status": capture_status,
            "source_capture_error": capture_error,
            "source_file": str(txt_path) if txt_path.exists() else "",
            "source_bytes": source_bytes,
            "source_length": source_length,
            "title_anchor": contexts["title_anchor"],
            "title_source_context": contexts["title_source_context"],
            "vendor_anchor": contexts["vendor_anchor"],
            "vendor_source_context": contexts["vendor_source_context"],
            "description_anchor": contexts["description_anchor"],
            "description_source_context": contexts["description_source_context"],
            "features_anchor": contexts["features_anchor"],
            "features_source_context": contexts["features_source_context"],
        })

        chunks = chunk_text(source)

        raw_row = {
            "row_number": idx + 1,
            "sku": sku,
            "cvs_rpc": rpc,
            "brand": brand,
            "retail_url": url,
        }

        for i, chunk in enumerate(chunks, start=1):
            raw_row[f"raw_source_{i}"] = chunk

        raw_rows.append(raw_row)

        time.sleep(sleep_seconds)

    summary_df = pd.DataFrame(summary_rows)
    raw_df = pd.DataFrame(raw_rows)

    debugger_df = summary_df.merge(
        raw_df,
        on=["row_number", "sku", "cvs_rpc", "brand", "retail_url"],
        how="left"
    )

    excel_path = out / "cvs_debugger_with_source.xlsx"
    with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
        debugger_df.to_excel(writer, index=False, sheet_name="debugger")
        summary_df.to_excel(writer, index=False, sheet_name="summary")
        raw_df.to_excel(writer, index=False, sheet_name="raw_source")

    zip_path = out / "cvs_debugger_with_source_bundle.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(excel_path, arcname=excel_path.name)
        for file in txt_dir.glob("*.txt"):
            zf.write(file, arcname=f"raw_txt/{file.name}")

    return excel_path, zip_path


def main():
    parser = argparse.ArgumentParser(
        description="Fetch raw CVS source for every URL and export debugger Excel with raw source and source contexts."
    )
    parser.add_argument(
        "input_file",
        help="CSV or XLSX containing Retail URL column. Summary sheet is used automatically for XLSX if present.",
    )
    parser.add_argument("--out-dir", default="cvs_raw_source_output", help="Output directory.")
    parser.add_argument("--sleep", type=float, default=0.5, help="Sleep between requests in seconds.")
    args = parser.parse_args()

    excel_path, zip_path = fetch_all(args.input_file, args.out_dir, args.sleep)
    print(f"Debugger Excel written to: {excel_path}")
    print(f"ZIP bundle written to: {zip_path}")


if __name__ == "__main__":
    main()
