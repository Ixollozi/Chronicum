from PIL import Image, ImageDraw
from pathlib import Path

src = Path(
    r"C:\Users\Sam\.cursor\projects\d-Cursor-Chronicum\assets"
    r"\c__Users_Sam_AppData_Roaming_Cursor_User_workspaceStorage_"
    r"b6d8dc26f0cc7352671588a290742554_images_Chronicum_logo-"
    r"a60196c5-05c9-4b19-90e6-904394e10a47.png"
)
app = Path(r"d:\Cursor\Chronicum\web\src\app")
pub = Path(r"d:\Cursor\Chronicum\web\public")

im = Image.open(src).convert("RGBA")
pixels = im.load()
w, h = im.size
min_x, min_y, max_x, max_y = w, h, 0, 0
for y in range(h):
    for x in range(w):
        r, g, b, a = pixels[x, y]
        if a > 10 and (r < 245 or g < 245 or b < 245):
            min_x = min(min_x, x)
            min_y = min(min_y, y)
            max_x = max(max_x, x)
            max_y = max(max_y, y)

pad = int(0.12 * max(max_x - min_x + 1, max_y - min_y + 1))
min_x = max(0, min_x - pad)
min_y = max(0, min_y - pad)
max_x = min(w - 1, max_x + pad)
max_y = min(h - 1, max_y + pad)
cw, ch = max_x - min_x + 1, max_y - min_y + 1
side = max(cw, ch)
cx = (min_x + max_x) // 2
cy = (min_y + max_y) // 2
left = max(0, cx - side // 2)
top = max(0, cy - side // 2)
right = min(w, left + side)
bottom = min(h, top + side)
left = max(0, right - side)
top = max(0, bottom - side)
cropped = im.crop((left, top, right, bottom))


def mark_on_white(size: int) -> Image.Image:
    """Opaque white square — for in-app logo / Apple (OS applies its own mask)."""
    base = Image.new("RGBA", (size, size), (255, 255, 255, 255))
    icon = cropped.resize((size, size), Image.Resampling.LANCZOS)
    base.paste(icon, (0, 0), icon)
    return base.convert("RGB")


def rounded_tab_icon(size: int, radius_ratio: float = 0.28) -> Image.Image:
    """White rounded tile + transparent corners so the browser tab isn't a sharp square."""
    radius = max(2, round(size * radius_ratio))
    inset = max(1, round(size * 0.08))
    inner = max(1, size - inset * 2)

    plate = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(plate)
    draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=(255, 255, 255, 255))

    mark = cropped.resize((inner, inner), Image.Resampling.LANCZOS).convert("RGBA")
    pixels = mark.load()
    for y in range(inner):
        for x in range(inner):
            r, g, b, a = pixels[x, y]
            if a < 8 or (r > 245 and g > 245 and b > 245):
                pixels[x, y] = (255, 255, 255, 0)
    plate.alpha_composite(mark, (inset, inset))
    return plate


def save_png(path: Path, size: int, rounded: bool) -> None:
    img = rounded_tab_icon(size) if rounded else mark_on_white(size)
    img.save(path, "PNG", optimize=True)
    print("wrote", path, size, "rounded" if rounded else "square")


logo = mark_on_white(512)
logo.save(pub / "logo.png", "PNG", optimize=True)
print("wrote", pub / "logo.png")

save_png(app / "icon.png", 512, rounded=True)
save_png(app / "apple-icon.png", 180, rounded=False)
save_png(pub / "icon-512.png", 512, rounded=True)
save_png(pub / "icon-192.png", 192, rounded=True)
save_png(pub / "apple-touch-icon.png", 180, rounded=False)

# Don't put favicon.ico in app/: Next then prefers it over PNG and Chrome draws a sharp square.
# Serve a 32px PNG-in-ICO from public for the default /favicon.ico request.
favicon_sizes = [32, 48]
favicon_frames = [rounded_tab_icon(s) for s in favicon_sizes]
favicon_frames[0].save(
    pub / "favicon.ico",
    format="ICO",
    sizes=[(s, s) for s in favicon_sizes],
    append_images=favicon_frames[1:],
)
print("wrote", pub / "favicon.ico")

og = Image.new("RGB", (1200, 630), (10, 12, 11))
rounded = rounded_tab_icon(400)
# flatten rounded mark onto dark OG background
og_canvas = Image.new("RGBA", (1200, 630), (10, 12, 11, 255))
og_canvas.alpha_composite(rounded, ((1200 - 400) // 2, (630 - 400) // 2))
og_canvas.convert("RGB").save(app / "opengraph-image.png", "PNG", optimize=True)
print("wrote", app / "opengraph-image.png")
print("crop box", left, top, right, bottom)
