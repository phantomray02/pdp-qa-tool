
import argparse
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


def safe_name(text: str) -> str:
    text = str(text or "").strip()
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", text)
    return text[:120] or "row"


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

    session = requests.Session()
    session.headers.update(HEADERS)

    summary_rows = []
    raw_rows = []

    for idx, row in df.iterrows():
        url = str(row[url_col]).strip() if pd.notna(row[url_col]) else ""
        sku = str(row.get(sku_col, "")).strip() if sku_col else ""
        rpc = str(row.get(rpc_col, "")).strip() if rpc_col else ""

        if not url:
            summary_rows.append({
                "row_number": idx + 1,
                "sku": sku,
                "cvs_rpc": rpc,
                "retail_url": "",
                "final_url": "",
                "status_code": "",
                "source_capture_status": "missing_url",
                "source_capture_error": "missing_url",
                "source_file": "",
                "source_bytes": 0,
                "source_length": 0,
            })

            raw_rows.append({
                "row_number": idx + 1,
                "sku": sku,
                "cvs_rpc": rpc,
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

        source_bytes = len(source.encode("utf-8", errors="ignore")) if source else 0
        source_length = len(source) if source else 0

        summary_rows.append({
            "row_number": idx + 1,
            "sku": sku,
            "cvs_rpc": rpc,
            "retail_url": url,
            "final_url": final_url,
            "status_code": status_code,
            "source_capture_status": capture_status,
            "source_capture_error": capture_error,
            "source_file": str(txt_path) if txt_path.exists() else "",
            "source_bytes": source_bytes,
            "source_length": source_length,
        })

        chunks = chunk_text(source)

        raw_row = {
            "row_number": idx + 1,
            "sku": sku,
            "cvs_rpc": rpc,
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
        on=["row_number", "sku", "cvs_rpc", "retail_url"],
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
        description="Fetch raw CVS source for every URL and export debugger Excel with full source split across columns."
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
