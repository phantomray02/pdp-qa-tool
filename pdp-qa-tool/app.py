import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
import re
from difflib import SequenceMatcher
from PIL import Image
from io import BytesIO
import json

st.title("PDP QA Tool ✅")

uploaded_file = st.file_uploader("Upload CSV", type=["csv"])

download_placeholder = st.empty()
export_rows = []

# =========================================
# ✅ IMAGE PROPERTIES TO EXTRACT (IN ORDER)
# ✅ CONDITIONAL LOGIC:
# - Always extract: Online Optimized Image, Flat Back_2D, Flat Left_2D
# - Then: ATF I/O-Generic (if exists) OR ATF 2-Generic (if exists)
# - Then: ATF 6-Generic (only if ATF I/O-Generic is NOT present)
# =========================================
ALWAYS_REQUIRED = [
    "Online Optimized Image",
    "Flat Back_2D",
    "Flat Left_2D"
]

CONDITIONAL_IO_OR_2 = [
    "ATF I/O-Generic",  # Try this first
    "ATF 2-Generic"     # If I/O doesn't exist, use this
]

FALLBACK_IF_NO_IO = "ATF 6-Generic"  # Only if I/O-Generic is missing

# =========================================
# ✅ START PLAYWRIGHT ONCE (GLOBAL)
# =========================================
from playwright.sync_api import sync_playwright
import atexit

def cleanup():
    try:
        browser.close()
        p.stop()
    except:
        pass

atexit.register(cleanup)

p = sync_playwright().start()
browser = p.chromium.launch(headless=True)
atexit.register(lambda: (browser.close(), p.stop()))

# =========================================
# ✅ CACHE
# =========================================
html_cache = {}
image_cache = {}

# =========================================
# ✅ GET HTML (OPTIMIZED)
# =========================================
def get_html(url):
    if url in html_cache:
        return html_cache[url]

    try:
        page = browser.new_page()
        page.goto(url, timeout=30000, wait_until="networkidle")
        
        # Scroll for lazy loading
        for _ in range(5):
            page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
            page.wait_for_timeout(1000)
        
        page.wait_for_selector("img", timeout=10000)
        page.wait_for_timeout(2000)
        
        html = page.content()
        page.close()
        
        html_cache[url] = html
        return html
        
    except Exception as e:
        print(f"Playwright failed for {url}: {e}")
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
# ✅ SALSIFY IMAGES - FORCED EXTRACTION WITH CONDITIONAL LOGIC
# =========================================
def normalize_prop(p):
    return p.lower().replace(" ", "").replace("_", "").replace("-", "")


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


def extract_from_container(container):
    # Try noscript first (best quality)
    noscript = container.find("noscript")
    if noscript:
        img = noscript.find("img")
        url = extract_best_image_from_tag(img)
        if url:
            return url

    # Try main img
    img = container.find("img")
    url = extract_best_image_from_tag(img)
    if url:
        return url

    # Try ANY image inside
    for img in container.find_all("img"):
        url = extract_best_image_from_tag(img)
        if url:
            return url

    return None


def extract_from_json(html):
    """
    🔥 Most reliable fallback
    Extracts images directly from embedded JSON
    """
    results = []

    try:
        matches = re.findall(r'https://images\\.salsify\\.com[^"]+', html)

        for m in matches:
            clean = m.split("?")[0]
            results.append(clean)

        return list(dict.fromkeys(results))  # dedupe

    except:
        return []


