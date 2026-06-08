import re
import html
import json
import time
import hashlib
import traceback
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup
from openpyxl import load_workbook
from openpyxl.styles import PatternFill
from pandas.errors import EmptyDataError

# =========================================
# APP SETUP
# =========================================
st.set_page_config(layout="wide")
st.title("CVS Copy Extractor ✅")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
}

REQUEST_TIMEOUT = 8
MAX_CACHE = 300

# CVS-only app, so this can stay light.
BATCH_SIZE = 25
MAX_WORKERS = 3
UI_UPDATE_EVERY = 2

html_cache = {}

# =========================================
# GENERIC HELPERS
# =========================================
def normalize_space(text):
    text = str(text or "")
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def dedupe_preserve_order(items):
    seen = set()
    out = []
    for item in items:
        item = normalize_space(item)
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def read_uploaded_csv_from_bytes(file_bytes):
    if not file_bytes:
        raise EmptyDataError("Uploaded file is empty.")
    if len(file_bytes.strip()) == 0:
        raise EmptyDataError("Uploaded file is empty.")

    last_error = None
    for encoding in ["utf-8-sig", "utf-8", "latin1"]:
        try:
            return pd.read_csv(BytesIO(file_bytes), encoding=encoding)
        except Exception as e:
            last_error = e

    raise last_error if last_error else EmptyDataError("Could not parse uploaded CSV.")


