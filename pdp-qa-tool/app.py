

import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
import re
from difflib import SequenceMatcher
from PIL import Image
from io import BytesIO

st.title("PDP QA Tool ✅")

uploaded_file = st.file_uploader("Upload CSV", type=["csv"])

# ✅ TOP DOWNLOAD BUTTON PLACEHOLDER
download_placeholder = st.empty()

# ✅ STORAGE FOR EXPORT DATA
export_rows = []


# =========================================
# ✅ IMAGE ORDER
# =========================================
IMAGE_ORDER = [
    "Online Optimized Image",
    "Flat Back_2D",
    "Flat Left_2D",
    "ATF I/O-Generic",
    "ATF 2-Generic",
    "ATF 3-Generic",
    "ATF 4-Generic",
    "ATF 5-Generic",
    "ATF 6-Generic"
]

# =========================================
# ✅ CACHE
# =========================================
html_cache = {}
image_cache = {}

def get_html(url):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        page.goto(url)

        # ✅ STEP 1 — SCROLL MULTIPLE TIMES
        for _ in range(5):
            page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
            page.wait_for_timeout(1000)

        # ✅ STEP 2 — WAIT FOR IMAGES TO LOAD
        page.wait_for_selector("img", timeout=10000)

        # ✅ EXTRA BUFFER
        page.wait_for_timeout(2000)

        html = page.content()
        browser.close()

    return html

    # =========================================
    # ✅ STEP 1: TRY API USING PRODUCT ID
    # =========================================
    product_id_match = re.search(r'prodid-(\d+)', url)

    if product_id_match:
        product_id = product_id_match.group(1)

        api_url = f"https://www.cvs.com/api/product/v2/{product_id}"

        try:
            res = requests.get(api_url, headers=headers, timeout=15)

            if res.status_code == 200 and res.text.strip():
                return res.text  # ✅ JSON response

        except:
            pass

    # =========================================
    # ✅ STEP 2: FALLBACK TO NORMAL PAGE
    # =========================================
    try:
        res = requests.get(url, headers=headers, timeout=15)
        return res.text
    except:
        return ""
        
def get_soup(url):
    return BeautifulSoup(get_html(url), "html.parser")

# =========================================
# ✅ CLEAN TEXT
# =========================================
def clean_text(raw):
    if not raw:
        return ""

    raw = re.sub(r'<.*?>', '', raw)
    raw = raw.replace('"value":"', '')
    raw = raw.replace('"}', '')
    raw = raw.replace('{', '').replace('}', '')
    raw = raw.replace('"', '')

    raw = raw.lstrip(' ,.') 
    raw = raw.rstrip(' ,')
    raw = re.sub(r'\s+', ' ', raw)

    return raw.strip()

# =========================================
# ✅ SALSIFY IMAGES
# =========================================
def get_salsify_images(url):
    soup = get_soup(url)

    images = []

    # ✅ only grab the digital assets section
    asset_blocks = soup.select('section[aria-label="Digital assets"] div[role="group"]')

    for block in asset_blocks:

        label = block.get("aria-label", "").strip()

        # ✅ THIS IS THE FIX: target correct image only
        img = block.select_one('img[alt="salsify-image"]')

        if not img:
            continue

        src = img.get("src") or ""

        # ✅ fallback for lazy-loaded images
        if not src:
            src = img.get("data-src") or img.get("srcset", "").split(" ")[0]

        if not src.startswith("http"):
            continue

        clean_src = src.split("?")[0]

        images.append({
            "url": clean_src,
            "type": label
        })

    return images
# =========================================
# ✅ ORDER IMAGES
# =========================================
def order_salsify(images):
    ordered = {k: None for k in IMAGE_ORDER}

    for img in images:
        t = img.get("type")
        if t in ordered and ordered[t] is None:
            ordered[t] = img["url"]

    img_list = [img["url"] for img in images]

    for i, key in enumerate(IMAGE_ORDER):
        if ordered[key] is None and i < len(img_list):
            ordered[key] = img_list[i]

    return ordered

# =========================================
# ✅ CVS IMAGES
# =========================================
def get_cvs_images(url):
    html = get_html(url)

    matches = re.findall(
        r'/bizcontent/merchandising/productimages/high_res/[^\s"]+\.jpg\?[^\s"]*',
        html
    )

    image_dict = {}

    for m in matches:
        full = "https://www.cvs.com" + m
        base = full.split("?")[0]
        name = base.split("/")[-1]

        size_match = re.search(r'Resize=\((\d+)', m)
        size = int(size_match.group(1)) if size_match else 0

        if name not in image_dict or size > image_dict[name]["size"]:
            image_dict[name] = {"url": base, "size": size}

    return [v["url"] for v in image_dict.values()]
