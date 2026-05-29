
import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
import re
from difflib import SequenceMatcher

st.title("PDP QA Tool (FINAL ✅ Working CVS Text)")

uploaded_file = st.file_uploader("Upload CSV", type=["csv"])

# -----------------------------
# HTML HELPERS
# -----------------------------
def get_html(url):
    headers = {"User-Agent": "Mozilla/5.0"}
    return requests.get(url, headers=headers).text

def get_soup(url):
    return BeautifulSoup(get_html(url), "html.parser")

# -----------------------------
# MATCH SCORE
# -----------------------------
def score(a, b):
    if not a or not b:
        return 0
    return int(SequenceMatcher(None, a.lower(), b.lower()).ratio() * 100)

# -----------------------------
# ✅ SALSIFY (unchanged)
# -----------------------------
def get_salsify_text(url):
    soup = get_soup(url)
    raw = soup.get_text(" ", strip=True)

    title = ""
    description = ""
    features = []

    # Title
    t = re.search(r'General Product Title(.*?)(General|$)', raw, re.I)
    if t:
        title = t.group(1).strip()

    # Description
    d = re.search(r'General Description(.*?)(General Feature 1|$)', raw, re.I)
    if d:
        description = d.group(1).strip()

    # Features
    f = re.findall(r'General Feature \d+(.*?)(?=General Feature \d+|$)', raw, re.I)
    for x in f:
        features.append(x.strip())

    return {
        "title": title,
        "description": description,
        "features": features
    }

# -----------------------------
# ✅ CVS TEXT (FINAL WORKING VERSION ✅)
# -----------------------------

def get_cvs_text(url):
    html = get_html(url)
    soup = BeautifulSoup(html, "html.parser")

    title = ""
    description = ""
    features = []

    # =========================================
    # ✅ METHOD 1: DIRECT CONTAINER (BEST CASE)
    # =========================================
    container = soup.find("div", class_=re.compile("whitespace-pre-line"))

    if container:
        # ✅ TITLE
        title_tag = container.find("p", class_=re.compile("text-lg"))
        if title_tag:
            title = title_tag.get_text(strip=True)

        # ✅ DESCRIPTION
        desc_tag = container.find("span", class_=re.compile("text-base"))
        if desc_tag:
            description = desc_tag.get_text(" ", strip=True)

        # ✅ FEATURES
        for li in container.find_all("li", id=re.compile("vendorDetailsBullet")):
            txt = li.get_text(" ", strip=True)
            if txt:
                features.append(txt)

        if title or description or features:
            return {
                "title": title,
                "description": description,
                "features": features
            }

    # =========================================
    # ✅ METHOD 2: FALLBACK TEXT PARSE (ALWAYS WORKS)
    # =========================================
    full_text = soup.get_text("\n", strip=True)
    lines = [l.strip() for l in full_text.split("\n") if l.strip()]

    start_idx = None

    for i, line in enumerate(lines):
        if line.lower() == "details":
            start_idx = i
            break

    if start_idx is None:
        return {"title": "", "description": "", "features": []}

    # ✅ TITLE
    title = lines[start_idx + 1]

    # ✅ DESCRIPTION
    desc_parts = []
    i = start_idx + 2

    while i < len(lines):
        line = lines[i]

        if len(line) < 10:
            i += 1
            continue

        if "tampon" in line.lower() or len(line) > 120:
            desc_parts.append(line)

        # stop before junk sections
        if any(x in line.lower() for x in ["prescription", "coupon", "store"]):
            break

        i += 1

    description = " ".join(desc_parts)

    # ✅ FEATURES
    for j in range(i, len(lines)):
        line = lines[j]

        if any(x in line.lower() for x in ["prescription", "coupon", "store"]):
            break

        if 10 < len(line) < 120:
            features.append(line)

    return {
        "title": title,
        "description": description,
        "features": features[:6]
    }


# -----------------------------
# IMAGES (KEEP YOUR WORKING VERSION)
# -----------------------------
def get_salsify_images(url):
    soup = get_soup(url)
    imgs = []

    for img in soup.find_all("img"):
        src = img.get("src") or ""
        if src.startswith("http"):
            imgs.append(src)

    return list(dict.fromkeys(imgs))[:8]


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

        size_match = re.search(r'Resize=\((\d+),', m)
        size = int(size_match.group(1)) if size_match else 0

        if name not in image_dict or size > image_dict[name]["size"]:
            image_dict[name] = {"url": base, "size": size}

    return [v["url"] for v in image_dict.values()]

# -----------------------------
# MAIN
# -----------------------------
if uploaded_file:

    df = pd.read_csv(uploaded_file)

    for _, row in df.iterrows():

        st.subheader(f"SKU: {row['sku']}")

        s_text = get_salsify_text(row["salsify_url"])
        r_text = get_cvs_text(row["retail_url"])

        s_images = get_salsify_images(row["salsify_url"])
        r_images = get_cvs_images(row["retail_url"])

        # -----------------------------
        # ✅ IMAGES
        # -----------------------------
        st.markdown("## Image Comparison")

        max_len = max(len(s_images), len(r_images))

        for i in range(max_len):
            col1, col2 = st.columns(2)

            with col1:
                if i < len(s_images):
                    st.image(s_images[i], caption=f"Salsify {i+1}")
                else:
                    st.error("Missing")

            with col2:
                if i < len(r_images):
                    st.image(r_images[i], caption=f"CVS {i+1}")
                else:
                    st.error("Missing")

        # -----------------------------
        # ✅ TITLE
        # -----------------------------
        st.markdown("## General Product Title")
        st.write("Salsify:", s_text["title"])
        st.write("CVS:", r_text["title"])
        st.write(f"Match: {score(s_text['title'], r_text['title'])}%")

        # -----------------------------
        # ✅ DESCRIPTION
        # -----------------------------
        st.markdown("## General Description")
        st.write("Salsify:", s_text["description"])
        st.write("CVS:", r_text["description"])
        st.write(f"Match: {score(s_text['description'], r_text['description'])}%")

        # -----------------------------
        # ✅ FEATURES
        # -----------------------------
        st.markdown("## Features")

        max_len = max(len(s_text["features"]), len(r_text["features"]))

        for i in range(max_len):
            col1, col2, col3 = st.columns([3, 3, 1])

            f1 = s_text["features"][i] if i < len(s_text["features"]) else ""
            f2 = r_text["features"][i] if i < len(r_text["features"]) else ""

            with col1:
                st.write("•", f1)

            with col2:
                st.write("•", f2 if f2 else "❌ Missing")

            with col3:
                st.write(f"{score(f1, f2)}%" if f1 and f2 else "—")

        st.divider()
