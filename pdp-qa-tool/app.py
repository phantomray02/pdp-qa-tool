import io
import json
import re
from difflib import SequenceMatcher
from html import unescape
from urllib.parse import urljoin

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
}

REQUEST_TIMEOUT = 15
SECTION_WINDOW = 4500

NOISE_PHRASES = [
    "manage prescriptions",
    "schedule a vaccine",
    "weekly ad",
    "coupons rewards",
    "minuteclinic",
    "oak street health",
    "accessibility statement",
    "privacy policy",
    "terms of use",
    "shipping restrictions",
    "same-day delivery policies",
    "back to top",
    "sign in",
    "cart",
    "menu",
    "wellness zone",
    "help center",
    "contact us",
    "return policy",
    "store locator",
    "careers",
    "social responsibility",
    "news room",
    "real estate",
    "supplier program",
    "additional resources",
    "patient authorization forms",
    "notice of privacy practices",
    "drug information center",
    "order cancellation policy",
    "language assistance",
    "non-discrimination policy",
    "refill prescriptions",
    "transfer prescriptions",
    "patient forms",
    "ethics",
    "human rights",
    "media exchange",
    "supplier",
    "privacy practices",
    "extra big deals",
    "use the cvs app",
]

FEATURE_HINTS = [
    "3x",
    "strong",
    "stronger",
    "soft",
    "softest",
    "comfort",
    "absorbent",
    "absorbency",
    "cleaningripples",
    "septic",
    "flushable",
    "hypoallergenic",
    "dermatologist",
    "tested",
    "no perfumes",
    "no dyes",
    "95% water",
    "95 water",
    "aloe",
    "vitamin e",
    "chamomile",
    "odorblock",
    "dryshield",
    "moisturewick",
    "moisture-wicking",
    "hsa",
    "fsa",
    "alcohol free",
    "free of added perfumes",
    "free of added dyes",
    "maximum absorbency",
    "light absorbency",
    "ultra clean",
    "ultra comfort",
    "ultra soft",
    "trusted care",
    "soothing lotion",
    "cooling aloe",
    "mega rolls",
    "wipes",
    "underwear",
    "guards",
    "shields",
    "tissues",
    "fragrance free",
    "gentleplus",
    "fresh care",
]

TITLE_PATTERNS = [
    r"<h1[^>]*>.*?</h1>",
    r"<title[^>]*>.*?</title>",
    r'"productName"\s*:\s*"[^"]+"',
    r'"name"\s*:\s*"[^"]+"',
    r'"title"\s*:\s*"[^"]+"',
    r'<meta[^>]+property="og:title"[^>]+content="[^"]+"',
]

DESCRIPTION_PATTERNS = [
    r'"longDescription"\s*:\s*"[^"]+"',
    r'"shortDescription"\s*:\s*"[^"]+"',
    r'"description"\s*:\s*"[^"]+"',
    r'"productDescription"\s*:\s*"[^"]+"',
    r'<meta[^>]+name="description"[^>]+content="[^"]+"',
    r'<meta[^>]+property="og:description"[^>]+content="[^"]+"',
    r"Details",
    r"Specifications",
    r"Directions",
    r"Warnings",
    r"Ingredients",
]

FEATURE_ARRAY_PATTERNS = [
    r'"features"\s*:\s*\[([^\]]+)\]',
    r'"feature"\s*:\s*\[([^\]]+)\]',
    r'"benefits"\s*:\s*\[([^\]]+)\]',
    r'"bullets"\s*:\s*\[([^\]]+)\]',
    r'"bulletText"\s*:\s*\[([^\]]+)\]',
    r'"keyFeatures"\s*:\s*\[([^\]]+)\]',
]

DETAILS_PATTERNS = [
    r"Details",
    r"Specifications",
    r"Directions",
    r"Warnings",
    r"Ingredients",
]

SOURCE_TITLE_ALIASES = [
    "product name", "title", "product_title", "salsify title", "salsify_title", "name"
]
SOURCE_DESCRIPTION_ALIASES = [
    "description", "product description", "long description", "long_description",
    "salsify description", "salsify_description"
]
SOURCE_FEATURE_ALIASES = [
    "features", "feature", "bullets", "benefits", "key features", "key_features",
    "general feature 1", "general feature 2", "general feature 3", "general feature 4", "general feature 5",
    "salsify features", "salsify_features"
]
SOURCE_IMAGE_ALIASES = [
    "image", "image url", "image_url", "primary image", "primary_image", "main image", "main_image"
]


