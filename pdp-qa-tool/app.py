
import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
import re
from difflib import SequenceMatcher

st.title("PDP QA Tool (FINAL ✅)")

uploaded_file = st.file_uploader("Upload CSV", type=["csv"])

# -----------------------------
# HELPERS
# -----------------------------
def get_html(url):
    headers = {"User-Agent": "Mozilla/5.0"}
    return requests.get(url, headers=headers).text

def get_soup(url):
    return BeautifulSoup(get_html(url), "html.parser")

def score(a, b):
    if not a or not b:
        return 0
    return int(SequenceMatcher(None, a.lower(), b.lower()).ratio() * 100)

# -----------------------------
# ✅ IMAGES
# -----------------------------
def get_salsify_images(url):
    try:
        soup = get_soup(url)
        imgs = []

        for img in soup.find_all("img"):
            src = img.get("src") or ""
            if src.startswith("http"):
                imgs.append(src)

        return list(dict.fromkeys(imgs))[:8]
    except:
        return []

def get_cvs_images(url):
    try:
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

    except:
        return []

# -----------------------------
# ✅ SALSIFY TEXT (STRICT)
# -----------------------------
def get_salsify_text(url):
    try:
        soup = get_soup(url)
        raw = soup.get_text(" ", strip=True)

        title = ""
        description = ""
        features = []

        # TITLE
        t = re.search(r'General Product Title(.*?)(General|$)', raw, re.I)
        if t:
            title = t.group(1).strip()

        # DESCRIPTION
        d = re.search(r'General Description(.*?)(General Feature 1|$)', raw, re.I)
        if d:
            description = d.group(1).strip()

        # FEATURES
        f = re.findall(
            r'General Feature \d+(.*?)(?=General Feature \d+|$)',
            raw,
            re.I
        )

        for x in f:
            clean = x.strip()
            if clean:
                features.append(clean)

        return {
            "title": title,
            "description": description,
            "features": features
        }

    except:
        return {"title": "", "description": "", "features": []}

# -----------------------------
# ✅ CVS TEXT (STRICT RULES)
# -----------------------------


def get_cvs_text(url):
    try:
        soup = get_soup(url)

        title = ""
        description = ""
        features = []

        # -----------------------------
        # ✅ TITLE
        # -----------------------------
        title_tags = soup.select("p.text-lg")

        for tag in title_tags:
            txt = tag.get_text(strip=True)

            if "kotex" in txt.lower():
                title = txt
                break

        # -----------------------------
        # ✅ DESCRIPTION (BEST MATCH)
        # -----------------------------
        spans = soup.select("span.text-base")

        best_desc = ""

        for span in spans:
            txt = span.get_text(" ", strip=True)

            if (
                len(txt) > 150
                and "tampon" in txt.lower()
                and "see all" not in txt.lower()
            ):
                # pick longest one (most complete)
                if len(txt) > len(best_desc):
                    best_desc = txt

        description = best_desc

        # -----------------------------
        # ✅ FEATURES (STRICT ✅)
        # -----------------------------
        for li in soup.select("li[id^='vendorDetailsBullet']"):

            txt = li.get_text(" ", strip=True)

            if (
                txt
                and len(txt) > 5
                and "last updated" not in txt.lower()
            ):
                features.append(txt)

        return {
            "title": title,
            "description": description,
            "features": features
        }

    except:
        return {"title": "", "description": "", "features": []
               }
        
# -----------------------------
# MAIN
# -----------------------------
if uploaded_file:

    df = pd.read_csv(uploaded_file)

    for _, row in df.iterrows():

        st.subheader(f"SKU: {row['sku']}")

        # DATA
        s_images = get_salsify_images(row["salsify_url"])
        r_images = get_cvs_images(row["retail_url"])

        s_text = get_salsify_text(row["salsify_url"])
        r_text = get_cvs_text(row["retail_url"])

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

        col1, col2 = st.columns(2)

        with col1:
            st.write(s_text["title"])

        with col2:
            st.write(r_text["title"])

        st.write(f"Match: {score(s_text['title'], r_text['title'])}%")

        # -----------------------------
        # ✅ DESCRIPTION
        # -----------------------------
        st.markdown("## General Description")

        col1, col2 = st.columns(2)

        with col1:
            st.write(s_text["description"])

        with col2:
            st.write(r_text["description"])

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
                if f1:
                    st.write("•", f1)

            with col2:
                if f2:
                    st.write("•", f2)
                else:
                    st.write("❌ Missing")

            with col3:
                st.write(f"{score(f1, f2)}%" if f1 and f2 else "—")

        st.divider()