def prepare_input_df(df):
    df = df.copy()
    df.columns = [c.strip().lower() for c in df.columns]

    df.rename(
        columns={
            "retail url": "retail_url",
            "sku id": "sku",
            "product sku": "sku",
            "cvs rpc": "cvs_rpc",
        },
        inplace=True,
    )

    required = ["retail_url"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")

    return df


def clear_html_cache():
    html_cache.clear()

# =========================================
# HTML FETCH
# =========================================
def get_html(url):
    if not url:
        return ""

    if url in html_cache:
        html_cache[url] = html_cache.pop(url)
        return html_cache[url]

    try:
        r = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        if r.status_code == 200 and r.text:
            html_cache[url] = r.text
            while len(html_cache) > MAX_CACHE:
                html_cache.pop(next(iter(html_cache)))
            return r.text
    except Exception:
        pass

    return ""

# =========================================
# CVS PARSERS
# =========================================
def clean_cvs_text(text):
    if not text:
        return ""

    text = str(text)

    text = text.replace("\\u0026", "&")
    text = text.replace("\\n", " ")
    text = text.replace("\\/", "/")
    text = text.replace('\\"', '"')

    text = html.unescape(text)

    # Remove embedded Next.js chunk wrappers that split description text.
    text = re.sub(
        r'"\]\)\s*</script>\s*<script>\s*self\.__next_f\.push\(\[1,\s*"',
        "",
        text,
        flags=re.DOTALL,
    )
    text = re.sub(
        r'"\]\)&lt;/script&gt;&lt;script&gt;self\.__next_f\.push\(\[1,\s*"',
        "",
        text,
        flags=re.DOTALL,
    )
    text = re.sub(
        r'"\]\)&lt;\/script&gt;&lt;script&gt;self\.__next_f\.push\(\[1,\s*"',
        "",
        text,
        flags=re.DOTALL,
    )

    text = re.sub(
        r'"\]\)\s*self\.__next_f\.push\(\[1,\s*"',
        "",
        text,
        flags=re.DOTALL,
    )

    text = re.sub(r'^(?:T[0-9A-Za-z]+,)+', "", text)
    text = re.sub(r"\s+", " ", text)

    return text.strip(' \t\r\n"')


def get_nextjs_chunks(html_text):
    """
    Decode all self.__next_f.push([1,"..."]) chunks into one combined source string.
    """
    if not html_text:
        return ""

    source = html.unescape(html_text)
    pattern = r'self\.__next_f\.push\(\[1,\s*"((?:\\.|[^"\\])*)"\s*\]\)'
    chunks = []

    for m in re.finditer(pattern, source, re.DOTALL):
        payload = m.group(1)
        try:
            decoded = json.loads(f'"{payload}"')
        except Exception:
            decoded = payload
            decoded = decoded.replace("\\n", "\n")
            decoded = decoded.replace("\\/", "/")
            decoded = decoded.replace('\\"', '"')
        chunks.append(decoded)

    return "\n".join(chunks)


def extract_balanced_bracket_block(source, start_index):
    if start_index < 0 or start_index >= len(source) or source[start_index] != "[":
        return ""

    depth = 0
    in_str = False
    escape = False

    for i in range(start_index, len(source)):
        ch = source[i]

        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    return source[start_index:i + 1]

    return ""


def parse_jsonish_array_text(array_text):
    array_text = normalize_space(array_text)
    if not array_text:
        return []

    candidates = [
        array_text,
        array_text.replace('\\"', '"'),
        html.unescape(array_text).replace('\\"', '"'),
    ]

    for candidate in candidates:
        try:
            value = json.loads(candidate)
            if isinstance(value, list):
                return [clean_cvs_text(x) for x in value if isinstance(x, str)]
        except Exception:
            pass

    inner = array_text[1:-1] if array_text.startswith("[") and array_text.endswith("]") else array_text
    parts = re.split(r'"\s*,\s*"', inner)

    cleaned = []
    for part in parts:
        val = clean_cvs_text(part.strip().strip('"'))
        if val:
            cleaned.append(val)

    return cleaned


def find_vendor_mapping(source):
    if not source:
        return None

    patterns = [
        r'\{"vendorDetailsBullets":"\$([0-9A-Za-z]{1,3})","vendorDetailsParagraph":"\$([0-9A-Za-z]{1,3})"\}',
        r'vendorDetailsBullets"\s*:\s*"\$([0-9A-Za-z]{1,3})"\s*,\s*"vendorDetailsParagraph"\s*:\s*"\$([0-9A-Za-z]{1,3})"',
    ]

    for pattern in patterns:
        m = re.search(pattern, source)
        if m:
            return m

    return None


def looks_like_top_level_key_at(source, idx):
    if idx < 0 or idx >= len(source):
        return False

    m = re.match(r'([0-9A-Za-z]{1,3}):(?=[\[{"]|T[0-9A-Za-z]+,)', source[idx:])
    if not m:
        return False

    if idx > 0 and re.match(r"[0-9A-Za-z]", source[idx - 1]):
        return False

    return True


def extract_top_level_value_block(source, key):
    pattern = rf'{re.escape(str(key))}:'
    m = re.search(pattern, source)
    if not m:
        return ""

    start = m.end()
    i = start
    in_str = False
    escape = False
    bracket_depth = 0
    brace_depth = 0
    paren_depth = 0

    while i < len(source):
        ch = source[i]

        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            i += 1
            continue

        if ch == '"':
            in_str = True
            i += 1
            continue

        if ch == "[":
            bracket_depth += 1
            i += 1
            continue
        if ch == "]":
            bracket_depth = max(0, bracket_depth - 1)
            i += 1
            continue

        if ch == "{":
            brace_depth += 1
            i += 1
            continue
        if ch == "}":
            brace_depth = max(0, brace_depth - 1)
            i += 1
            continue

        if ch == "(":
            paren_depth += 1
            i += 1
            continue
        if ch == ")":
            paren_depth = max(0, paren_depth - 1)
            if bracket_depth == 0 and brace_depth == 0 and paren_depth == 0:
                if looks_like_top_level_key_at(source, i + 1):
                    break
            i += 1
            continue

        if bracket_depth == 0 and brace_depth == 0 and paren_depth == 0:
            if looks_like_top_level_key_at(source, i):
                break

        i += 1

    return source[start:i].strip()


def extract_vendor_copy_from_source(source, source_name=""):
    debug = {
        "vendorPatternFound": False,
        "vendorDetailsBulletsRef": "",
        "vendorDetailsParagraphRef": "",
        "featuresKey": "",
        "descriptionKey": "",
        "featuresArrayFound": False,
        "descriptionBlockFound": False,
        "Source Used": source_name,
        "vendorPatternExcerpt": "",
        "featuresArrayExcerpt": "",
        "descriptionBlockExcerpt": "",
    }

    vendor_match = find_vendor_mapping(source)
    if not vendor_match:
        return {"features": [], "description": "", "debug": debug}

    features_key = vendor_match.group(1)
    description_key = vendor_match.group(2)

    debug["vendorPatternFound"] = True
    debug["vendorDetailsBulletsRef"] = f"${features_key}"
    debug["vendorDetailsParagraphRef"] = f"${description_key}"
    debug["featuresKey"] = features_key
    debug["descriptionKey"] = description_key
    debug["vendorPatternExcerpt"] = normalize_space(
        source[max(0, vendor_match.start() - 200): vendor_match.end() + 600]
    )[:2000]

    # FEATURES
    features = []
    features_marker = re.search(rf'{re.escape(features_key)}:\[', source)

    if features_marker:
        array_start = features_marker.end() - 1
        array_text = extract_balanced_bracket_block(source, array_start)
        debug["featuresArrayFound"] = bool(array_text)
        debug["featuresArrayExcerpt"] = normalize_space(array_text)[:2000]
        features = parse_jsonish_array_text(array_text)

    # DESCRIPTION
    desc_block = extract_top_level_value_block(source, description_key)
    debug["descriptionBlockFound"] = bool(desc_block)
    debug["descriptionBlockExcerpt"] = normalize_space(desc_block)[:2000]
    description = clean_cvs_text(desc_block)

    return {
        "features": dedupe_preserve_order(features),
        "description": description,
        "debug": debug,
    }


def extract_vendor_copy_from_nextjs(html_text):
    raw_text = get_nextjs_chunks(html_text)
    raw_html = html.unescape(html_text or "")

    debug = {
        "rawHtmlLength": len(raw_html or ""),
        "rawTextLength": len(raw_text or ""),
        "nextjsChunkFound": bool(raw_text),
        "rawHtmlHasSelfNextF": "self.__next_f.push([1," in (raw_html or ""),
        "rawHtmlHasVendorDetailsBullets": "vendorDetailsBullets" in (raw_html or ""),
        "rawHtmlHasVendorDetailsParagraph": "vendorDetailsParagraph" in (raw_html or ""),
        "rawTextHasVendorDetailsBullets": "vendorDetailsBullets" in (raw_text or ""),
        "rawTextHasVendorDetailsParagraph": "vendorDetailsParagraph" in (raw_text or ""),
        "vendorPatternFound": False,
        "vendorDetailsBulletsRef": "",
        "vendorDetailsParagraphRef": "",
        "featuresKey": "",
        "descriptionKey": "",
        "featuresArrayFound": False,
        "descriptionBlockFound": False,
        "Source Used": "",
        "vendorPatternExcerpt": "",
        "featuresArrayExcerpt": "",
        "descriptionBlockExcerpt": "",
        "rawHtmlVendorExcerpt": "",
        "rawTextVendorExcerpt": "",
    }

    if "vendorDetailsBullets" in raw_html:
        idx = raw_html.find("vendorDetailsBullets")
        debug["rawHtmlVendorExcerpt"] = normalize_space(raw_html[max(0, idx - 250): idx + 1500])[:2000]

    if "vendorDetailsBullets" in raw_text:
        idx = raw_text.find("vendorDetailsBullets")
        debug["rawTextVendorExcerpt"] = normalize_space(raw_text[max(0, idx - 250): idx + 1500])[:2000]

    result = extract_vendor_copy_from_source(raw_text, "raw_text")

    if not result.get("description") and not result.get("features"):
        result = extract_vendor_copy_from_source(raw_html, "raw_html")

    debug.update(result.get("debug", {}))

    return {
        "features": result.get("features", []),
        "description": result.get("description", ""),
        "debug": debug,
    }


def extract_cvs_images_from_html(html_text):
    matches = re.findall(r'/bizcontent/merchandising/productimages/high_res/[^\s"]+\.jpg\?[^\"]*', html_text or "")

    best_images = {}
    order = []

    for m in matches:
        full = "https://www.cvs.com" + m
        base = full.split("?")[0]
        name = base.split("/")[-1]
        size_match = re.search(r"Resize=\((\d+)", m)
        size = int(size_match.group(1)) if size_match else 0

        if name not in best_images:
            order.append(name)
            best_images[name] = {"url": base, "size": size}
        elif size > best_images[name]["size"]:
            best_images[name] = {"url": base, "size": size}

    return [best_images[name]["url"] for name in order]


def get_cvs_text_from_html(html_text, retail_url=""):
    debug = {"Title Path": "", "Description Path": "", "Features Path": ""}

    if not html_text:
        return {"title": "", "description": "", "features": [], "debug": debug}

    soup = BeautifulSoup(html_text, "html.parser")
    title = ""

    h1 = soup.find("h1")
    if h1:
        title = normalize_space(h1.get_text(" ", strip=True))
        debug["Title Path"] = "h1"
    elif soup.title:
        title = normalize_space(soup.title.get_text(" ", strip=True))
        debug["Title Path"] = "html_title"

    vendor_copy = extract_vendor_copy_from_nextjs(html_text)
    description = clean_cvs_text(vendor_copy.get("description", ""))
    features = [clean_cvs_text(x) for x in vendor_copy.get("features", [])]

    debug.update(vendor_copy.get("debug", {}))
    debug["Description Path"] = "vendorDetailsParagraph" if description else "description_empty"
    debug["Features Path"] = "vendorDetailsBullets" if features else "features_empty"

    return {
        "title": title,
        "description": description,
        "features": features[:5],
        "debug": debug,
    }


def get_cvs_bundle(retail_url):
    html_text = get_html(retail_url)
    return {
        "text": get_cvs_text_from_html(html_text, retail_url=retail_url),
        "images": extract_cvs_images_from_html(html_text),
    }

# =========================================
# SINGLE URL TESTER
# =========================================
st.markdown("## 🔗 Test One CVS URL")

single_url = st.text_input(
    "Paste one CVS retail URL",
    placeholder="https://www.cvs.com/shop/..."
)

if st.button("Run Single URL Test"):
    if not single_url.strip():
        st.warning("Paste a CVS URL first.")
    else:
        bundle = get_cvs_bundle(single_url.strip())
        text = bundle["text"]
        images = bundle["images"]
        debug = text.get("debug", {})

        st.markdown("### Title")
        st.write(text.get("title", "") or "Missing title")

        st.markdown("### Description")
        st.write(text.get("description", "") or "Missing description")

        st.markdown("### Features")
        if text.get("features"):
            for i, f in enumerate(text["features"], 1):
                st.write(f"{i}. {f}")
        else:
            st.write("Missing features")

        st.markdown("### Images")
        st.write(f"Image count: {len(images)}")
        if images:
            cols = st.columns(min(3, len(images)))
            for i, img_url in enumerate(images[:3]):
                cols[i % 3].image(img_url)

        with st.expander("Debug"):
            st.json({
                "Title Path": debug.get("Title Path", ""),
                "Description Path": debug.get("Description Path", ""),
                "Features Path": debug.get("Features Path", ""),
                "vendorPatternFound": debug.get("vendorPatternFound", False),
                "vendorDetailsBulletsRef": debug.get("vendorDetailsBulletsRef", ""),
                "vendorDetailsParagraphRef": debug.get("vendorDetailsParagraphRef", ""),
                "featuresKey": debug.get("featuresKey", ""),
                "descriptionKey": debug.get("descriptionKey", ""),
                "featuresArrayFound": debug.get("featuresArrayFound", False),
                "descriptionBlockFound": debug.get("descriptionBlockFound", False),
                "Source Used": debug.get("Source Used", ""),
                "rawHtmlLength": debug.get("rawHtmlLength", 0),
                "rawTextLength": debug.get("rawTextLength", 0),
            })

            st.write("vendorPatternExcerpt")
            st.code(debug.get("vendorPatternExcerpt", ""))

            st.write("featuresArrayExcerpt")
            st.code(debug.get("featuresArrayExcerpt", ""))

            st.write("descriptionBlockExcerpt")
            st.code(debug.get("descriptionBlockExcerpt", ""))

# =========================================
# BULK CSV TESTER
# =========================================
st.markdown("## 📄 Bulk CVS Test From CSV")

uploaded_file = st.file_uploader("Upload CSV", type=["csv"], key="csv_upload")

if uploaded_file:
    try:
        file_bytes = uploaded_file.getvalue()
        df = read_uploaded_csv_from_bytes(file_bytes)
        df = prepare_input_df(df)

        st.write(f"Rows found: {len(df)}")

        if st.button("Run Bulk CVS Test"):
            progress = st.progress(0)
            status = st.empty()

            summary_rows = []
            debug_rows = []

            rows = [row.to_dict() for _, row in df.iterrows()]
            total = len(rows)

            def process_cvs_row(row_dict):
                retail_url = row_dict.get("retail_url", "")
                bundle = get_cvs_bundle(retail_url)
                text = bundle["text"]
                debug = text.get("debug", {})
                images = bundle["images"]

                return {
                    "summary": {
                        "SKU": row_dict.get("sku", ""),
                        "CVS RPC": row_dict.get("cvs_rpc", ""),
                        "Brand": row_dict.get("brand", ""),
                        "Retail URL": retail_url,
                        "CVS Title": text.get("title", ""),
                        "CVS Description": text.get("description", ""),
                        "CVS Features": " | ".join(text.get("features", [])),
                        "CVS Image Count": len(images),
                        "Description Path": debug.get("Description Path", ""),
                        "Features Path": debug.get("Features Path", ""),
                    },
                    "debug": {
                        "SKU": row_dict.get("sku", ""),
                        "CVS RPC": row_dict.get("cvs_rpc", ""),
                        "Brand": row_dict.get("brand", ""),
                        "Retail URL": retail_url,
                        "Title Path": debug.get("Title Path", ""),
                        "Description Path": debug.get("Description Path", ""),
                        "Features Path": debug.get("Features Path", ""),
                        "vendorPatternFound": debug.get("vendorPatternFound", False),
                        "vendorDetailsBulletsRef": debug.get("vendorDetailsBulletsRef", ""),
                        "vendorDetailsParagraphRef": debug.get("vendorDetailsParagraphRef", ""),
                        "featuresKey": debug.get("featuresKey", ""),
                        "descriptionKey": debug.get("descriptionKey", ""),
                        "featuresArrayFound": debug.get("featuresArrayFound", False),
                        "descriptionBlockFound": debug.get("descriptionBlockFound", False),
                        "Source Used": debug.get("Source Used", ""),
                        "rawHtmlLength": debug.get("rawHtmlLength", 0),
                        "rawTextLength": debug.get("rawTextLength", 0),
                        "vendorPatternExcerpt": debug.get("vendorPatternExcerpt", ""),
                        "featuresArrayExcerpt": debug.get("featuresArrayExcerpt", ""),
                        "descriptionBlockExcerpt": debug.get("descriptionBlockExcerpt", ""),
                    }
                }

            completed = 0
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                futures = [executor.submit(process_cvs_row, row) for row in rows]

                for future in as_completed(futures):
                    completed += 1
                    result = future.result()

                    summary_rows.append(result["summary"])
                    debug_rows.append(result["debug"])

                    progress.progress(completed / max(total, 1))
                    status.markdown(f"Processed {completed}/{total}")

            summary_df = pd.DataFrame(summary_rows)
            debug_df = pd.DataFrame(debug_rows)

            st.markdown("### Summary")
            st.dataframe(summary_df, use_container_width=True)

            with st.expander("Debug Table"):
                st.dataframe(debug_df, use_container_width=True)

            output = BytesIO()
            with pd.ExcelWriter(output, engine="openpyxl") as writer:
                summary_df.to_excel(writer, index=False, sheet_name="Summary")
                debug_df.to_excel(writer, index=False, sheet_name="Debug")
            output.seek(0)

            st.download_button(
                label="📥 Download CVS Test Report",
                data=output.getvalue(),
                file_name="cvs_extraction_test.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

    except EmptyDataError:
        st.error("The uploaded CSV is empty or could not be read.")
    except ValueError as e:
        st.error(str(e))
    except Exception as e:
        st.error("Critical app error.")
        st.text(str(e))
        st.text(traceback.format_exc())
