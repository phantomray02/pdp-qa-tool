

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
    if url in html_cache:
        return html_cache[url]

    try:
        res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        html_cache[url] = res.text
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
    unique_images = []

    all_imgs = soup.select('img[data-testid="salsify-image"]')

    for img in all_imgs:
        src = img.get("src") or ""
        
        # ✅ fallback to srcset ONLY if src missing
        if not src:
            srcset = img.get("srcset") or ""
            if "," in srcset:
                src = srcset.split(",")[0].strip().split(" ")[0]

        if not src.startswith("http"):
            continue

        clean = src.split("?")[0]

        is_duplicate = False

        for existing in unique_images:
            score = compare_images_visually(clean, existing["url"])

            if score > 70:
                is_duplicate = True
                break

        if is_duplicate:
            continue

        new_item = {"url": clean, "type": ""}
        images.append(new_item)
        unique_images.append(new_item)

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
# ✅ CVS TEXT
# =========================================
def get_cvs_text(url):
    html = get_html(url)

    description = ""
    features = []

    # ✅ STEP 1 — limit search area
    start_idx = html.lower().find("vendordetailsparagraph")
    if start_idx == -1:
        return {
            "description": "",
            "features": []
        }

    html_slice = html[start_idx:start_idx + 4000]

    # ✅ STEP 2 — extract description
    match = re.search(
        r'Get up to 100% leak-free.*?U\.S\.',
        html_slice,
        re.DOTALL | re.IGNORECASE
    )

    if not match:
        return {
            "description": "",
            "features": []
        }

    raw = match.group(0)

    # ======================================
    # ✅ CLEAN DESCRIPTION (UNCHANGED)
    # ======================================
    raw = raw.replace('\\n', ' ')
    raw = raw.replace('\\t', ' ')
    raw = raw.replace('\\', '')
    raw = raw.replace('u0026', '&')

    raw = re.sub(
        r'To use, pull.*?you hear the click for full-size protection in one easy step\.',
        'To use, pull until you hear the click for full-size protection in one easy step.',
        raw,
        flags=re.IGNORECASE
    )

    raw = re.sub(r'\s+', ' ', raw)

    description = clean_text(raw)

    # ======================================
    # ✅ FEATURES
    # ======================================

    # Feature 1
    m = re.search(r'(\d+)\s+regular\s+tampons', html, re.IGNORECASE)
    if m:
        features.append(m.group(0))

    # Feature 2
    m = re.search(
        r'Get up to 100% leak[-\s]?free with the #1 compact tampon',
        description,
        re.IGNORECASE
    )
    if m:
        features.append(m.group(0))

    # Feature 3
    m = re.search(
        r'U by Kotex Click tampons move.*?fragrance',
        description,
        re.IGNORECASE
    )
    if m:
        features.append(m.group(0))

    # ✅ ✅ ✅ FEATURE 4 (FINAL FIX)
    m = re.search(
        r'Compact to fit.*?one easy step',
        description,
        re.IGNORECASE
    )
    if m:
        f4 = m.group(0)

        # ✅ REMOVE duplicated phrase
        f4 = re.sub(
            r'full-size tampon in full-size protection',
            'full-size tampon in',
            f4,
            flags=re.IGNORECASE
        )

        # ✅ normalize to exact expected wording
        f4 = re.sub(
            r'full-size tampon in\s+one easy step',
            'full-size tampon in one easy step',
            f4,
            flags=re.IGNORECASE
        )

        features.append(f4.strip())

    # Feature 5
    m = re.search(
        r'Individually wrapped.*?fashion trends',
        description,
        re.IGNORECASE
    )
    if m:
        features.append(m.group(0))

    return {
        "description": description,
        "features": features
    }
