import io
import re
from difflib import SequenceMatcher
from typing import List, Tuple

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
}

REQUEST_TIMEOUT = 25
EXCEL_CELL_LIMIT = 30000
CONTEXT_WINDOW = 1200


def normalize_space(text: str) -> str:
    text = str(text or "")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def safe_text(text: str) -> str:
    return normalize_space(text).replace("\x00", "")


def chunk_text(text: str, chunk_size: int = EXCEL_CELL_LIMIT) -> list:
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


def fetch_html(url: str) -> str:
    if not url or not str(url).strip():
        return ""
    resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.text


def get_soup_from_html(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def extract_page_text_from_soup(soup: BeautifulSoup) -> str:
    soup = BeautifulSoup(str(soup), "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(separator=" ", strip=True)
    return normalize_space(text)


def extract_title_from_soup(soup: BeautifulSoup) -> str:
    h1 = soup.find("h1")
    if h1:
        return normalize_space(h1.get_text(" ", strip=True))

    title_tag = soup.find("title")
    if title_tag:
        return normalize_space(title_tag.get_text(" ", strip=True))

    og_title = soup.find("meta", attrs={"property": "og:title"})
    if og_title and og_title.get("content"):
        return normalize_space(og_title.get("content"))

    return ""


def extract_meta_description_from_soup(soup: BeautifulSoup) -> str:
    meta = soup.find("meta", attrs={"name": "description"})
    if meta and meta.get("content"):
        return normalize_space(meta.get("content"))

    og_desc = soup.find("meta", attrs={"property": "og:description"})
    if og_desc and og_desc.get("content"):
        return normalize_space(og_desc.get("content"))

    return ""


def extract_feature_bullets_from_soup(soup: BeautifulSoup) -> str:
    bullets = []

    for li in soup.find_all("li"):
        txt = normalize_space(li.get_text(" ", strip=True))
        if len(txt) >= 20:
            bullets.append(txt)

    # dedupe while preserving order
    seen = set()
    deduped = []
    for item in bullets:
        if item not in seen:
            seen.add(item)
            deduped.append(item)

    return " | ".join(deduped[:25])


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
            return safe_text(anchor), safe_text(context)

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


def get_images_from_html(html: str) -> List[str]:
    try:
        soup = get_soup_from_html(html)
        images = soup.find_all("img")
        image_urls = []
        for img in images:
            src = img.get("src")
            if src and "http" in src:
                image_urls.append(src.strip())
        return sorted(set(image_urls))
    except Exception:
        return []


def get_salsify_data(html: str) -> str:
    try:
        soup = get_soup_from_html(html)
        title = extract_title_from_soup(soup)
        meta_desc = extract_meta_description_from_soup(soup)
        page_text = extract_page_text_from_soup(soup)
        combined = " ".join([title, meta_desc, page_text])
        return normalize_space(combined)
    except Exception:
        return ""


def get_cvs_data(html: str) -> Tuple[str, str]:
    try:
        soup = get_soup_from_html(html)
        title = extract_title_from_soup(soup)
        meta_desc = extract_meta_description_from_soup(soup)
        features = extract_feature_bullets_from_soup(soup)
        page_text = extract_page_text_from_soup(soup)

        description_text = normalize_space(" ".join([title, meta_desc, page_text[:3000]]))

        if not features:
            features = normalize_space(page_text[:2500])

        return description_text, features
    except Exception:
        return "", ""


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

    progress = st.progress(0)
    total = len(df)

    for i, (_, row) in enumerate(df.iterrows(), start=1):
        salsify_url = str(row[salsify_col]).strip() if pd.notna(row[salsify_col]) else ""
        retail_url = str(row[retail_col]).strip() if pd.notna(row[retail_col]) else ""
        sku = str(row[sku_col]).strip() if sku_col and pd.notna(row[sku_col]) else ""
        cvs_rpc = str(row[rpc_col]).strip() if rpc_col and pd.notna(row[rpc_col]) else ""
        brand = str(row[brand_col]).strip() if brand_col and pd.notna(row[brand_col]) else ""

        s_html = ""
        r_html = ""
        s_text = ""
        cvs_desc = ""
        cvs_features = ""
        s_images = []
        r_images = []
        s_status = ""
        r_status = ""
        error_text = ""

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
            # Salsify fetch
            s_resp = requests.get(salsify_url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            s_status = s_resp.status_code
            s_resp.raise_for_status()
            s_html = s_resp.text

            # CVS fetch
            r_resp = requests.get(retail_url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            r_status = r_resp.status_code
            r_resp.raise_for_status()
            r_html = r_resp.text

            s_text = get_salsify_data(s_html)
            cvs_desc, cvs_features = get_cvs_data(r_html)
            contexts = extract_source_contexts(r_html)

            s_images = get_images_from_html(s_html)
            r_images = get_images_from_html(r_html)

            s_set = set(s_images)
            r_set = set(r_images)
            match_count = len(s_set & r_set)
            total_salsify = len(s_set)
            image_match_pct = round((match_count / total_salsify) * 100, 2) if total_salsify else 0.0

            desc_score = similarity_score(s_text, cvs_desc)
            feat_score = similarity_score(s_text, cvs_features)

            status = "PASS" if desc_score > 85 and feat_score > 80 and image_match_pct > 50 else "FAIL"

        except Exception as exc:
            error_text = f"{type(exc).__name__}: {exc}"
            match_count = 0
            total_salsify = 0
            image_match_pct = 0.0
            desc_score = 0
            feat_score = 0
            status = "ERROR"

        source_chunks = chunk_text(r_html)
        row_dict = {
            "sku": sku,
            "cvs_rpc": cvs_rpc,
            "brand": brand,
            "salsify_url": salsify_url,
            "retail_url": retail_url,
            "salsify_status_code": s_status,
            "retail_status_code": r_status,
            "desc_score": desc_score,
            "feat_score": feat_score,
            "image_match_pct": image_match_pct,
            "matching_image_count": match_count,
            "salsify_image_count": total_salsify,
            "status": status,
            "error": error_text,
            "salsify_text": s_text,
            "cvs_description": cvs_desc,
            "cvs_features": cvs_features,
            "salsify_images": " | ".join(s_images),
            "retail_images": " | ".join(r_images),
            "title_anchor": contexts["title_anchor"],
            "title_source_context": contexts["title_source_context"],
            "vendor_anchor": contexts["vendor_anchor"],
            "vendor_source_context": contexts["vendor_source_context"],
            "description_anchor": contexts["description_anchor"],
            "description_source_context": contexts["description_source_context"],
            "features_anchor": contexts["features_anchor"],
            "features_source_context": contexts["features_source_context"],
            "source_bytes": len(r_html.encode('utf-8', errors='ignore')) if r_html else 0,
            "source_length": len(r_html) if r_html else 0,
        }

        for idx_chunk, chunk in enumerate(source_chunks, start=1):
            row_dict[f"raw_source_{idx_chunk}"] = chunk

        results.append(row_dict)
        progress.progress(i / total)

    progress.empty()
    return pd.DataFrame(results)


def main():
    st.set_page_config(page_title="PDP QA Tool", layout="wide")
    st.title("PDP QA Tool")

    uploaded_file = st.file_uploader("Upload CSV", type=["csv"])

    if uploaded_file is None:
        st.info("Upload a CSV file to begin.")
        st.stop()

    try:
        df = pd.read_csv(uploaded_file)
    except Exception as exc:
        st.error(f"Could not read uploaded CSV: {exc}")
        st.stop()

    st.write("Preview of uploaded data:")
    st.dataframe(df.head(), use_container_width=True)

    if st.button("Run QA"):
        with st.spinner("Running QA checks..."):
            try:
                results_df = run_qa(df)
            except Exception as exc:
                st.error(f"QA run failed: {exc}")
                st.stop()

        st.success("QA run complete.")
        st.dataframe(results_df.head(50), use_container_width=True)

        excel_bytes = make_excel_bytes(results_df)
        st.download_button(
            label="Download Excel Results",
            data=excel_bytes,
            file_name="pdp_qa_results.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )


if __name__ == "__main__":
    main()
