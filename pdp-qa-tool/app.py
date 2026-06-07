import io
import json
import re
from difflib import SequenceMatcher
from html import unescape
from typing import Dict, List, Tuple

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
}

REQUEST_TIMEOUT = 20
EXCEL_CELL_LIMIT = 30000
CONTEXT_WINDOW = 1200


def normalize_space(text: str) -> str:
    text = str(text or "")
    text = unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def safe_text(text: str) -> str:
    return normalize_space(text).replace("\x00", "")


def chunk_text(text: str, chunk_size: int = EXCEL_CELL_LIMIT) -> List[str]:
    text = text if isinstance(text, str) else str(text or "")
    if not text:
        return [""]
    return [text[i:i + chunk_size] for i in range(0, len(text), chunk_size)]


def similarity_score(a: str, b: str) -> int:
    a = normalize_space(a)
    b = normalize_space(b)
    if not a or not b:
        return 0
    return int(round(SequenceMatcher(None, a, b).ratio() * 100))


def fetch_page(url: str) -> Tuple[int, str, str]:
    if not url or not str(url).strip():
        return 0, "", ""
    resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT, allow_redirects=True)
    return resp.status_code, resp.url, resp.text


def build_soup(html_source: str) -> BeautifulSoup:
    return BeautifulSoup(html_source or "", "html.parser")


def get_clean_page_text(soup: BeautifulSoup) -> str:
    soup_copy = BeautifulSoup(str(soup), "html.parser")
    for tag in soup_copy(["script", "style", "noscript"]):
        tag.decompose()
    return normalize_space(soup_copy.get_text(separator=" ", strip=True))


def extract_title_candidates(soup: BeautifulSoup) -> List[str]:
    out = []

    h1 = soup.find("h1")
    if h1:
        out.append(h1.get_text(" ", strip=True))

    title_tag = soup.find("title")
    if title_tag:
        out.append(title_tag.get_text(" ", strip=True))

    og_title = soup.find("meta", attrs={"property": "og:title"})
    if og_title and og_title.get("content"):
        out.append(og_title.get("content"))

    return [normalize_space(x) for x in out if normalize_space(x)]


def extract_description_candidates(soup: BeautifulSoup) -> List[str]:
    out = []

    meta_desc = soup.find("meta", attrs={"name": "description"})
    if meta_desc and meta_desc.get("content"):
        out.append(meta_desc.get("content"))

    og_desc = soup.find("meta", attrs={"property": "og:description"})
    if og_desc and og_desc.get("content"):
        out.append(og_desc.get("content"))

    return [normalize_space(x) for x in out if normalize_space(x)]


def extract_feature_candidates(source: str, soup: BeautifulSoup) -> List[str]:
    out = []

    feature_patterns = [
        r'"features"\s*:\s*(\[[^\]]+\])',
        r'"feature"\s*:\s*(\[[^\]]+\])',
        r'"benefits"\s*:\s*(\[[^\]]+\])',
        r'"bullets"\s*:\s*(\[[^\]]+\])',
        r'"bulletText"\s*:\s*(\[[^\]]+\])',
        r'"keyFeatures"\s*:\s*(\[[^\]]+\])',
    ]

    for pattern in feature_patterns:
        for m in re.finditer(pattern, source, flags=re.IGNORECASE | re.DOTALL):
            out.append(m.group(0))

    for li in soup.find_all("li"):
        txt = normalize_space(li.get_text(" ", strip=True))
        if len(txt) >= 20:
            out.append(txt)

    # de-duplicate
    seen = set()
    deduped = []
    for item in out:
        if item and item not in seen:
            seen.add(item)
            deduped.append(item)

    return deduped


def extract_vendor_candidates(source: str, soup: BeautifulSoup) -> List[str]:
    out = []

    vendor_patterns = [
        r'>\s*From\s+([^<]+)<',
        r'See all\s+([A-Za-z0-9 &._-]+)\s+products',
        r'"brand"\s*:\s*"([^"]+)"',
        r'"vendor"\s*:\s*"([^"]+)"',
        r'"manufacturer"\s*:\s*"([^"]+)"',
    ]

    for pattern in vendor_patterns:
        for m in re.finditer(pattern, source, flags=re.IGNORECASE | re.DOTALL):
            if m.groups():
                out.append(m.group(1))
            else:
                out.append(m.group(0))

    for a in soup.find_all("a", href=True):
        txt = normalize_space(a.get_text(" ", strip=True))
        href = a.get("href", "")
        if txt and ("brand-shop" in href or "See all" in txt):
            out.append(txt)

    seen = set()
    deduped = []
    for item in out:
        item = normalize_space(item)
        if item and item not in seen:
            seen.add(item)
            deduped.append(item)

    return deduped