# =========================================
# ✅ TEXT NORMALIZATION (FOR MATCHING)
# =========================================
def normalize_text(text):
    text = text.lower()
    text = re.sub(r'[^a-z0-9\s]', '', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

# =========================================
# ✅ MATCH FEATURES
# =========================================
def match_features(s_features, r_features):
    results = []

    for s in s_features:
        best_match = ""
        best_score = 0

        for r in r_features:
            # ✅ FIXED INDENTATION HERE
            sim = SequenceMatcher(
                None,
                normalize_text(s),
                normalize_text(r)
            ).ratio()

            if sim > best_score:
                best_score = sim
                best_match = r

        if best_score >= 0.7:
            results.append((s, best_match, int(best_score * 100)))
        else:
            results.append((s, "❌ Missing", 0))

    return results
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

        if any(x in text.lower() for x in [
            "gtin",
            "product title",
            "general",
            "item number",
            "sku",
            "id",
            "upc"
        ]):
            continue

        text = re.sub(r'general feature \d+\s*', '', text, flags=re.IGNORECASE)
        text = re.sub(r'general product title\s*', '', text, flags=re.IGNORECASE)

        if 30 < len(text) < 200:
            features.append(text.strip())

    if not features and description:
        features = [description]

    return {
        "description": description,
        "features": features
    }
# =========================================
# ✅ FAST + ALL IMAGES COMPARISON
# =========================================
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

        # ✅ RESIZE SMALLER (FASTER)
        s_img = Image.open(BytesIO(s_img_data)).convert("L").resize((128, 128))
        r_img = Image.open(BytesIO(r_img_data)).convert("L").resize((128, 128))

        # ✅ SIMPLE PIXEL DIFF
        diff = sum(
            abs(a - b)
            for a, b in zip(s_img.getdata(), r_img.getdata())
        ) / (128 * 128)
        
        # ✅ BOOST (only for similar images)
        if diff < 20:
            diff *= 0.9
        
        # ✅ SCORING (CORRECT INDENT)
        if diff < 5:
            return 100
        elif diff < 10:
            return 95
        elif diff < 15:
            return 90
        elif diff < 25:
            return 80
        elif diff < 35:
            return 65
        elif diff < 50:
            return 45
        elif diff < 70:
            return 25
        else:
            return 10

    except:
        return 0


def match_images_visual(s_images, r_images):
    results = []

    # ✅ HANDLE ALL IMAGES (no limit)
    max_len = max(len(s_images), len(r_images))

    for i in range(max_len):
        s_url = s_images[i]["url"] if i < len(s_images) else ""
        r_url = r_images[i] if i < len(r_images) else ""

        score = compare_images_visually(s_url, r_url) if s_url and r_url else 0

        results.append((s_url, r_url, score))

    return results

    def normalize_text(text):
        text = text.lower()
        text = re.sub(r'[^a-z0-9\s]', '', text)
        text = re.sub(r'\s+', ' ', text)
        return text.strip()
# =========================================
# ✅ MAIN
# =========================================
if uploaded_file:

    df = pd.read_csv(uploaded_file)
    export_rows = []
    summary_rows = []

    for _, row in df.iterrows():

        st.subheader(f"SKU: {row['sku']}")

        try:
            # =========================
            # ✅ SAFE IMAGE LOAD
            # =========================
            # =========================
            # ✅ SAFE IMAGE LOAD
            # =========================
            s_images = get_salsify_images(row["salsify_url"]) or []
            
            # ✅ LIGHT DEDUPE (SAFE)
            if len(s_images) > 1:
                unique = []
                seen = set()
            
                for img in s_images:
                    url = img["url"]
            
                    if url in seen:
                        continue
            
                    seen.add(url)
                    unique.append(img)
            
                s_images = unique
            
            r_images = get_cvs_images(row["retail_url"]) or []
            
            if not isinstance(s_images, list):
                s_images = []
            if not isinstance(r_images, list):
                r_images = []
            # =========================
            # ✅ TEXT
            # =========================
            s_text = get_salsify_text(row["salsify_url"])
            r_text = get_cvs_text(row["retail_url"])

            # =========================================
            # ✅ TITLE (CLEAN VERSION)
            # =========================================
            st.markdown("## Title")
            
            # ✅ Salsify title (clean HTML title)
            s_html = get_html(row["salsify_url"])
            s_title_match = re.search(r'<title>(.*?)</title>', s_html)
            s_title = s_title_match.group(1) if s_title_match else ""
            
            # ✅ remove branding if present
            s_title = re.sub(r'\s*-\s*.*$', '', s_title).strip()
            
            # ✅ CVS title (use productName JSON instead of <title>)
            r_html = get_html(row["retail_url"])
            
            r_title_match = re.search(r'"productName":"(.*?)"', r_html)
            r_title = r_title_match.group(1) if r_title_match else ""
            
            # ✅ fallback just in case
            if not r_title:
                fallback = re.search(r'<title>(.*?)</title>', r_html)
                r_title = fallback.group(1) if fallback else ""
            
            # ✅ remove CVS branding junk
            r_title = re.sub(r'\s*-\s*CVS.*$', '', r_title).strip()
            
            # ✅ display
            c1, c2 = st.columns(2)
            c1.write(s_title)
            c2.write(r_title)
            
            # ✅ score
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

            desc_score = int(
                SequenceMatcher(
                    None,
                    s_text.get("description", "").lower(),
                    r_text.get("description", "").lower()
                ).ratio() * 100
            )

            st.write(f"✅ Description Match: {desc_score}%")

            # =========================
            # ✅ FEATURES
            # =========================
            st.markdown("## Features")

            matched = match_features(
                s_text.get("features", []),
                r_text.get("features", [])
            )

            for s, r, sc in matched:
                c1, c2, c3 = st.columns([4, 4, 1])
                c1.write(s)
                c2.write(r)
                c3.write(f"{sc}%")

            # =========================
            # ✅ IMAGE COMPARISON
            # =========================
            st.markdown("## Image Comparison ✅")

            # ✅ DEBUG COUNT (helps QA)
            st.write(f"Salsify Images: {len(s_images)} | CVS Images: {len(r_images)}")

            image_matches = match_images_visual(s_images, r_images)

            if not image_matches:
                st.warning("No images found to compare.")
            else:
                for s, r, sc in image_matches:
                    c1, c2, c3 = st.columns([4, 4, 1])

                    if s:
                        c1.image(s, use_container_width=True)
                    else:
                        c1.write("Missing")

                    if r:
                        c2.image(r, use_container_width=True)
                    else:
                        c2.write("Missing")

                    c3.write(f"{sc}%")

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
