import requests
from bs4 import BeautifulSoup
import re
from playwright.sync_api import sync_playwright

# URLs to compare
url_working = "https://sites.salsify.com/c59eb481-0fb4-407b-ac3d-710e4b28a712/83f32e36-ef43-47a1-92e5-8c9a07b01e56/product/01247-06/U-by-Kotex-Clean-andamp-Secure-Wrapped-Panty-Liners-Light-Absorbency-Long-Length-16-Count/"
url_broken = "https://sites.salsify.com/c59eb481-0fb4-407b-ac3d-710e4b28a712/83f32e36-ef43-47a1-92e5-8c9a07b01e56/product/19304-13/Poise-Daily-Liners-Incontinence-Panty-Liners-2-Drop-Very-Light-Absorbency-Long-Length-44-Count-of-Pantiliners/"

def get_html_playwright(url):
    """Get HTML using Playwright"""
    try:
        p = sync_playwright().start()
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, timeout=30000, wait_until="networkidle")
        
        for _ in range(5):
            page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
            page.wait_for_timeout(1000)
        
        html = page.content()
        page.close()
        browser.close()
        p.stop()
        return html
    except Exception as e:
        print(f"Error: {e}")
        return ""

def analyze_page(url, name):
    """Analyze a Salsify page"""
    print(f"\n{'='*80}")
    print(f"📊 ANALYZING: {name}")
    print(f"{'='*80}\n")
    
    html = get_html_playwright(url)
    soup = BeautifulSoup(html, "html.parser")
    
    # Find asset containers
    asset_containers = soup.find_all("div", {"class": "asset-list_images__2aKCB"})
    print(f"✅ Asset containers found: {len(asset_containers)}")
    
    if len(asset_containers) > 0:
        print(f"\n🔍 Container Details:")
        for idx, container in enumerate(asset_containers[:5]):
            aria_label = container.get("aria-label", "N/A")
            noscript = container.find("noscript")
            img_in_noscript = noscript.find("img") if noscript else None
            
            print(f"\n  Container {idx + 1}:")
            print(f"    aria-label: {aria_label}")
            print(f"    has noscript: {noscript is not None}")
            print(f"    has img in noscript: {img_in_noscript is not None}")
            
            if img_in_noscript:
                srcset = img_in_noscript.get("srcset", "")
                src = img_in_noscript.get("src", "")
                print(f"    srcset exists: {len(srcset) > 0}")
                print(f"    src exists: {len(src) > 0}")
                if srcset:
                    first_url = srcset.split(",")[0].strip().split()[0] if srcset else ""
                    print(f"    first srcset URL: {first_url[:100]}...")
    
    # All img tags with salsify
    all_imgs = soup.find_all("img")
    salsify_imgs = [img for img in all_imgs if "salsify" in (img.get("src", "") + img.get("srcset", ""))]
    print(f"\n✅ Total img tags: {len(all_imgs)}")
    print(f"✅ Salsify img tags: {len(salsify_imgs)}")
    
    # Save HTML for inspection
    filename = f"salsify_{name.replace(' ', '_').lower()}.html"
    with open(filename, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n💾 HTML saved to: {filename}")
    
    return {
        "containers": len(asset_containers),
        "total_imgs": len(all_imgs),
        "salsify_imgs": len(salsify_imgs)
    }

# Analyze both
working = analyze_page(url_working, "WORKING - U by Kotex")
broken = analyze_page(url_broken, "BROKEN - Poise")

print(f"\n{'='*80}")
print("📈 COMPARISON")
print(f"{'='*80}")
print(f"Working - Containers: {working['containers']}, Salsify imgs: {working['salsify_imgs']}")
print(f"Broken  - Containers: {broken['containers']}, Salsify imgs: {broken['salsify_imgs']}")
