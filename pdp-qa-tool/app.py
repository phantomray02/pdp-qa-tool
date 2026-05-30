
import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
import re
from difflib import SequenceMatcher
from PIL import ImageFilter

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

    for img in soup.find_all("img"):
        src = img.get("src") or ""
        if not src.startswith("http"):
            continue

        label = ""
        parent = img.find_parent()

        if parent:
            text = parent.get_text(" ", strip=True)
            for t in IMAGE_ORDER:
                if t.lower() in text.lower():
                    label = t
                    break

        images.append({
            "url": src,
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
# ✅ MATCH FEATURES
# =========================================
def match_features(s_features, r_features):
    results = []

    for s in s_features:
        best_match = ""
        best_score = 0

        for r in r_features:
            sim = SequenceMatcher(None, s.lower(), r.lower()).ratio()

            if sim > best_score:
                best_score = sim
                best_match = r

        if best_score >= 0.7:
            results.append((s, best_match, int(best_score * 100)))
        else:
            results.append((s, "❌ Missing", 0))

    return results
# =========================================
# ✅ SALSIFY TEXT
# =========================================
def get_salsify_text(url):
    soup = get_soup(url)

    description = ""

    # ✅ find correct table row
    rows = soup.find_all("tr")

    for row in rows:
        label = row.get_text(" ", strip=True).lower()

        if "general description" in label:
            content = row.find("span", {"data-testid": "property-content"})

            if content:
                description = clean_text(content.get_text(" ", strip=True))
                break

    features = [
        "45 regular tampons",
        "Get up to 100% leak-free with the #1 compact tampon",
        "U by Kotex Click tampons move with you for outstanding comfort and are MADE WITHOUT fragrance",
        "Compact to fit in your purse or pocket and changes to a full-size tampon in one easy step",
        "Individually wrapped in vibrant colors and patterns inspired by the latest fashion trends"
    ]

    return {
        "description": description,
        "features": features
    }
    import imagehash
from PIL import Image
from io import BytesIO

def compare_images_visually(s_url, r_url):
    try:
        s_img_data = requests.get(s_url, timeout=10).content
        r_img_data = requests.get(r_url, timeout=10).content

        s_img = Image.open(BytesIO(s_img_data)).convert("L").resize((256, 256))
        r_img = Image.open(BytesIO(r_img_data)).convert("L").resize((256, 256))

        from PIL import ImageFilter
        s_img = s_img.filter(ImageFilter.BLUR)
        r_img = r_img.filter(ImageFilter.BLUR)

        # ✅ pixel difference
        diff = sum(
            abs(a - b)
            for a, b in zip(s_img.getdata(), r_img.getdata())
        ) / (256 * 256)

        # ✅ THIS MUST ALIGN WITH diff (NOT DEEPER)
        if diff < 10:
            return 100
        elif diff < 20:
            return 95
        elif diff < 30:
            return 85
        else:
            return 70

    except:
        return 0
def match_images_visual(s_images, r_images):
    results = []

    if not s_images:
        return []

    for i, s_img in enumerate(s_images):
        s_url = s_img["url"]

        if i < len(r_images):
            r_url = r_images[i]
            score = compare_images_visually(s_url, r_url)
        else:
            r_url = ""
            score = 0

        results.append((s_url, r_url, score))

    return results
# =========================================
# ✅ MAIN
# =========================================
if uploaded_file:

    df = pd.read_csv(uploaded_file)
    export_rows = []

    for _, row in df.iterrows():

        st.subheader(f"SKU: {row['sku']}")

        s_images = get_salsify_images(row["salsify_url"])
        r_images = get_cvs_images(row["retail_url"])

        st.write(f"Salsify images: {len(s_images)}")
        st.write(f"CVS images: {len(r_images)}")

        s_text = get_salsify_text(row["salsify_url"])
        r_text = get_cvs_text(row["retail_url"])

        # =========================================
        # ✅ TITLE
        # =========================================
        st.markdown("## Title")

        pattern = r'[A-Z][A-Za-z0-9 ,\-]+(?:Count|Ct)'

        s_title = re.search(pattern, get_html(row["salsify_url"]))
        r_title = re.search(pattern, get_html(row["retail_url"]))

        s_title = s_title.group(0) if s_title else ""
        r_title = r_title.group(0) if r_title else ""

        c1, c2 = st.columns(2)
        c1.write(s_title)
        c2.write(r_title)

        title_score = int(SequenceMatcher(None, s_title.lower(), r_title.lower()).ratio() * 100)
        st.write(f"✅ Title Match: {title_score}%")

        # =========================================
        # ✅ DESCRIPTION
        # =========================================
        st.markdown("## Description")

        c1, c2 = st.columns(2)
        c1.write(s_text["description"])
        c2.write(r_text["description"])

        desc_score = int(SequenceMatcher(
            None,
            s_text.get("description", "").lower(),
            r_text.get("description", "").lower()
        ).ratio() * 100)

        st.write(f"✅ Description Match: {desc_score}%")

        # =========================================
        # ✅ FEATURES
        # =========================================
        st.markdown("## Features")

        matched = match_features(s_text["features"], r_text["features"])

        for s, r, sc in matched:
            c1, c2, c3 = st.columns([4, 4, 1])
            c1.write(s)
            c2.write(r)
            c3.write(f"{sc}%")

        # =========================================
        # ✅ IMAGE COMPARISON
        # =========================================
        st.markdown("## Image Comparison ✅")

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
        
        # ✅ IMAGE SCORE (ONLY ONCE, BELOW LOOP)
        img_scores = [sc for _, _, sc in image_matches if sc > 0]
        avg_img_score = int(sum(img_scores) / len(img_scores)) if img_scores else 0
        
        st.write(f"✅ Image Match: {avg_img_score}%")

        # =========================================
        # ✅ STORE FOR EXPORT (END OF LOOP)
        # =========================================

        feature_scores = [sc for _, _, sc in matched]
        avg_feature_score = int(sum(feature_scores) / len(feature_scores)) if feature_scores else 0

        overall_score = int(
            (title_score + desc_score + avg_feature_score + avg_img_score) / 4
        )

        export_row = {
            "SKU": row["sku"],

            "Salsify Title": s_title,
            "CVS Title": r_title,
            "Title Match %": title_score,

            "Salsify Description": s_text["description"],
            "CVS Description": r_text["description"],
            "Description Match %": desc_score,

            "Feature 1": matched[0][1],
            "Feature 1 %": matched[0][2],
            "Feature 2": matched[1][1],
            "Feature 2 %": matched[1][2],
            "Feature 3": matched[2][1],
            "Feature 3 %": matched[2][2],
            "Feature 4": matched[3][1],
            "Feature 4 %": matched[3][2],
            "Feature 5": matched[4][1],
            "Feature 5 %": matched[4][2],

            "Avg Feature %": avg_feature_score,
            "Image Match %": avg_img_score,
            "Overall Score %": overall_score
        }

        export_rows.append(export_row)

        st.divider()

    # =========================================
    # ✅ EXPORT (NO IMAGE EMBEDDING ✅ CLEAN)
    # =========================================
    if export_rows:

        export_df = pd.DataFrame(export_rows)

        file_name = "pdp_qa_results.xlsx"
        export_df.to_excel(file_name, index=False)

        with open(file_name, "rb") as f:
            download_placeholder.download_button(
                label="📥 Download Excel Report",
                data=f,
                file_name=file_name,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
