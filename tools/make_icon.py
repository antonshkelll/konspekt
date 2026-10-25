"""Рисует иконку приложения: облачко диалога с волной звука на фиолетовом фоне."""
from pathlib import Path

from PIL import Image, ImageDraw

S = 1024  # рисуем крупно, потом уменьшаем — так края сглаживаются
OUT = Path(__file__).resolve().parent.parent / "assets"


def gradient(size, top, bottom):
    img = Image.new("RGB", (size, size))
    px = img.load()
    for y in range(size):
        for x in range(size):
            t = (x + y) / (2 * size)
            px[x, y] = tuple(int(a + (b - a) * t) for a, b in zip(top, bottom))
    return img


def main():
    OUT.mkdir(exist_ok=True)
    bg = gradient(S, (91, 91, 240), (168, 85, 247)).convert("RGBA")
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, S - 1, S - 1), radius=230, fill=255)
    icon = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    icon.paste(bg, mask=mask)

    d = ImageDraw.Draw(icon)
    # облачко диалога
    d.rounded_rectangle((190, 230, 834, 700), radius=150, fill="white")
    d.polygon([(330, 660), (290, 840), (480, 690)], fill="white")
    # звуковая волна внутри облачка
    bars = [90, 170, 250, 150, 220, 110]
    cx, cy, w, gap = 512, 465, 46, 26
    x0 = cx - (len(bars) * w + (len(bars) - 1) * gap) // 2
    for i, h in enumerate(bars):
        x = x0 + i * (w + gap)
        d.rounded_rectangle((x, cy - h // 2, x + w, cy + h // 2), radius=w // 2, fill=(91, 91, 240))
    # точка «идёт запись»
    d.ellipse((740, 150, 900, 310), fill=(229, 72, 77), outline="white", width=28)

    icon.resize((256, 256), Image.LANCZOS).save(OUT / "icon.png")
    icon.save(OUT / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("icon ->", OUT)


if __name__ == "__main__":
    main()