def normalize_space(text):
    text = str(text or "")
    text = unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def dedupe_preserve_order(items):
    output = []
    seen = set()
    for item in items:
        item = normalize_space(item)
        if item and item not in seen:
            seen.add(item)
            output.append(item)
    return output


def contains_noise(text):
    lowered = normalize_space(text).lower()
    if not lowered:
        return True
    return any(phrase in lowered for phrase in NOISE_PHRASES)


def simple_sentence_split(text):
    text = normalize_space(text)
    if not text:
        return []
    parts = re.split(r"(?<=[\.!\?])\s+|\s+\|\s+|;\s+|\s{2,}", text)
    return [normalize_space(p) for p in parts if normalize_space(p)]


def clean_section_lines(section_text):
    text = normalize_space(section_text)
    if not text:
        return []

    text = re.sub(r"<[^>]+>", " ", text)
    text = normalize_space(text)

    lines = simple_sentence_split(text)
    output = []

    for line in lines:
        lowered = line.lower()
        if len(line) < 8:
            continue
        if contains_noise(line):
            continue
        if lowered in {"details", "specifications", "directions", "warnings", "ingredients"}:
            continue
        if lowered in {"home", "shop", "household", "paper & plastic", "toilet paper"}:
            continue
        output.append(line)

    return dedupe_preserve_order(output)


def fetch_page(session, url):
    if not url or not str(url).strip():
        return 0, "", ""
    response = session.get(
        url,
        headers=HEADERS,
        timeout=REQUEST_TIMEOUT,
        allow_redirects=True,
    )
    return response.status_code, response.url, response.text


def build_soup(html_source):
    return BeautifulSoup(html_source or "", "html.parser")


def clean_page_text(soup):
    soup_copy = BeautifulSoup(str(soup), "html.parser")
    for tag in soup_copy(["script", "style", "noscript"]):
        tag.decompose()
    return normalize_space(soup_copy.get_text(separator=" ", strip=True))


def make_excel_bytes(results_df):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        results_df.to_excel(writer, index=False, sheet_name="comparison")
    output.seek(0)
    return output.getvalue()


def pull_big_section(source, patterns, window=SECTION_WINDOW):
    source = str(source or "")
    if not source:
        return ""
    for pattern in patterns:
        match = re.search(pattern, source, flags=re.IGNORECASE | re.DOTALL)
        if match:
            start = max(0, match.start() - window)
            end = min(len(source), match.end() + window)
            return normalize_space(source[start:end])
    return ""


def parse_jsonld_blocks(soup):
    blocks = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text() or ""
        raw = raw.strip()
        if not raw:
            continue
        try:
            blocks.append(json.loads(raw))
        except Exception:
            continue
    return blocks