def get_salsify_images(url):
    html = get_html(url)
    soup = BeautifulSoup(html, "html.parser")

    images = []
    seen = set()

    print("\n🔍 HARDENED EXTRACTION START\n")

    # ==================================================
    # 🔹 STEP 1: FIND PROPERTY-BASED CONTAINERS
    # ==================================================
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

    print(f"✅ Found {len(property_map)} property containers")

    def get_prop_image(target):
        t_norm = normalize_prop(target)

        for pname, container in property_map.items():
            if normalize_prop(pname) == t_norm:
                return extract_from_container(container)

        return None

    # ==================================================
    # 🔹 STEP 2: ALWAYS REQUIRED
    # ==================================================
    ALWAYS = [
        "Online Optimized Image",
        "Flat Back_2D",
        "Flat Left_2D"
    ]

    for prop in ALWAYS:
        url = get_prop_image(prop)

        if url and url not in seen:
            images.append({"type": prop, "url": url})
            seen.add(url)
            print(f"✅ {prop}")
        else:
            print(f"❌ {prop}")

    # ==================================================
    # 🔹 STEP 3: IO / 2 LOGIC
    # ==================================================
    io_url = get_prop_image("ATF I/O-Generic")

    io_found = False

    if io_url:
        images.append({"type": "ATF I/O-Generic", "url": io_url})
        seen.add(io_url)
        io_found = True
        print("✅ ATF I/O-Generic")
    else:
        atf2 = get_prop_image("ATF 2-Generic")

        if atf2:
            images.append({"type": "ATF 2-Generic", "url": atf2})
            seen.add(atf2)
            print("✅ ATF 2-Generic")
        else:
            print("❌ No ATF I/O or 2")

    # ==================================================
    # 🔹 STEP 4: ATF 6 FALLBACK
    # ==================================================
    if not io_found:
        atf6 = get_prop_image("ATF 6-Generic")

        if atf6:
            images.append({"type": "ATF 6-Generic", "url": atf6})
            seen.add(atf6)
            print("✅ ATF 6-Generic")
        else:
            print("❌ No ATF 6")

    else:
        print("⏭️ Skipping ATF 6 (I/O exists)")

    # ==================================================
    # 🔹 STEP 5: FALLBACK → GALLERY IMAGES
    # ==================================================
    if not images:
        print("⚠️ No structured images → fallback to gallery")

        for img in soup.find_all("img"):
            url = extract_best_image_from_tag(img)

            if url and "salsify" in url and url not in seen:
                seen.add(url)
                images.append({
                    "type": "Fallback",
                    "url": url
                })

    # ==================================================
    # 🔹 STEP 6: FINAL FALLBACK → JSON PARSE
    # ==================================================
    if not images:
        print("⚠️ No gallery images → parsing JSON")

        json_imgs = extract_from_json(html)

        for url in json_imgs[:10]:  # limit
            images.append({
                "type": "JSON",
                "url": url
            })

    print(f"\n✅ FINAL IMAGE COUNT: {len(images)}\n")

    return images
    
    # Method 1: Look in noscript tag (lazy loading fallback)
    noscript = container.find("noscript")
    if noscript:
        img_tag = noscript.find("img")
        if img_tag:
            srcset = img_tag.get("srcset", "")
            src = img_tag.get("src", "")
            
            if srcset and "salsify" in srcset:
                urls = [u.strip().split()[0] for u in srcset.split(",") if u.strip()]
                img_url = urls[-1] if urls else None
            elif src and "salsify" in src:
                img_url = src
    
    # Method 2: Look for img tag with data-testid="salsify-image"
    if not img_url:
        main_img = container.find("img", {"data-testid": "salsify-image"})
        if main_img:
            srcset = main_img.get("srcset", "")
            src = main_img.get("src", "")
            
            if srcset and "salsify" in srcset:
                urls = [u.strip().split()[0] for u in srcset.split(",") if u.strip()]
                img_url = urls[-1] if urls else None
            elif src and "salsify" in src:
                img_url = src
                
    if not property_map:
        print("⚠️ No asset containers found — using gallery fallback")
    
        imgs = soup.find_all("img")
    
        for img in imgs:
            src = img.get("src", "")
            if "salsify" in src and src not in seen_urls:
                seen_urls.add(src)
                images.append({
                    "type": "Fallback Image",
                    "url": src
                })
    # Method 3: Look for any img tag in the container with salsify URL
    if not img_url:
        for img_tag in container.find_all("img"):
            srcset = img_tag.get("srcset", "")
            src = img_tag.get("src", "")
            
            if srcset and "salsify" in srcset:
                urls = [u.strip().split()[0] for u in srcset.split(",") if u.strip()]
                img_url = urls[-1] if urls else None
                break
            elif src and "salsify" in src:
                img_url = src
                break
    
    return img_url

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
# ✅ RENDER IMAGE COMPARISON
# =========================================
def render_image_comparison_by_property(s_images, r_images):
    st.markdown("## Image Comparison ✅")
    st.write(f"Salsify Images: {len(s_images)} | CVS Images: {len(r_images)}")

    max_len = max(len(s_images), len(r_images))

    for i in range(max_len):
        col1, col2 = st.columns(2)

        if i < len(s_images):
            col1.markdown(f"**{s_images[i]['type']}**")
def load_image(url):
    try:
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept": "image/*,*/*;q=0.8"
        }
        response = requests.get(url, headers=headers, timeout=10)

        if response.status_code == 200:
            return Image.open(BytesIO(response.content))
        else:
            print(f"❌ Image failed: {response.status_code} → {url}")
            return None

    except Exception as e:
        print(f"❌ Image error: {e}")
        return None

        else:
            col1.write("❌ Missing in Salsify")

        if i < len(r_images):
            col2.markdown(f"**CVS Image {i+1}**")
            
        img_obj = load_image(r_images[i])
        
        if img_obj:
            col2.image(img_obj)
        else:
            col2.write("❌ Failed to load CVS image")

        else:
            col2.write("❌ Missing in CVS")

# =========================================
# ✅ SALSIFY TEXT
# =========================================
def get_salsify_text(url):
    """Extract title, description, and features from Salsify."""
    try:
        soup = get_soup(url)
        description = ""
        features = []
        
        rows = soup.find_all("tr")
        
        for row in rows:
            label = row.get_text(" ", strip=True).lower()
            
            if "general description" in label:
                content = row.find("span", {"data-testid": "property-content"})
                if content:
                    description = clean_text(content.get_text(" ", strip=True))
            
            if re.search(r'general feature \d+', label, re.IGNORECASE):
                clean = re.sub(r'general feature \d+\s*', '', label, flags=re.IGNORECASE)
                if clean.strip():
                    features.append(clean.strip())
        
        return {
            "description": description,
            "features": features[:5]
        }
    except:
        return {"description": "", "features": []}

