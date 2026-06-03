import streamlit as st
import pandas as pd
import requests
import re
from difflib import SequenceMatcher
from PIL import Image
from io import BytesIO

st.title("PDP QA Tool ✅")

uploaded_file = st.file_uploader("Upload CSV", type=["csv"])
download_placeholder = st.empty()

# =========================================
# ✅ CACHE
# =========================================
html_cache = {}

def get_html(url):
    if url in html_cache:
        return html_cache[url]

    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        if r.status_code == 200:
            html_cache[url] = r.text
            return r.text
    except:
        pass

    return ""

# =========================================
# ✅ LOAD IMAGE
# =========================================
def load_image(url):
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            return Image.open(BytesIO(r.content))
    except:
        return None
    return None

# =========================================
# ✅ ✅ FINAL SALSIFY (PROPERTY + HERO FIX ✅)
# =========================================
def get_salsify_images(url):
    html = get_html(url)

    # =========================================
    # ✅ STEP 1: HERO (TOP 3 FIX ✅)
    # =========================================
    hero_matches = re.findall(r'https://images\.salsify\.com[^"\s]+', html)

    hero_map = {}

    for m in hero_matches[:15]:
        base = m.split("?")[0]
        fname = base.split("/")[-1].lower()

        if any(x in fname for x in ["thumb", "icon", "small"]):
            continue

        key = re.sub(r'(_|-)?\d+x\d+', '', fname)

        size = 0
        size_match = re.search(r'Resize=\((\d+)', m)
        if size_match:
            size = int(size_match.group(1))

        if key not in hero_map or size > hero_map[key]["size"]:
            hero_map[key] = {"url": base, "size": size}

    hero_images = list(hero_map.values())[:3]

    # =========================================
    # ✅ STEP 2: PROPERTY-LEVEL (CRITICAL FIX ✅)
    # =========================================
    prop_matches = re.findall(
        r'"property":"([^"]+)".*?"value":"(https://images\.salsify\.com[^"]+)"',
        html,
        re.DOTALL
    )

    property_map = {}

    for prop, img_url in prop_matches:

        prop = prop.strip()
        base = img_url.split("?")[0]

        size = 0
        size_match = re.search(r'Resize=\((\d+)', img_url)
        if size_match:
            size = int(size_match.group(1))

        # ✅ KEEP ONLY ONE (BEST) IMAGE PER PROPERTY
        if prop not in property_map or size > property_map[prop]["size"]:
            property_map[prop] = {
                "url": base,
                "size": size
            }

    # =========================================
    # ✅ STEP 3: ORDERED ATF
    # =========================================
    TARGET_ORDER = [
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

    property_images = []

    for prop in TARGET_ORDER:
        if prop in property_map:
            property_images.append({
                "type": prop,
                "url": property_map[prop]["url"]
            })

    # =========================================
    # ✅ STEP 4: MERGE (NO DUPLICATES)
    # =========================================
    final = []

    used_urls = set()

    # ✅ add hero first
    for i, img in enumerate(hero_images):
        if img["url"] not in used_urls:
            final.append({
                "type": f"Hero {i+1}",
                "url": img["url"]
            })
            used_urls.add(img["url"])

    # ✅ then properties
    for img in property_images:
        if img["url"] not in used_urls:
            final.append(img)
            used_urls.add(img["url"])

    return final[:8]

# =========================================
# ✅ CVS (UNLIMITED + BEST RES ✅)
# =========================================
def get_cvs_images(url):
    html = get_html(url)

    matches = re.findall(
        r'/bizcontent/merchandising/productimages/high_res/[^\s"]+\.jpg\?[^"]*',
        html
    )

    best = {}

    for m in matches:
        full = "https://www.cvs.com" + m
        base = full.split("?")[0]
        name = base.split("/")[-1]

        size_match = re.search(r'Resize=\((\d+)', m)
        size = int(size_match.group(1)) if size_match else 0

        if name not in best or size > best[name]["size"]:
            best[name] = {"url": base, "size": size}

    return [v["url"] for v in best.values()]

# =========================================
# ✅ TEXT
# =========================================
def get_salsify_text(url):
    html = get_html(url)

    desc = ""
    features = []

    d = re.search(r'"generalDescription":"(.*?)"', html)
    if d:
        desc = d.group(1)

    features = re.findall(r'"generalFeature\d+":"(.*?)"', html)

    return {"description": desc, "features": features[:5]}

def get_cvs_text(html):
    html = html.replace('\\"', '"')
    m = re.search(r'vendorDetailsParagraph":"(.*?)"', html)

    return {"description": m.group(1) if m else ""}

def normalize_text(t):
    return re.sub(r'[^a-z0-9\s]', '', str(t).lower())

def keyword_score(a, b):
    return int(SequenceMatcher(None, normalize_text(a), normalize_text(b)).ratio() * 100)

# =========================================
# ✅ MAIN
# =========================================
if uploaded_file:

    df = pd.read_csv(uploaded_file)
    summary_rows = []

    for _, row in df.iterrows():

        st.subheader(f"SKU: {row['sku']}")

        html = get_html(row["retail_url"])

        s_text = get_salsify_text(row["salsify_url"])
        r_text = get_cvs_text(html)

        # ✅ DESCRIPTION
        st.markdown("## Description")

        c1, c2 = st.columns(2)
        c1.write(s_text["description"])
        c2.write(r_text["description"])

        desc_score = keyword_score(s_text["description"], r_text["description"])
        st.write(f"✅ Description Match: {desc_score}%")

        # ✅ IMAGE COMPARISON
        st.markdown("## Image Comparison")

        s_images = get_salsify_images(row["salsify_url"])
        r_images = get_cvs_images(row["retail_url"])

        max_len = max(len(s_images), len(r_images))

        for i in range(max_len):

            c1, c2 = st.columns(2)

            if i < len(s_images):
                c1.markdown(f"**{s_images[i]['type']}**")
                img = load_image(s_images[i]["url"])
                if img:
                    c1.image(img)

            if i < len(r_images):
                c2.markdown(f"**CVS {i+1}**")
                img = load_image(r_images[i])
                if img:
                    c2.image(img)

        img_score = int(
            (min(len(s_images), len(r_images)) /
             max(len(s_images), len(r_images), 1)) * 100
        )

        overall = int((img_score + desc_score) / 2)

        summary_rows.append({
            "SKU": row["sku"],
            "Image %": img_score,
            "Description %": desc_score,
            "Overall %": overall
        })

# =========================================
# ✅ EXPORT ✅
# =========================================
if summary_rows:
    df = pd.DataFrame(summary_rows)

    file_name = "pdp_qa_results.xlsx"

    with pd.ExcelWriter(file_name, engine="openpyxl") as writer:
        df.to_excel(writer, index=False)

    with open(file_name, "rb") as f:
        download_placeholder.download_button(
            "📥 Download Excel",
            f,
            file_name
        )