TITLE_CONTEXT_PATTERNS = [
    r"<title[^>]*>.*?</title>",
    r"<h1[^>]*>.*?</h1>",
    r'"title"\s*:\s*"[^"]+"',
    r'"name"\s*:\s*"[^"]+"',
    r'"productName"\s*:\s*"[^"]+"',
]

VENDOR_CONTEXT_PATTERNS = [
    r'>\s*From\s+[^<]+<',
    r'See all\s+[A-Za-z0-9 &._-]+\s+products',
    r'"brand"\s*:\s*"[^"]+"',
    r'"vendor"\s*:\s*"[^"]+"',
    r'"manufacturer"\s*:\s*"[^"]+"',
]

DESCRIPTION_CONTEXT_PATTERNS = [
    r'"description"\s*:\s*"[^"]+"',
    r'"longDescription"\s*:\s*"[^"]+"',
    r'"shortDescription"\s*:\s*"[^"]+"',
    r'"productDescription"\s*:\s*"[^"]+"',
    r'<meta[^>]+name="description"[^>]+content="[^"]+"',
    r'<meta[^>]+property="og:description"[^>]+content="[^"]+"',
]

FEATURES_CONTEXT_PATTERNS = [
    r'"features"\s*:\s*\[[^\]]+\]',
    r'"feature"\s*:\s*\[[^\]]+\]',
    r'"benefits"\s*:\s*\[[^\]]+\]',
    r'"bullets"\s*:\s*\[[^\]]+\]',
    r'"bulletText"\s*:\s*\[[^\]]+\]',
    r'"keyFeatures"\s*:\s*\[[^\]]+\]',
    r'<li[^>]*>.*?</li>',
]


def find_first_context(source: str, patterns: List[str], window: int = CONTEXT_WINDOW) -> Tuple[str, str]:
    source = str(source or "")
    if not source:
        return "", ""

    for pattern in patterns:
        match = re.search(pattern, source, flags=re.IGNORECASE | re.DOTALL)
        if match:
            start = max(0, match.start() - window)
            end = min(len(source), match.end() + window)
            anchor = safe_text(match.group(0))
            context = safe_text(source[start:end])
            return anchor, context

    return "", ""


def extract_fields_and_contexts(html_source: str) -> Dict[str, str]:
    soup = build_soup(html_source)

    title_candidates = extract_title_candidates(soup)
    vendor_candidates = extract_vendor_candidates(html_source, soup)
    desc_candidates = extract_description_candidates(soup)
    feature_candidates = extract_feature_candidates(html_source, soup)

    title_extracted = title_candidates[0] if title_candidates else ""
    vendor_extracted = vendor_candidates[0] if vendor_candidates else ""
    description_extracted = desc_candidates[0] if desc_candidates else ""

    # fallback to visible page text slice if no meta description
    if not description_extracted:
        description_extracted = get_clean_page_text(soup)[:3000].strip()

    features_extracted = " | ".join(feature_candidates[:25])

    title_anchor, title_source_context = find_first_context(html_source, TITLE_CONTEXT_PATTERNS)
    vendor_anchor, vendor_source_context = find_first_context(html_source, VENDOR_CONTEXT_PATTERNS)
    description_anchor, description_source_context = find_first_context(html_source, DESCRIPTION_CONTEXT_PATTERNS)
    features_anchor, features_source_context = find_first_context(html_source, FEATURES_CONTEXT_PATTERNS)

    return {
        "title_extracted": title_extracted,
        "vendor_extracted": vendor_extracted,
        "description_extracted": description_extracted,
        "features_extracted": features_extracted,
        "title_anchor": title_anchor,
        "title_source_context": title_source_context,
        "vendor_anchor": vendor_anchor,
        "vendor_source_context": vendor_source_context,
        "description_anchor": description_anchor,
        "description_source_context": description_source_context,
        "features_anchor": features_anchor,
        "features_source_context": features_source_context,
    }


def get_images_from_html(html_source: str) -> List[str]:
    try:
        soup = build_soup(html_source)
        images = soup.find_all("img")
        image_urls = []
        for img in images:
            src = img.get("src")
            if src and "http" in src:
                image_urls.append(src.strip())
        return sorted(set(image_urls))
    except Exception:
        return []


def validate_columns(df: pd.DataFrame):
    cols = {c.strip().lower(): c for c in df.columns}
    required = ["salsify_url", "retail_url"]
    missing = [col for col in required if col not in cols]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")
    return cols


def make_excel_bytes(results_df: pd.DataFrame) -> bytes:
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        results_df.to_excel(writer, index=False, sheet_name="debugger")
    output.seek(0)
    return output.getvalue()


