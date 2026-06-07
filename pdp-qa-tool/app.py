
import argparse
import os
import re
import time
import zipfile
from pathlib import Path

import pandas as pd
import requests

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36',
    'Accept-Language': 'en-US,en;q=0.9',
    'Connection': 'keep-alive',
}


def safe_name(text: str) -> str:
    text = str(text or '').strip()
    text = re.sub(r'[^A-Za-z0-9._-]+', '_', text)
    return text[:120] or 'row'


def find_url_column(df: pd.DataFrame) -> str:
    normalized = {str(c).strip().lower(): c for c in df.columns}
    for candidate in ['retail url', 'retail_url', 'url', 'product url']:
        if candidate in normalized:
            return normalized[candidate]
    raise ValueError('Could not find a retail URL column.')


def load_input(path: str) -> pd.DataFrame:
    p = Path(path)
    if p.suffix.lower() == '.csv':
        df = pd.read_csv(p)
    elif p.suffix.lower() in ['.xlsx', '.xlsm', '.xls']:
        # Prefer Summary sheet if it exists.
        try:
            xls = pd.ExcelFile(p, engine='openpyxl')
            sheet = 'Summary' if 'Summary' in xls.sheet_names else xls.sheet_names[0]
            df = pd.read_excel(p, sheet_name=sheet, engine='openpyxl')
        except Exception:
            df = pd.read_excel(p, engine='openpyxl')
    else:
        raise ValueError('Unsupported input type. Use CSV or XLSX.')
    return df


def fetch_all(input_path: str, out_dir: str, sleep_seconds: float = 0.5):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    html_dir = out / 'raw_html'
    html_dir.mkdir(exist_ok=True)

    df = load_input(input_path)
    url_col = find_url_column(df)
    cols_lower = {str(c).strip().lower(): c for c in df.columns}
    sku_col = cols_lower.get('sku') or cols_lower.get('sku id') or cols_lower.get('product sku')
    rpc_col = cols_lower.get('cvs rpc')

    session = requests.Session()
    session.headers.update(HEADERS)

    rows = []
    for idx, row in df.iterrows():
        url = str(row[url_col]).strip() if pd.notna(row[url_col]) else ''
        if not url:
            rows.append({
                'row_number': idx + 1,
                'sku': row.get(sku_col, '') if sku_col else '',
                'cvs_rpc': row.get(rpc_col, '') if rpc_col else '',
                'retail_url': '',
                'status_code': '',
                'saved_html_path': '',
                'html_bytes': 0,
                'error': 'missing_url',
            })
            continue

        sku = str(row.get(sku_col, '')).strip() if sku_col else ''
        rpc = str(row.get(rpc_col, '')).strip() if rpc_col else ''
        base_name = '_'.join([x for x in [safe_name(sku), safe_name(rpc), safe_name(str(idx + 1))] if x])
        html_path = html_dir / f'{base_name}.html'

        status_code = ''
        error = ''
        html_bytes = 0
        try:
            resp = session.get(url, timeout=30)
            status_code = resp.status_code
            if resp.status_code == 200:
                html_path.write_text(resp.text, encoding='utf-8')
                html_bytes = len(resp.text.encode('utf-8', errors='ignore'))
            else:
                error = f'http_{resp.status_code}'
        except Exception as exc:
            error = f'{type(exc).__name__}: {exc}'

        rows.append({
            'row_number': idx + 1,
            'sku': sku,
            'cvs_rpc': rpc,
            'retail_url': url,
            'status_code': status_code,
            'saved_html_path': str(html_path) if html_path.exists() else '',
            'html_bytes': html_bytes,
            'error': error,
        })
        time.sleep(sleep_seconds)

    index_path = out / 'cvs_raw_source_index.xlsx'
    pd.DataFrame(rows).to_excel(index_path, index=False, engine='openpyxl')

    zip_path = out / 'cvs_raw_source_bundle.zip'
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.write(index_path, arcname=index_path.name)
        for file in html_dir.glob('*.html'):
            zf.write(file, arcname=f'raw_html/{file.name}')

    return index_path, zip_path


def main():
    parser = argparse.ArgumentParser(description='Fetch raw CVS product page source HTML for every retail URL in a CSV/XLSX file.')
    parser.add_argument('input_file', help='CSV or XLSX containing Retail URL column. Summary sheet is used automatically for XLSX if present.')
    parser.add_argument('--out-dir', default='cvs_raw_source_output', help='Output directory.')
    parser.add_argument('--sleep', type=float, default=0.5, help='Sleep between requests in seconds.')
    args = parser.parse_args()

    index_path, zip_path = fetch_all(args.input_file, args.out_dir, args.sleep)
    print(f'Index written to: {index_path}')
    print(f'ZIP bundle written to: {zip_path}')


if __name__ == '__main__':
    main()
