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
download_placeholder = st.empty()

# =========================================
# ✅ CACHE
# =========================================
html_cache = {}

def get_html(url):
    if url in html_cache:
        return html_cache[url]

    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=15)

        if r.status_code == 200:
            html_cache[url] = r.text
            return r.text
    except:
        pass

    return ""

def get_soup(url):
    return BeautifulSoup(get_html(url), "html.parser")

# =========================================
# ✅ IMAGE HELPERS
# =========================================
def load_image(url):
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            return Image.open(BytesIO(r.content))
    except:
        return None
    return None

def extract_best_image_from_tag(img_tag):
    if not img_tag:
        return None

    srcset = img_tag.get("srcset", "")
    src = img_tag.get("src", "")

    if srcset and "salsify" in srcset:
        urls = [u.strip().split()[0] for u in srcset.split(",") if u.strip()]
        return urls[-1] if urls else None

    if src and "salsify" in src:
        return src

    return None

# =========================================
# ✅ COALESCE / PRIORITY IMAGE LOGIC
# =========================================
def normalize_prop(p):
    return p.lower().replace(" ", "").replace("_", "").replace("-", "")

def get_salsify_images(url):
    html = get_html(url)
    soup = BeautifulSoup(html, "html.parser")

    images = []
    seen = set()

    # 🔹 STEP 1: PROPERTY CONTAINERS
    containers = soup.find_all(
        lambda tag: tag.name == "div"
        and tag.get("class")
        and any("asset-list_images__" in c for c in tag.get("class"))
    )

    property_map = {}

    for c in containers:
        aria = c.get("aria-label", "").strip().rstrip("-")
        if aria:
            property_map[aria] = c

    def get_prop_image(target):
        t_norm = normalize_prop(target)

        for pname, container in property_map.items():
            if normalize_prop(pname) == t_norm:
                return extract_best_image_from_tag(container.find("img")) or None
        return None

    # 🔹 STEP 2: ALWAYS REQUIRED
    always = [
        "Online Optimized Image",
        "Flat Back_2D",
        "Flat Left_2D"
    ]

    for prop in always:
        url = get_prop_image(prop)
        if url and url not in seen:
            images.append({"type": prop, "url": url})
            seen.add(url)

    # 🔹 STEP 3: IO or 2
    io = get_prop_image("ATF I/O-Generic")

    if io:
        images.append({"type": "ATF I/O-Generic", "url": io})
        seen.add(io)
    else:
        atf2 = get_prop_image("ATF 2-Generic")
        if atf2:
            images.append({"type": "ATF 2-Generic", "url": atf2})
            seen.add(atf2)

    # 🔹 STEP 4: Add remaining ATFs
    for lvl in ["ATF 3-Generic", "ATF 4-Generic", "ATF 5-Generic"]:
        val = get_prop_image(lvl)
        if val and val not in seen:
            images.append({"type": lvl, "url": val})
            seen.add(val)

    # 🔹 STEP 5: ATF 6 fallback
    if not io:
        atf6 = get_prop_image("ATF 6-Generic")
        if atf6 and atf6 not in seen:
            images.append({"type": "ATF 6-Generic", "url": atf6})
            seen.add(atf6)

    # 🔹 STEP 6: FALLBACK → any image
    if not images:
        for img in soup.find_all("img"):
            url = extract_best_image_from_tag(img)
            if url and "salsify" in url and url not in seen:
                seen.add(url)
                images.append({"type": "Fallback", "url": url})

    return images

# =========================================
# ✅ CVS IMAGES
# =========================================
def get_cvs_images(url):
    html = get_html(url)

    matches = re.findall(
        r'/bizcontent/merchandising/productimages/high_res/[^\s"]+\.jpg',
        html
    )

    return ["https://www.cvs.com" + m for m in matches]

# =========================================
# ✅ TEXT
# =========================================
def normalize_text(text):
    text = str(text).lower()
    text = re.sub(r'[^a-z0-9\s]', '', text)
    return text

def keyword_score(a, b):
    a = normalize_text(a)
    b = normalize_text(b)

    if not a or not b:
        return 0

    return int(SequenceMatcher(None, a, b).ratio() * 100)

def get_salsify_text(url):
    soup = get_soup(url)
    desc = ""

    for row in soup.find_all("tr"):
        label = row.get_text(" ", strip=True).lower()
        if "general description" in label:
            desc = row.get_text(" ", strip=True)
            break

    return {"description": desc}

def get_cvs_text(html):
    desc = ""

    match = re.search(r'vendorDetailsParagraph":"(.*?)"', html)
    if match:
        desc = match.group(1)

    return {"description": desc}

# =========================================
# ✅ MAIN
# =========================================
if uploaded_file:

    df = pd.read_csv(uploaded_file)
    summary_rows = []

    for _, row in df.iterrows():

        st.subheader(f"SKU: {row['sku']}")

        s_images = get_salsify_images(row["salsify_url"])
        r_images = get_cvs_images(row["retail_url"])

        # ✅ IMAGE VIEW
        max_len = max(len(s_images), len(r_images))

        for i in range(max_len):
            c1, c2 = st.columns(2)

            if i < len(s_images):
                c1.markdown(f"Salsify {i+1}")
                img = load_image(s_images[i]["url"])
                if img:
                    c1.image(img)
            else:
                c1.write("Missing")

            if i < len(r_images):
                c2.markdown(f"CVS {i+1}")
                img = load_image(r_images[i])
                if img:
                    c2.image(img)
            else:
                c2.write("Missing")

        # ✅ SCORE
        img_score = int((min(len(s_images), len(r_images)) / max(len(s_images), len(r_images), 1)) * 100)

        s_text = get_salsify_text(row["salsify_url"])
        r_text = get_cvs_text(get_html(row["retail_url"]))

        desc_score = keyword_score(
            s_text.get("description", ""),
            r_text.get("description", "")
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
if 'summary_rows' in locals() and summary_rows:

    df = pd.DataFrame(summary_rows)

    file_name = "pdp_qa_results.xlsx"

    with pd.ExcelWriter(file_name, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Summary")

    with open(file_name, "rb") as f:
        download_placeholder.download_button(
            "📥 Download Excel",
            f,
            file_name
        )