def run_qa(df: pd.DataFrame) -> pd.DataFrame:
    cols = validate_columns(df)

    salsify_col = cols["salsify_url"]
    retail_col = cols["retail_url"]
    sku_col = cols.get("sku")
    rpc_col = cols.get("cvs rpc")
    brand_col = cols.get("brand")

    results = []

    progress = st.progress(0.0)
    status_box = st.empty()
    total = len(df)

    for i, (_, row) in enumerate(df.iterrows(), start=1):
        salsify_url = str(row[salsify_col]).strip() if pd.notna(row[salsify_col]) else ""
        retail_url = str(row[retail_col]).strip() if pd.notna(row[retail_col]) else ""
        sku = str(row[sku_col]).strip() if sku_col and pd.notna(row[sku_col]) else ""
        cvs_rpc = str(row[rpc_col]).strip() if rpc_col and pd.notna(row[rpc_col]) else ""
        brand = str(row[brand_col]).strip() if brand_col and pd.notna(row[brand_col]) else ""

        status_box.write(f"Processing row {i} of {total}: CVS RPC = {cvs_rpc or '(blank)'}")

        s_status = 0
        r_status = 0
        s_final_url = ""
        r_final_url = ""
        s_html = ""
        r_html = ""
        s_text = ""
        s_images = []
        r_images = []
        desc_score = 0
        feat_score = 0
        image_match_pct = 0.0
        match_count = 0
        total_salsify = 0
        app_status = "ERROR"
        error_text = ""

        extracted = {
            "title_extracted": "",
            "vendor_extracted": "",
            "description_extracted": "",
            "features_extracted": "",
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
            # Salsify
            s_status, s_final_url, s_html = fetch_page(salsify_url)
            if s_status != 200:
                raise RuntimeError(f"Salsify HTTP {s_status}")

            # Retail/CVS
            r_status, r_final_url, r_html = fetch_page(retail_url)
            if r_status != 200:
                raise RuntimeError(f"Retail HTTP {r_status}")

            # Extract
            s_soup = build_soup(s_html)
            s_text = get_clean_page_text(s_soup)

            extracted = extract_fields_and_contexts(r_html)

            s_images = get_images_from_html(s_html)
            r_images = get_images_from_html(r_html)

            s_set = set(s_images)
            r_set = set(r_images)
            match_count = len(s_set & r_set)
            total_salsify = len(s_set)
            image_match_pct = round((match_count / total_salsify) * 100, 2) if total_salsify else 0.0

            desc_score = similarity_score(s_text, extracted["description_extracted"])
            feat_score = similarity_score(s_text, extracted["features_extracted"])

            app_status = "PASS" if desc_score > 85 and feat_score > 80 and image_match_pct > 50 else "FAIL"

        except Exception as exc:
            error_text = f"{type(exc).__name__}: {exc}"

        row_dict = {
            "sku": sku,
            "cvs_rpc": cvs_rpc,
            "brand": brand,
            "salsify_url": salsify_url,
            "retail_url": retail_url,
            "salsify_final_url": s_final_url,
            "retail_final_url": r_final_url,
            "salsify_status_code": s_status,
            "retail_status_code": r_status,
            "desc_score": desc_score,
            "feat_score": feat_score,
            "image_match_pct": image_match_pct,
            "matching_image_count": match_count,
            "salsify_image_count": total_salsify,
            "status": app_status,
            "error": error_text,
            "salsify_text": s_text,
            "salsify_images": " | ".join(s_images),
            "retail_images": " | ".join(r_images),
            "source_bytes": len(r_html.encode("utf-8", errors="ignore")) if r_html else 0,
            "source_length": len(r_html) if r_html else 0,
            **extracted,
        }

        source_chunks = chunk_text(r_html)
        for idx_chunk, chunk in enumerate(source_chunks, start=1):
            row_dict[f"raw_source_{idx_chunk}"] = chunk

        results.append(row_dict)
        progress.progress(i / total)

    progress.empty()
    status_box.empty()
    return pd.DataFrame(results)


def main():
    st.set_page_config(page_title="PDP QA Tool", layout="wide")
    st.title("PDP QA Tool")

    uploaded_file = st.file_uploader("Upload CSV", type=["csv"])

    if uploaded_file is None:
        st.info("Upload a CVS CSV file to begin.")
        st.stop()

    try:
        df = pd.read_csv(uploaded_file)
    except Exception as exc:
        st.error(f"Could not read uploaded CSV: {exc}")
        st.stop()

    st.write("Preview of uploaded data:")
    st.dataframe(df.head(), width="stretch")

    if st.button("Run Extraction"):
        with st.spinner("Fetching CVS pages and extracting title, vendor, description, and features..."):
            try:
                results_df = run_qa(df)
            except Exception as exc:
                st.error(f"Extraction run failed: {exc}")
                st.stop()

        st.success("Extraction run complete.")
        st.dataframe(results_df.head(50), width="stretch")

        excel_bytes = make_excel_bytes(results_df)
        st.download_button(
            label="Download Debugger Excel",
            data=excel_bytes,
            file_name="cvs_debugger_with_source.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )


if __name__ == "__main__":
    main()