# =========================================
# ✅ CVS TEXT
# =========================================
def get_cvs_text(html):
    """Extract title, description, and features from CVS."""
    description = ""
    features = []

    if not html:
        return {"description": "", "features": []}

    try:
        text = html.replace('\\"', '"')
        text = re.sub(r'\\n', ' ', text)

        desc_patterns = [
            r'vendorDetailsParagraph":"(.*?)"',
            r'"description":"(.*?)"',
            r'"longDescription":"(.*?)"'
        ]

        for pattern in desc_patterns:
            match = re.search(pattern, text, re.DOTALL)
            if match:
                description = match.group(1)
                break

        description = re.sub(r'\s+', ' ', description).strip()

        feature_matches = re.findall(
            r'([A-Z][A-Z\s\-]{5,}:\s[^"]+)',
            text
        )

        for f in feature_matches:
            clean_f = re.sub(r'\s+', ' ', f).strip()
            if len(clean_f) > 20:
                features.append(clean_f)

        features = list(dict.fromkeys(features))[:5]

    except Exception as e:
        print("CVS parse error:", e)

    return {
        "description": description,
        "features": features
    }

# =========================================
# ✅ TEXT NORMALIZATION
# =========================================
def normalize_text(text):
    text = str(text).lower()
    text = re.sub(r'[^a-z0-9\s]', '', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

# =========================================
# ✅ KEYWORD SCORE
# =========================================
def keyword_score(a, b):
    a = normalize_text(a)
    b = normalize_text(b)

    if not a or not b:
        return 0

    ratio = max(
        SequenceMatcher(None, a, b).ratio(),
        SequenceMatcher(None, a[:200], b[:200]).ratio()
    )

    a_words = set(a.split())
    b_words = set(b.split())

    overlap = len(a_words & b_words)
    total = len(a_words | b_words)

    word_score = (overlap / total) if total else 0

    final = (ratio * 0.6) + (word_score * 0.4)

    return int(final * 100)

# =========================================
# ✅ MAIN
# =========================================
if uploaded_file:

    df = pd.read_csv(uploaded_file)
    export_rows = []
    summary_rows = []

    for _, row in df.iterrows():

        st.subheader(f"SKU: {row['sku']}")

        full_html = get_html(row["retail_url"])

        try:
            # Get images
            s_images = get_salsify_images(row["salsify_url"])
            s_images = [img for img in s_images if img.get("url")]

            r_images = get_cvs_images(row["retail_url"])

            # Debug view
            with st.expander("🧺 Salsify Images", expanded=True):
                st.write("Total Salsify images:", len(s_images))
                cols = st.columns(3)
                
            print("\n🧠 PROPERTY MAP KEYS:")
            for k in property_map.keys():
                print(f"  → {k}")


                for i, img in enumerate(s_images):
                    cols[i % 3].image(img["url"], caption=img["type"], use_container_width=True)

            # Comparison
            render_image_comparison_by_property(s_images, r_images)

            # Image score
            img_score = 100 if len(s_images) == len(r_images) else int((min(len(s_images), len(r_images)) / max(len(s_images), len(r_images), 1)) * 100)
            st.write(f"✅ Image Match: {img_score}%")

            # Get text
            s_text = get_salsify_text(row["salsify_url"])
            r_text = get_cvs_text(full_html)

            # Title
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

            r_title_match = re.search(r'"productName":"(.*?)"', full_html)
            r_title = r_title_match.group(1) if r_title_match else ""
            r_title = re.sub(r'\s*-\s*CVS.*$', '', r_title).strip()

            c1, c2 = st.columns(2)
            c1.write(f"Salsify: {s_title}")
            c2.write(f"CVS: {r_title}")

            title_score = int(SequenceMatcher(None, s_title.lower(), r_title.lower()).ratio() * 100)
            st.write(f"✅ Title Match: {title_score}%")

            # Description
            st.markdown("## Description")
            c1, c2 = st.columns(2)
            c1.write(s_text.get("description", "")[:200])
            c2.write(r_text.get("description", "")[:200])

            desc_score = keyword_score(s_text.get("description", ""), r_text.get("description", ""))
            st.write(f"✅ Description Match: {desc_score}%")

            # Summary
            overall_score = int((title_score + desc_score + img_score) / 3)

            summary_rows.append({
                "SKU": row["sku"],
                "Title %": title_score,
                "Description %": desc_score,
                "Image %": img_score,
                "Overall %": overall_score
            })

        except Exception as e:
            st.error(f"❌ Error on SKU {row['sku']}: {e}")
            import traceback
            traceback.print_exc()
            continue

# =========================================
# ✅ EXPORT
# =========================================
if summary_rows:
    summary_df = pd.DataFrame(summary_rows)

    file_name = "pdp_qa_results.xlsx"

    with pd.ExcelWriter(file_name, engine="openpyxl") as writer:
        summary_df.to_excel(writer, index=False, sheet_name="Summary")

    with open(file_name, "rb") as f:
        download_placeholder.download_button(
            label="📥 Download Excel Report",
            data=f,
            file_name=file_name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
