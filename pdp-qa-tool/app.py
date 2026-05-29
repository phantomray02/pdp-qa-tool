
def get_cvs_images(url):
    try:
        API_KEY = "YOUR_API_KEY_HERE"

        payload = {
            "api_key": API_KEY,
            "url": url,
            "render": "true"  # ✅ THIS enables JS rendering
        }

        response = requests.get("http://api.scraperapi.com", params=payload, timeout=30)
        html = response.text

        # ✅ Now thumbnails exist in HTML
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")

        imgs = soup.find_all("img")

        thumbs = []

        for img in imgs:
            src = img.get("src") or ""
            width = img.get("width")
            height = img.get("height")

            try:
                if width and height:
                    if int(width) <= 300 and int(height) <= 300:
                        if src.startswith("http"):
                            thumbs.append(src)
            except:
                continue

        # remove duplicates
        return list(dict.fromkeys(thumbs))

    except:
        return []