def iter_json_nodes(obj):
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from iter_json_nodes(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from iter_json_nodes(item)


def normalize_image_url(image_url, final_url):
    image_url = normalize_space(image_url)
    if not image_url:
        return ""
    image_url = image_url.replace("http://localhost:4300", "https://www.cvs.com")
    image_url = image_url.replace("https://localhost:4300", "https://www.cvs.com")
    return urljoin(final_url, image_url)


def extract_from_jsonld(soup, final_url):
    title = ""
    description = ""
    features = []
    image = ""

    for block in parse_jsonld_blocks(soup):
        for node in iter_json_nodes(block):
            node_type = str(node.get("@type", "")).lower()
            if "product" in node_type or node.get("name") or node.get("description"):
                if not title and node.get("name"):
                    title = normalize_space(node.get("name"))
                if not description and node.get("description"):
                    description = normalize_space(node.get("description"))
                if not image and node.get("image"):
                    img = node.get("image")
                    if isinstance(img, list) and img:
                        image = normalize_image_url(str(img[0]), final_url)
                    else:
                        image = normalize_image_url(str(img), final_url)
                for key in ["features", "feature", "benefits", "bullets", "bulletText", "keyFeatures"]:
                    if key in node:
                        value = node[key]
                        if isinstance(value, list):
                            for item in value:
                                item = normalize_space(item)
                                if item and not contains_noise(item):
                                    features.append(item)
                        else:
                            value = normalize_space(value)
                            if value and not contains_noise(value):
                                features.append(value)

    return {
        "title": title,
        "description": description,
        "features": " | ".join(dedupe_preserve_order(features)[:25]),
        "image": image,
    }


def extract_title(soup, html_source, final_url):
    h1 = soup.find("h1")
    if h1:
        text = normalize_space(h1.get_text(" ", strip=True))
        if text:
            return text, "h1"
    jsonld = extract_from_jsonld(soup, final_url)
    if jsonld["title"]:
        return jsonld["title"], "jsonld_title"
    og_title = soup.find("meta", attrs={"property": "og:title"})
    if og_title and og_title.get("content"):
        return normalize_space(og_title.get("content")), "og_title"
    title_tag = soup.find("title")
    if title_tag:
        text = normalize_space(title_tag.get_text(" ", strip=True))
        if text:
            return text, "html_title"
    section = pull_big_section(html_source, TITLE_PATTERNS)
    lines = clean_section_lines(section)
    if lines:
        return lines[0], "title_section_line"
    return "", "title_empty"


def extract_description(soup, html_source, final_url):
    jsonld = extract_from_jsonld(soup, final_url)
    if jsonld["description"]:
        return jsonld["description"], "jsonld_description"

    details_section = pull_big_section(html_source, DETAILS_PATTERNS)
    details_lines = clean_section_lines(details_section)
    details_keep = []
    for line in details_lines:
        lowered = line.lower()
        if any(hint in lowered for hint in FEATURE_HINTS):
            details_keep.append(line)
        elif len(line) >= 25 and not contains_noise(line):
            details_keep.append(line)
    details_keep = dedupe_preserve_order(details_keep)
    if details_keep:
        return " ".join(details_keep[:8]), "details_section_lines"

    meta_desc = soup.find("meta", attrs={"name": "description"})
    if meta_desc and meta_desc.get("content"):
        text = normalize_space(meta_desc.get("content"))
        if text:
            return text, "meta_description"

    og_desc = soup.find("meta", attrs={"property": "og:description"})
    if og_desc and og_desc.get("content"):
        text = normalize_space(og_desc.get("content"))
        if text:
            return text, "og_description"

    section = pull_big_section(html_source, DESCRIPTION_PATTERNS)
    lines = clean_section_lines(section)
    if lines:
        return " ".join(lines[:6]), "description_section_lines"

    fallback = clean_page_text(soup)[:2500]
    return fallback, "visible_text_fallback"


def synthesize_features_from_text(title_text, description_text, details_text):
    combined = " ".join([
        normalize_space(title_text),
        normalize_space(description_text),
        normalize_space(details_text),
    ]).lower()
    features = []

    phrase_patterns = [
        r"3x [a-z\- ]+",
        r"septic[- ]safe",
        r"hypoallergenic",
        r"dermatologist[- ]tested",
        r"alcohol[- ]free",
        r"fragrance[- ]free",
        r"moisture[- ]wicking",
        r"maximum absorbency",
        r"light absorbency",
        r"softest",
        r"stronger",
        r"strong",
        r"comfort",
        r"flushable",
        r"odorblock",
        r"dryshield",
        r"moisturewick",
        r"cleaningripples",
        r"aloe",
        r"vitamin e",
        r"chamomile",
    ]
    for pattern in phrase_patterns:
        for match in re.finditer(pattern, combined, flags=re.IGNORECASE):
            features.append(normalize_space(match.group(0)))

    count_patterns = [
        r"\b\d+\s*mega rolls?\b",
        r"\b\d+\s*ct\b",
        r"\b\d+\s*count\b",
        r"\b\d+\s*wipes?\b",
        r"\b\d+\s*pk\b",
        r"\b\d+\s*total wipes\b",
        r"\bsmall\/medium\b",
        r"\bx-large\b",
        r"\bxx-large\b",
        r"\blarge\b",
        r"\bmedium\b",
        r"\bsmall\b",
        r"\bgrey\b",
        r"\bgray\b",
        r"\bblush\b",
    ]
    for pattern in count_patterns:
        for match in re.finditer(pattern, combined, flags=re.IGNORECASE):
            features.append(normalize_space(match.group(0)))

    return " | ".join(dedupe_preserve_order(features)[:20])


def extract_features(soup, html_source, final_url, title_text, description_text):
    jsonld = extract_from_jsonld(soup, final_url)
    if jsonld["features"]:
        return jsonld["features"], "jsonld_features"

    for pattern in FEATURE_ARRAY_PATTERNS:
        match = re.search(pattern, html_source, flags=re.IGNORECASE | re.DOTALL)
        if match:
            payload = normalize_space(match.group(1))
            if payload and not contains_noise(payload):
                return payload, "features_json_array"

    details_section = pull_big_section(html_source, DETAILS_PATTERNS)
    features_section = pull_big_section(
        html_source,
        FEATURE_ARRAY_PATTERNS + DETAILS_PATTERNS + [r"<li[^>]*>.*?</li>"]
    )
    source_for_features = f"{features_section} {details_section}"

    bullets = []
    section_soup = build_soup(source_for_features)
    seen = set()
    for li in section_soup.find_all("li"):
        text = normalize_space(li.get_text(" ", strip=True))
        lowered = text.lower()
        if not text:
            continue
        if len(text) < 20:
            continue
        if contains_noise(text):
            continue
        if not any(hint in lowered for hint in FEATURE_HINTS):
            continue
        if text not in seen:
            seen.add(text)
            bullets.append(text)
    if bullets:
        return " | ".join(bullets[:20]), "features_html_bullets"

    lines = clean_section_lines(source_for_features)
    keep = []
    for line in lines:
        lowered = line.lower()
        if any(hint in lowered for hint in FEATURE_HINTS):
            keep.append(line)
    keep = dedupe_preserve_order(keep)
    if keep:
        return " | ".join(keep[:20]), "features_section_lines"

    synthesized = synthesize_features_from_text(title_text, description_text, details_section)
    if synthesized:
        return synthesized, "features_synthesized"

    return "", "features_empty"


def extract_image(soup, final_url):
    jsonld = extract_from_jsonld(soup, final_url)
    if jsonld["image"]:
        return jsonld["image"], "jsonld_image"
    og_image = soup.find("meta", attrs={"property": "og:image"})
    if og_image and og_image.get("content"):
        return normalize_image_url(og_image.get("content"), final_url), "og_image"
    twitter_image = soup.find("meta", attrs={"name": "twitter:image"})
    if twitter_image and twitter_image.get("content"):
        return normalize_image_url(twitter_image.get("content"), final_url), "twitter_image"
    for img in soup.find_all("img"):
        src = img.get("src")
        if src and "productimages" in src:
            return normalize_image_url(src, final_url), "img_tag"
    return "", "image_empty"


def normalize_compare_text(text):
    text = normalize_space(text).lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def string_similarity(a, b):
    a = normalize_compare_text(a)
    b = normalize_compare_text(b)
    if not a and not b:
        return 100
    if not a or not b:
        return 0
    return round(100 * SequenceMatcher(None, a, b).ratio())


def token_overlap_score(a, b):
    a_tokens = set(normalize_compare_text(a).split())
    b_tokens = set(normalize_compare_text(b).split())
    if not a_tokens and not b_tokens:
        return 100
    if not a_tokens or not b_tokens:
        return 0
    return round(100 * len(a_tokens & b_tokens) / len(a_tokens | b_tokens))


def detect_column(df, aliases):
    cols = {c.strip().lower(): c for c in df.columns}
    for alias in aliases:
        if alias in cols:
            return cols[alias]
    for c in df.columns:
        cl = c.strip().lower()
        for alias in aliases:
            if alias in cl:
                return c
    return None


def collect_source_features(row, feature_cols):
    values = []
    for col in feature_cols:
        if col in row and pd.notna(row[col]):
            val = normalize_space(row[col])
            if val:
                values.append(val)
    return " | ".join(dedupe_preserve_order(values))


def compare_status(title_score, desc_score, feat_score, img_match):
    passes = sum([
        title_score >= 70,
        desc_score >= 50,
        feat_score >= 35,
        img_match,
    ])
    return "PASS" if passes >= 3 else "FAIL"


def validate_columns(df):
    cols = {c.strip().lower(): c for c in df.columns}
    if "retail_url" not in cols:
        raise ValueError("Missing required column: retail_url")
    return cols


def process_items(df, max_rows):
    cols = validate_columns(df)
    retail_col = cols["retail_url"]
    sku_col = cols.get("sku")
    rpc_col = cols.get("cvs rpc")

    source_title_col = detect_column(df, SOURCE_TITLE_ALIASES)
    source_description_col = detect_column(df, SOURCE_DESCRIPTION_ALIASES)
    source_image_col = detect_column(df, SOURCE_IMAGE_ALIASES)
    source_feature_cols = []
    for alias in SOURCE_FEATURE_ALIASES:
        found = detect_column(df, [alias])
        if found and found not in source_feature_cols:
            source_feature_cols.append(found)

    work_df = df.head(max_rows).copy()
    results = []

    session = requests.Session()
    session.headers.update(HEADERS)

    progress = st.progress(0.0)
    status_box = st.empty()
    total = len(work_df)

    for i, (_, row) in enumerate(work_df.iterrows(), start=1):
        retail_url = str(row[retail_col]).strip() if pd.notna(row[retail_col]) else ""
        sku = str(row[sku_col]).strip() if sku_col and pd.notna(row[sku_col]) else ""
        cvs_rpc = str(row[rpc_col]).strip() if rpc_col and pd.notna(row[rpc_col]) else ""

        source_title = normalize_space(row[source_title_col]) if source_title_col and pd.notna(row[source_title_col]) else ""
        source_description = normalize_space(row[source_description_col]) if source_description_col and pd.notna(row[source_description_col]) else ""
        source_features = collect_source_features(row, source_feature_cols)
        source_image = normalize_space(row[source_image_col]) if source_image_col and pd.notna(row[source_image_col]) else ""

        status_box.write(f"Processing row {i} of {total}: CVS RPC = {cvs_rpc or '(blank)'}")

        status_code = 0
        final_url = ""
        html_source = ""
        source_capture_status = "failed"
        source_capture_error = ""

        title_extracted = ""
        description_extracted = ""
        features_extracted = ""
        image_extracted = ""

        title_extraction_path = ""
        description_extraction_path = ""
        features_extraction_path = ""
        image_extraction_path = ""

        title_score = 0
        description_score = 0
        features_score = 0
        image_match = False
        compare_status_value = "FAIL"

        try:
            status_code, final_url, html_source = fetch_page(session, retail_url)
            if status_code == 200:
                source_capture_status = "success"
                soup = build_soup(html_source)

                title_extracted, title_extraction_path = extract_title(soup, html_source, final_url)
                description_extracted, description_extraction_path = extract_description(soup, html_source, final_url)
                features_extracted, features_extraction_path = extract_features(
                    soup,
                    html_source,
                    final_url,
                    title_extracted,
                    description_extracted,
                )
                image_extracted, image_extraction_path = extract_image(soup, final_url)

                title_score = string_similarity(source_title, title_extracted)
                description_score = token_overlap_score(source_description, description_extracted)
                features_score = token_overlap_score(source_features, features_extracted)

                if source_image and image_extracted:
                    image_match = normalize_compare_text(source_image).split("?")[0] in normalize_compare_text(image_extracted)
                else:
                    image_match = False

                compare_status_value = compare_status(title_score, description_score, features_score, image_match)
            else:
                source_capture_error = f"http_{status_code}"
        except Exception as exc:
            source_capture_error = f"{type(exc).__name__}: {exc}"

        results.append({
            "sku": sku,
            "cvs_rpc": cvs_rpc,
            "retail_url": retail_url,
            "final_url": final_url,
            "status_code": status_code,
            "source_capture_status": source_capture_status,
            "source_capture_error": source_capture_error,
            "source_title": source_title,
            "source_description": source_description,
            "source_features": source_features,
            "source_image": source_image,
            "title_extracted": title_extracted,
            "description_extracted": description_extracted,
            "features_extracted": features_extracted,
            "image_extracted": image_extracted,
            "title_extraction_path": title_extraction_path,
            "description_extraction_path": description_extraction_path,
            "features_extraction_path": features_extraction_path,
            "image_extraction_path": image_extraction_path,
            "title_score": title_score,
            "description_score": description_score,
            "features_score": features_score,
            "image_match": image_match,
            "compare_status": compare_status_value,
        })

        progress.progress(i / total)

    progress.empty()
    status_box.empty()
    return pd.DataFrame(results)


def main():
    st.set_page_config(page_title="PDP QA Comparison Tool", layout="wide")
    st.title("PDP QA Comparison Tool")

    uploaded_file = st.file_uploader("Upload source CSV", type=["csv"])

    if uploaded_file is None:
        st.info("Upload a CSV with at least a retail_url column and your source title/description/features/image fields.")
        st.stop()

    try:
        df = pd.read_csv(uploaded_file)
    except Exception as exc:
        st.error(f"Could not read uploaded CSV: {exc}")
        st.stop()

    st.write("Preview of uploaded data:")
    st.dataframe(df.head(), width="stretch")

    max_rows = st.number_input(
        "Rows to process",
        min_value=1,
        max_value=len(df),
        value=min(100, len(df)),
        step=1,
    )

    if st.button("Run Comparison"):
        with st.spinner("Fetching CVS pages, extracting live PDP copy, and comparing against your source fields..."):
            try:
                results_df = process_items(df, int(max_rows))
            except Exception as exc:
                st.error(f"Run failed: {exc}")
                st.stop()

        st.success("Comparison complete.")
        st.dataframe(results_df.head(50), width="stretch")

        excel_bytes = make_excel_bytes(results_df)
        st.download_button(
            label="Download Comparison Excel",
            data=excel_bytes,
            file_name="pdp_qa_comparison.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )


if __name__ == "__main__":
    main()