# =========================================
# ✅ FINAL FEATURE EXTRACTION (STRONG + GENERAL)
# =========================================
def extract_features_from_description(desc):

    if not desc:
        return []

    sentences = re.split(r'(?<=[.!?])\s+', desc)

    features = []

    for s in sentences:
        clean_s = clean_text(s)
        words = clean_s.split()

        wc = len(words)

        # ✅ general feature-like sentence rules
        if 8 <= wc <= 28 and clean_s and clean_s[0].isupper():
            features.append(clean_s)

        # ✅ CVS-style label extraction inside description
        label_matches = re.findall(r'([A-Z][A-Z\s\-]+:\s[^.]+)', clean_s)
        for lm in label_matches:
            features.append(lm)

    # ✅ remove duplicates
    seen = set()
    unique = []

    for f in features:
        if f not in seen and len(f) > 20:
            seen.add(f)
            unique.append(f)

    return unique[:5]
# =========================================
# ✅ CVS TEXT (FINAL WORKING VERSION)
# =========================================
import re
import json

def get_cvs_text(html):

    description = ""
    features = []

    if not html:
        return {"description": "", "features": []}

    # =========================================
    # ✅ STEP 1 — Extract JSON-like text blocks
    # =========================================
    try:
        # Grab all JS blocks that contain text
        matches = re.findall(
            r'self\.__next_f\.push\((.*?)\);',
            html,
            re.DOTALL
        )

        full_text = " ".join(matches)

        # =========================================
        # ✅ DESCRIPTION
        # =========================================
        desc_match = re.search(
            r'vendorDetailsParagraph":"(.*?)"',
            full_text,
            re.DOTALL
        )

        if desc_match:
            description = desc_match.group(1)

            # clean escaped text
            description = description.replace('\\\"', '"')
            description = re.sub(r'\\n', ' ', description)
            description = re.sub(r'\s+', ' ', description)

        # =========================================
        # ✅ FEATURES
        # =========================================
        feature_matches = re.findall(
            r'([A-Z][A-Z\s\-]+:\s.*?)(?:\\n|\\")',
            full_text
        )

        for f in feature_matches:
            clean_f = f.replace('\\\"', '"')
            clean_f = re.sub(r'\s+', ' ', clean_f).strip()

            if len(clean_f) > 20:
                features.append(clean_f)

        # remove duplicates
        features = list(dict.fromkeys(features))[:5]

    except:
        pass

    return {
        "description": description.strip(),
        "features": features
    }
# =========================================
# ✅ SALSIFY TEXT CLEAN VERSION
# =========================================
def get_salsify_text(url):
    soup = get_soup(url)

    description = ""
    features = []

    rows = soup.find_all("tr")

    # ✅ DESCRIPTION
    for row in rows:
        label = row.get_text(" ", strip=True).lower()

        if "general description" in label:
            content = row.find("span", {"data-testid": "property-content"})
            if content:
                description = clean_text(content.get_text(" ", strip=True))
                break

    # ✅ FEATURES (FILTERED)

    for row in rows:
        text = row.get_text(" ", strip=True)
    
        # ✅ ONLY grab actual "General Feature" rows
        if re.search(r'general feature \d+', text, re.IGNORECASE):
            clean = re.sub(r'general feature \d+\s*', '', text, flags=re.IGNORECASE)
            features.append(clean.strip())


    if not features and description:
        features = [description]

    return {
        "description": description,
        "features": features
    }
# =========================================
# ✅ FAST + ALL IMAGES COMPARISON
# =========================================
def load_image_with_white_bg(img_data):
    img = Image.open(BytesIO(img_data)).convert("RGBA")

    # ✅ create white background
    white_bg = Image.new("RGBA", img.size, (255, 255, 255, 255))

    # ✅ paste using alpha channel (this removes transparency issue)
    if img.mode == "RGBA":
        white_bg.paste(img, mask=img.split()[3])
    else:
        white_bg.paste(img)

    # ✅ convert back to grayscale
    return white_bg.convert("L")


