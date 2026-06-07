
import io
import re
from typing import List, Tuple

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup
from rapidfuzz import fuzz

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
}

REQUEST_TIMEOUT = 25


def normalize_space(text: str) -> str:
    text = str(text or "")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def fetch_html(url: str) -> str:
    if not url or not str(url).strip():
        return ""
    resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.text


def get_soup(url: str) -> BeautifulSoup:
    html = fetch_html(url)
    return BeautifulSoup(html, "html.parser")


def extract_page_text_from_soup(soup: BeautifulSoup) -> str:
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

    meta_og = soup.find("meta", attrs={"property": "og:title"})
    if meta_og and meta_og.get("content"):
        return normalize_space(meta_og.get("content"))

    return ""


def extract_meta_description_from_soup(soup: BeautifulSoup) -> str:
    meta = soup.find("meta", attrs={"name": "description"})
    if meta and meta.get("content"):
        return normalize_space(meta.get("content"))

    meta_og = soup.find("meta", attrs={"property": "og:description"})
    if meta_og and meta_og.get("content"):
        return normalize_space(meta_og.get("content"))

    return ""


def extract_feature_bullets_from_soup(soup: BeautifulSoup) -> str:
    bullets: List[str] = []

    for li in soup.find_all("li"):
        txt = normalize_space(li.get_text(" ", strip=True))
        if len(txt) >= 20:
            bullets.append(txt)

    # de-duplicate while preserving order
    seen = set()
    deduped = []
    for b in bullets:
        if b not in seen:
            seen.add(b)
            deduped.append(b)

    return " | ".join(deduped[:25])


def get_text(url: str) -> str:
    try:
        soup = get_soup(url)
        return extract_title_from_soup(soup)
    except Exception:
        return ""


def get_images(url: str) -> List[str]:
    try:
        soup = get_soup(url)
        images = soup.find_all("img")
        image_urls = []
        for img in images:
            src = img.get("src")
            if src and "http" in src:
                image_urls.append(src.strip())
        return sorted(set(image_urls))
    except Exception:
        return []


def get_salsify_data(url: str) -> str:
    try:
        soup = get_soup(url)
        title = extract_title_from_soup(soup)
        meta_desc = extract_meta_description_from_soup(soup)
        page_text = extract_page_text_from_soup(soup)
        combined = " ".join([title, meta_desc, page_text])
        return normalize_space(combined)
    except Exception:
        return ""


def get_cvs_data(url: str) -> Tuple[str, str]:
    try:
        soup = get_soup(url)

        title = extract_title_from_soup(soup)
        meta_desc = extract_meta_description_from_soup(soup)
        feature_bullets = extract_feature_bullets_from_soup(soup)
        page_text = extract_page_text_from_soup(soup)

        # description = title + meta description + trimmed page text
        description_text = normalize_space(" ".join([title, meta_desc, page_text[:3000]]))

        # features = bullet-like text if found, otherwise a trimmed fallback slice
        if not feature_bullets:
            feature_bullets = normalize_space(page_text[:2500])

        return description_text, feature_bullets
    except Exception:
        return "", ""


def validate_columns(df: pd.DataFrame):
    cols = {c.strip().lower(): c for c in df.columns}
    required = ["salsify_url", "retail_url"]
    missing = [col for col in required if col not in cols]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")
    return cols


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

        s_text = ""
        cvs_desc = ""
        cvs_features = ""
        s_images = []
        r_images = []
        error_text = ""

        try:
            s_text = get_salsify_data(salsify_url)
            cvs_desc, cvs_features = get_cvs_data(retail_url)

            s_images = get_images(salsify_url)
            r_images = get_images(retail_url)

            s_set = set(s_images)
            r_set = set(r_images)
            match_count = len(s_set & r_set)
            total_salsify = len(s_set)
            image_match_pct = round((match_count / total_salsify) * 100, 2) if total_salsify else 0.0

            desc_score = fuzz.partial_ratio(s_text, cvs_desc) if s_text and cvs_desc else 0
            feat_score = fuzz.partial_ratio(s_text, cvs_features) if s_text and cvs_features else 0

            status = "PASS" if desc_score > 85 and feat_score > 80 and image_match_pct > 50 else "FAIL"

            results.append({
                "sku": sku,
                "cvs_rpc": cvs_rpc,
                "brand": brand,
                "salsify_url": salsify_url,
                "retail_url": retail_url,
                "desc_score": desc_score,
                "feat_score": feat_score,
                "image_match_pct": image_match_pct,
                "matching_image_count": match_count,
                "salsify_image_count": total_salsify,
                "status": status,
                "salsify_text": s_text,
                "cvs_description": cvs_desc,
                "cvs_features": cvs_features,
                "salsify_images": " | ".join(s_images),
                "retail_images": " | ".join(r_images),
                "error": "",
            })

        except Exception as exc:
            error_text = f"{type(exc).__name__}: {exc}"
            results.append({
                "sku": sku,
                "cvs_rpc": cvs_rpc,
                "brand": brand,
                "salsify_url": salsify_url,
                "retail_url": retail_url,
                "desc_score": 0,
                "feat_score": 0,
                "image_match_pct": 0,
                "matching_image_count": 0,
                "salsify_image_count": 0,
                "status": "ERROR",
                "salsify_text": s_text,
                "cvs_description": cvs_desc,
                "cvs_features": cvs_features,
                "salsify_images": " | ".join(s_images),
                "retail_images": " | ".join(r_images),
                "error": error_text,
            })

        progress.progress(i / total)

    progress.empty()
    return pd.DataFrame(results)


def make_excel_bytes(results_df: pd.DataFrame) -> bytes:
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        results_df.to_excel(writer, index=False, sheet_name="QA Results")
    output.seek(0)
    return output.getvalue()


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
    st.dataframe(df.head())

    if st.button("Run QA"):
        with st.spinner("Running QA checks..."):
            try:
                results_df = run_qa(df)
            except Exception as exc:
                st.error(f"QA run failed: {exc}")
                st.stop()

        st.success("QA run complete.")
        st.dataframe(results_df, use_container_width=True)

        excel_bytes = make_excel_bytes(results_df)
        st.download_button(
            label="Download Excel Results",
            data=excel_bytes,
            file_name="pdp_qa_results.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )


if __name__ == "__main__":
    main()
