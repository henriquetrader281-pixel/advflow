"""Renderizador editorial leve para as artes do AdvFlow.

A primeira peça usa fotografia como capa; os slides seguintes usam layout editorial
tipográfico para manter legibilidade. OpenCV é opcional: sem ele, o foco fica no centro.
"""
import os
from functools import lru_cache
from PIL import Image, ImageColor, ImageDraw, ImageFont

W = 1080
PHI = 0.618
FONT_DIR = os.path.join(os.path.dirname(__file__), "fonts")
PALETTES = {
    "Creme e dourado": ("#f6f1e9", "#14213d", "#b8955a"),
    "Grafite e verde": ("#f2f5f4", "#14201e", "#0f6b5e"),
    "Azul institucional": ("#eef3fb", "#10264a", "#2f6fd1"),
    "Vinho e off-white": ("#f7f2ee", "#2b1418", "#7a1f35"),
}

@lru_cache(maxsize=64)
def font(sz, bold=False):
    names = (
        [os.path.join(FONT_DIR, "titulo.ttf"), "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
        if bold else
        [os.path.join(FONT_DIR, "texto.ttf"), "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
    )
    for path in names:
        if os.path.isfile(path):
            try:
                return ImageFont.truetype(path, sz)
            except OSError:
                pass
    return ImageFont.load_default()

def wrap(draw, text, fnt, max_width):
    lines, line = [], ""
    for word in str(text or "").split():
        trial = (line + " " + word).strip()
        if draw.textlength(trial, font=fnt) > max_width and line:
            lines.append(line)
            line = word
        else:
            line = trial
    if line:
        lines.append(line)
    return lines

def fit(draw, text, max_width, max_height, sizes, bold=False, leading=1.2, max_lines=4):
    for size in sizes:
        fnt = font(size, bold)
        lines = wrap(draw, text, fnt, max_width)
        step = int(size * leading)
        if len(lines) <= max_lines and len(lines) * step <= max_height:
            return fnt, lines, step
    size = sizes[-1]
    fnt = font(size, bold)
    lines = wrap(draw, text, fnt, max_width)
    step = int(size * leading)
    count = max(1, min(max_lines, int(max_height // max(step, 1))))
    if len(lines) > count:
        lines = lines[:count]
        lines[-1] = lines[-1].rstrip(".,; ") + "…"
    return fnt, lines, step

def cover_crop(image, width, height, focus_x=.5, focus_y=.4, target_x=.5, target_y=.382):
    scale = max(width / image.width, height / image.height)
    image = image.resize((round(image.width * scale), round(image.height * scale)), Image.Resampling.LANCZOS)
    ox = min(max(focus_x * image.width - target_x * width, 0), image.width - width)
    oy = min(max(focus_y * image.height - target_y * height, 0), image.height - height)
    return image.crop((int(ox), int(oy), int(ox) + width, int(oy) + height))

def autofoco(image):
    """Detecta rosto se OpenCV já estiver instalado; nunca é obrigatório."""
    try:
        import cv2
        import numpy as np
        gray = cv2.cvtColor(np.array(image.convert("RGB")), cv2.COLOR_RGB2GRAY)
        cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        faces = cascade.detectMultiScale(gray, 1.1, 5)
        if len(faces):
            x, y, w, h = max(faces, key=lambda face: face[2] * face[3])
            return (x + w / 2) / image.width, (y + h / 2) / image.height
    except Exception:
        pass
    return None

def render(slide, index, total, height, palette, office="", oab="", bg=None, logo=None, focus=(.5,.4), side="Centro"):
    bgc, ink, accent = tuple(palette)
    image = Image.new("RGB", (W, height), bgc)
    draw = ImageDraw.Draw(image)
    title = str(slide.get("titulo") or "")
    body = str(slide.get("texto") or slide.get("copy") or slide.get("corpo") or "")
    kicker = str(slide.get("kicker") or "CONTEÚDO INFORMATIVO").upper()[:72]
    muted = tuple(int(a * .72 + b * .28) for a,b in zip(ImageColor.getrgb(ink), ImageColor.getrgb(bgc)))
    footer = str(office or "CONTEÚDO INFORMATIVO") + (f" · OAB {oab}" if oab else "")
    disclaimer = "Informação geral. Avalie o contexto e as fontes aplicáveis."

    if bg is not None:
        photo_h = int(height * PHI)
        target_x = {"Esquerda":.382, "Centro":.5, "Direita":.618}.get(side,.5)
        image.paste(cover_crop(bg.convert("RGB"), W, photo_h, focus[0], focus[1], target_x, .382), (0,0))
        top, bottom, x0, x1 = photo_h - 60, height - 54, 108, W - 108
        draw.rounded_rectangle((54,top,W-54,bottom), radius=32, fill=bgc, outline=accent, width=3)
        y = top + 42
        draw.text((x0,y),kicker,font=font(22),fill=accent); y += 42
        draw.rectangle((x0,y,x0+140,y+6),fill=accent); y += 30
        max_y = bottom - 130
        title_font, title_lines, title_step = fit(draw,title,x1-x0,(max_y-y)*.63,[66,60,54,48,42],True,1.15,3)
        for line in title_lines:
            draw.text((x0,y),line,font=title_font,fill=ink); y += title_step
        y += 12
        body_font, body_lines, body_step = fit(draw,body,x1-x0,max(30,max_y-y),[32,29,26,23],False,1.35,4)
        for line in body_lines:
            draw.text((x0,y),line,font=body_font,fill=muted); y += body_step
        draw.rectangle((x0,bottom-120,x1,bottom-118),fill=accent)
        draw.text((x0,bottom-98),footer[:72],font=font(24,True),fill=ink)
        if total > 1:
            draw.text((x1-100,bottom-98),f"{index+1:02d}/{total:02d}",font=font(24,True),fill=accent)
        draw.text((x0,bottom-55),disclaimer,font=font(18),fill=muted)
    else:
        x0, x1 = 88, W-88
        draw.rectangle((0,0,W,14),fill=accent)
        y = 105
        if total > 1:
            draw.text((x0,y),f"{index+1:02d}",font=font(90,True),fill=accent)
            y += 135
        draw.text((x0,y),kicker,font=font(22),fill=accent); y += 48
        draw.rectangle((x0,y,x0+125,y+6),fill=accent); y += 38
        title_font,title_lines,title_step=fit(draw,title,x1-x0,height*.30,[72,64,58,52,46],True,1.16,4)
        for line in title_lines:
            draw.text((x0,y),line,font=title_font,fill=ink); y += title_step
        y += 32
        body_font,body_lines,body_step=fit(draw,body,x1-x0,max(80,height-300-y),[42,38,34,30,27],False,1.4,12)
        for line in body_lines:
            draw.text((x0,y),line,font=body_font,fill=muted); y += body_step
        draw.rectangle((x0,height-150,x1,height-148),fill=accent)
        draw.text((x0,height-126),footer[:72],font=font(26,True),fill=ink)
        draw.text((x0,height-78),disclaimer,font=font(19),fill=muted)
    if logo is not None:
        try:
            mark = logo.convert("RGBA")
            mark.thumbnail((170,75),Image.Resampling.LANCZOS)
            image.paste(mark,(W-88-mark.width,35),mark)
        except Exception:
            pass
    return image