def compare_images_visually(s_url, r_url):
    try:
        # ✅ CACHE DOWNLOAD
        if s_url in image_cache:
            s_img_data = image_cache[s_url]
        else:
            s_img_data = requests.get(s_url, timeout=5).content
            image_cache[s_url] = s_img_data

        if r_url in image_cache:
            r_img_data = image_cache[r_url]
        else:
            r_img_data = requests.get(r_url, timeout=5).content
            image_cache[r_url] = r_img_data

        # =========================
        # ✅ FIX: NORMALIZE BACKGROUND
        # =========================

        from PIL import ImageFilter
        
        # ✅ normalize + blur to ignore alignment issues
        s_img = load_image_with_white_bg(s_img_data).resize((64, 64)).filter(ImageFilter.GaussianBlur(2))
        r_img = load_image_with_white_bg(r_img_data).resize((64, 64)).filter(ImageFilter.GaussianBlur(2))

        # =========================
        # ✅ PIXEL DIFFERENCE
        # =========================
        diff = sum(
            abs(a - b)
            for a, b in zip(s_img.getdata(), r_img.getdata())
        ) / (64 * 64)

        # =========================
        # ✅ IMPROVED SCORING
        # =========================
        if diff < 5:
            return 100
        elif diff < 15:
            return 90
        elif diff < 30:
            return 75
        elif diff < 45:
            return 60
        elif diff < 60:
            return 45
        elif diff < 80:
            return 30
        else:
            return 15

    except:
        return 0

def match_images_visual(s_images, r_images):

    results = []
    used_r = set()

    for s in s_images:

        best_score = 0
        best_r = None
        best_idx = None

        for i, r in enumerate(r_images):

            if i in used_r:
                continue

            score = compare_images_visually(s["url"], r)

            if score > best_score:
                best_score = score
                best_r = r
                best_idx = i

        # ✅ LOWER THRESHOLD (IMPORTANT)
        if best_score >= 40:
            results.append((s["url"], best_r, best_score))
            used_r.add(best_idx)
        else:
            results.append((s["url"], "", best_score))

    return results
    # ✅ find unmatched CVS images
        unmatched_r = [
            r for i, r in enumerate(r_images)
            if all(r != match[1] for match in image_matches)
        ]
        
        for r in unmatched_r:
            st.error("🚨 Image exists on CVS but not matched to Salsify")
            st.image(r)
# =========================================
# ✅ TEXT NORMALIZATION
# =========================================
def normalize_text(text):
    text = str(text).lower()
    text = re.sub(r'[^a-z0-9\s]', '', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


# =========================================
# ✅ DESCRIPTION MATCHING (FIXES 0%)
# =========================================
from difflib import SequenceMatcher

def keyword_score(a, b):

    a = normalize_text(a)
    b = normalize_text(b)

    if not a or not b:
        return 0

    # ✅ overall similarity
    
    ratio = max(
        SequenceMatcher(None, a, b).ratio(),
        SequenceMatcher(None, a[:200], b[:200]).ratio()
    )


    # ✅ word overlap bonus
    a_words = set(a.split())
    b_words = set(b.split())

    overlap = len(a_words & b_words)
    total = len(a_words | b_words)

    word_score = (overlap / total) if total else 0

    # ✅ combine both
    final = (ratio * 0.6) + (word_score * 0.4)

    return int(final * 100)
# =========================================
# ✅ FEATURE MATCHING
# =========================================
def match_features(s_features, r_features):
    results = []

    for s in s_features:
        best_match = ""
        best_score = 0

        for r in r_features:
            # ✅ use fuzzy match instead of word overlap
            score = SequenceMatcher(
                None,
                normalize_text(s),
                normalize_text(r)
            ).ratio()

            if score > best_score:
                best_score = score
                best_match = r

        # ✅ lower threshold (CRITICAL CHANGE)
        if best_score >= 0.25:
            results.append((s, best_match, int(best_score * 100)))
        else:
            results.append((s, "❌ Missing", int(best_score * 100)))

    return results

# =========================================
# ✅ MAIN
# =========================================
if uploaded_file:

    df = pd.read_csv(uploaded_file)
    export_rows = []
    summary_rows = []

    for _, row in df.iterrows():

        st.subheader(f"SKU: {row['sku']}")

        # ✅ ALWAYS GET HTML THROUGH SCRAPERAPI
        full_html = get_html(row["retail_url"])

        # ✅ DEBUG BLOCK (ALWAYS VISIBLE)
        with st.expander("🔍 HTML DEBUG", expanded=True):
            st.write("HTML LENGTH:", len(full_html))
            st.write("Contains bladder:", "bladder" in full_html.lower())
            st.write("HAS vendorDetailsParagraph:", "vendorDetailsParagraph" in full_html)
            st.write("HAS ULTRA-ABSORBENT:", "ULTRA-ABSORBENT" in full_html)
            st.text(full_html[:500])
            s_images = get_salsify_images(row["salsify_url"])
            
            st.write("Salsify image count:", len(s_images))
            
            for i, img in enumerate(s_images):
                st.image(img["url"], caption=f"Salsify Image {i}")

        try:
            # =========================
            # ✅ TEXT EXTRACTION
            # =========================
            # =========================
            # ✅ TEXT EXTRACTION
            # =========================
            s_text = get_salsify_text(row["salsify_url"])
            r_text = get_cvs_text(full_html)

            # AFTER parsing
            r_text = get_cvs_text(full_html)
            
            st.write("✅ CVS DESCRIPTION:", r_text.get("description", ""))
            st.write("✅ CVS FEATURES:", r_text.get("features", []))


            # =========================
            # ✅ TITLE
            # =========================
            st.markdown("## Title")

            soup = get_soup(row["salsify_url"])

            s_title = ""
            for row_html in soup.find_all("tr"):
                label = row_html.get_text(" ", strip=True).lower()
                if "product title" in label:
                    span = row_html.find("span", {"data-testid": "property-content"})
                    if span:
                        s_title = span.get_text(strip=True)
                        break

            r_html = full_html  # ✅ use already-fetched HTML

            r_title_match = re.search(r'"productName":"(.*?)"', r_html)
            r_title = r_title_match.group(1) if r_title_match else ""

            if not r_title:
                fallback = re.search(r'<title>(.*?)</title>', r_html)
                r_title = fallback.group(1) if fallback else ""

            r_title = re.sub(r'\s*-\s*CVS.*$', '', r_title).strip()

            c1, c2 = st.columns(2)
            c1.write(s_title)
            c2.write(r_title)

            title_score = int(
                SequenceMatcher(None, s_title.lower(), r_title.lower()).ratio() * 100
            )

            st.write(f"✅ Title Match: {title_score}%")

            # =========================
            # ✅ DESCRIPTION
            # =========================
            st.markdown("## Description")

            c1, c2 = st.columns(2)
            c1.write(s_text.get("description", ""))
            c2.write(r_text.get("description", ""))

            desc_score = keyword_score(
                str(s_text.get("description", "")),
                str(r_text.get("description", ""))
            )

            st.write(f"✅ Description Match: {desc_score}%")

        except Exception as e:
            st.error(f"❌ Error on SKU {row['sku']}: {e}")
            continue
            # =========================
            # ✅ IMAGE SCORE
            # =========================
            img_scores = [sc for _, _, sc in image_matches if sc > 0]
            avg_img_score = int(sum(img_scores) / len(img_scores)) if img_scores else 0

            st.write(f"✅ Image Match: {avg_img_score}%")

            # =========================
            # ✅ OVERALL SCORE
            # =========================
            feature_scores = [sc for _, _, sc in matched]
            avg_feature_score = int(sum(feature_scores) / len(feature_scores)) if feature_scores else 0

            overall_score = int(
                (title_score + desc_score + avg_feature_score + avg_img_score) / 4
            )

            # =========================
            # ✅ SUMMARY SHEET
            # =========================
            summary_row = {
                "SKU": row["sku"],
                "Title %": title_score,
                "Description %": desc_score,
                "Feature %": avg_feature_score,
                "Image Match %": avg_img_score,
                "Overall %": overall_score
            }

            # ✅ IMAGE SCORES PER IMAGE
            for i, (_, _, sc) in enumerate(image_matches):
                summary_row[f"Image {i+1} %"] = sc

            summary_rows.append(summary_row)

            # =========================
            # ✅ DETAIL SHEET
            # =========================
            export_row = {
                "SKU": row["sku"],
                "Salsify Title": s_title,
                "CVS Title": r_title,
                "Salsify Description": s_text.get("description", ""),
                "CVS Description": r_text.get("description", "")
            }

            # ✅ SAFELY ADD FEATURES
            for i in range(min(len(matched), 5)):
                export_row[f"Feature {i+1}"] = matched[i][1]

            export_rows.append(export_row)

            st.divider()

        except Exception as e:
            st.error(f"❌ Error on SKU {row['sku']}: {e}")
            continue

    # =========================================
    # ✅ EXPORT
    # =========================================
    if summary_rows:

        summary_df = pd.DataFrame(summary_rows)
        detail_df = pd.DataFrame(export_rows)

        file_name = "pdp_qa_results.xlsx"

        with pd.ExcelWriter(file_name, engine="openpyxl") as writer:
            summary_df.to_excel(writer, index=False, sheet_name="Summary")
            detail_df.to_excel(writer, index=False, sheet_name="Details")

        with open(file_name, "rb") as f:
            download_placeholder.download_button(
                label="📥 Download Excel Report",
                data=f,
                file_name=file_name,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
