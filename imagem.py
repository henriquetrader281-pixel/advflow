"""Renderizador editorial leve para as artes do AdvFlow.

A primeira peça usa fotografia como capa; os slides seguintes usam layout editorial
tipográfico para manter legibilidade. OpenCV é opcional: sem ele, o foco fica no centro.
"""
import os
import re
import tempfile
from functools import lru_cache
import requests
from PIL import Image, ImageColor, ImageDraw, ImageFont

W = 1080
PHI = 0.618
FONT_DIR = os.path.join(os.path.dirname(__file__), "fonts")
FONT_CACHE_DIR = os.path.join(tempfile.gettempdir(), "advflow-font-cache")
DEFAULT_TYPOGRAPHY = "Clássico imponente — Playfair Display + Montserrat"

TYPOGRAPHY_PAIRS = {
    "Clássico imponente — Playfair Display + Montserrat": {
        "title": "Playfair Display", "body": "Montserrat",
        "description": "Serifada expressiva nos títulos; sans-serif limpa no corpo."
    },
    "Clássico editorial — Cormorant Garamond + Inter": {
        "title": "Cormorant Garamond", "body": "Inter",
        "description": "Editorial, elegante e com leitura confortável em telas."
    },
    "Contemporâneo refinado — Cinzel + Lato": {
        "title": "Cinzel", "body": "Lato",
        "description": "Título clássico em caixa alta; corpo discreto e legível."
    },
    "Moderno limpo — Playfair Display + Open Sans": {
        "title": "Playfair Display", "body": "Open Sans",
        "description": "Contraste entre título clássico e corpo funcional."
    },
}

FONT_SOURCES = {
    "Playfair Display": "https://raw.githubusercontent.com/google/fonts/main/ofl/playfairdisplay/PlayfairDisplay%5Bwght%5D.ttf",
    "Montserrat": "https://raw.githubusercontent.com/google/fonts/main/ofl/montserrat/Montserrat%5Bwght%5D.ttf",
    "Cormorant Garamond": "https://raw.githubusercontent.com/google/fonts/main/ofl/cormorantgaramond/CormorantGaramond%5Bwght%5D.ttf",
    "Inter": "https://raw.githubusercontent.com/google/fonts/main/ofl/inter/Inter%5Bopsz%2Cwght%5D.ttf",
    "Cinzel": "https://raw.githubusercontent.com/google/fonts/main/ofl/cinzel/Cinzel%5Bwght%5D.ttf",
    "Lato": "https://raw.githubusercontent.com/google/fonts/main/ofl/lato/Lato-Regular.ttf",
    "Lato Bold": "https://raw.githubusercontent.com/google/fonts/main/ofl/lato/Lato-Bold.ttf",
    "Open Sans": "https://raw.githubusercontent.com/google/fonts/main/ofl/opensans/OpenSans%5Bwdth%2Cwght%5D.ttf",
}

@lru_cache(maxsize=16)
def _download_font(family):
    """Cache open-license fonts outside the repository; gracefully allow offline fallback."""
    url = FONT_SOURCES.get(family)
    if not url:
        return None
    try:
        os.makedirs(FONT_CACHE_DIR, exist_ok=True)
        filename = re.sub(r"[^a-zA-Z0-9_-]", "_", family) + ".ttf"
        path = os.path.join(FONT_CACHE_DIR, filename)
        if os.path.isfile(path) and os.path.getsize(path) > 5000:
            return path
        response = requests.get(url, timeout=8, headers={"User-Agent": "AdvFlow/1.0"})
        response.raise_for_status()
        if len(response.content) < 5000:
            return None
        tmp_path = path + ".tmp"
        with open(tmp_path, "wb") as font_file:
            font_file.write(response.content)
        os.replace(tmp_path, path)
        return path
    except Exception:
        return None

def _system_fallbacks(role):
    candidates = ["titulo.ttf", "texto.ttf"] if role == "title" else ["texto.ttf", "titulo.ttf"]
    paths = [os.path.join(FONT_DIR, name) for name in candidates]
    if role == "title":
        paths.extend([
            "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "C:/Windows/Fonts/georgiab.ttf",
            "C:/Windows/Fonts/arialbd.ttf",
        ])
    else:
        paths.extend([
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
            "C:/Windows/Fonts/arial.ttf",
        ])
    return paths
PALETTES = {
    "Creme e dourado": ("#f6f1e9", "#14213d", "#b8955a"),
    "Grafite e verde": ("#f2f5f4", "#14201e", "#0f6b5e"),
    "Azul institucional": ("#eef3fb", "#10264a", "#2f6fd1"),
    "Vinho e off-white": ("#f7f2ee", "#2b1418", "#7a1f35"),
}

@lru_cache(maxsize=256)
def font(sz, bold=False, typography=DEFAULT_TYPOGRAPHY, role=None):
    role = role or ("title" if bold else "body")
    pair = TYPOGRAPHY_PAIRS.get(typography, TYPOGRAPHY_PAIRS[DEFAULT_TYPOGRAPHY])
    family = pair.get(role, pair["body"])
    if family == "Lato" and bold:
        path = _download_font("Lato Bold")
    else:
        path = _download_font(family)
    try:
        if path:
            loaded = ImageFont.truetype(path, int(sz))
            # Prefer a heavier variable-font instance for headings when supported by Pillow.
            try:
                axes = loaded.get_variation_axes()
                values = []
                for axis in axes:
                    tag = str(axis.get("tag", "")).lower()
                    if tag == "wght":
                        values.append(float(axis.get("maximum") if bold else axis.get("default", axis.get("minimum", 400))))
                    elif tag == "opsz":
                        values.append(float(min(max(int(sz), axis.get("minimum", 14)), axis.get("maximum", 32))))
                    else:
                        values.append(float(axis.get("default", axis.get("minimum", 0))))
                if values:
                    loaded.set_variation_by_axes(values)
            except Exception:
                pass
            return loaded
    except OSError:
        pass
    for fallback in _system_fallbacks(role):
        if os.path.isfile(fallback):
            try:
                return ImageFont.truetype(fallback, int(sz))
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

def fit(draw, text, max_width, max_height, sizes, bold=False, leading=1.2, max_lines=4, typography=DEFAULT_TYPOGRAPHY):
    for size in sizes:
        fnt = font(size, bold, typography, "title" if bold else "body")
        lines = wrap(draw, text, fnt, max_width)
        step = int(size * leading)
        if len(lines) <= max_lines and len(lines) * step <= max_height:
            return fnt, lines, step
    size = sizes[-1]
    fnt = font(size, bold, typography, "title" if bold else "body")
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

def render(slide, index, total, height, palette, office="", oab="", bg=None, logo=None, focus=(.5,.4), side="Centro", typography=DEFAULT_TYPOGRAPHY):
    bgc, ink, accent = tuple(palette)
    image = Image.new("RGB", (W, height), bgc)
    draw = ImageDraw.Draw(image)
    title = str(slide.get("titulo") or "").strip()
    body = str(slide.get("texto") or slide.get("copy") or slide.get("corpo") or "").strip()
    generic_titles = {"gancho", "introdução", "introducao", "contexto", "explicação", "explicacao", "aplicação", "aplicacao", "conclusão", "conclusao", "fechamento", "síntese", "sintese", "slide", f"slide {index+1}"}
    if title.casefold().strip(" :.-") in generic_titles:
        first_sentence = re.split(r"(?<=[.!?])\s+", body, maxsplit=1)[0].strip()
        title = first_sentence if first_sentence and len(first_sentence) <= 95 else "O que isso significa na prática?"
    kicker = str(slide.get("kicker") or "CONTEÚDO INFORMATIVO").upper()[:72]
    muted = tuple(int(a * .72 + b * .28) for a,b in zip(ImageColor.getrgb(ink), ImageColor.getrgb(bgc)))
    disclaimer = "Informação geral. Avalie o contexto e as fontes aplicáveis."

    if bg is not None:
        # Square artwork needs a shorter photo zone to preserve text space.
        photo_fraction = .50 if height <= W else PHI
        photo_h = int(height * photo_fraction)
        target_x = {"Esquerda":.382, "Centro":.5, "Direita":.618}.get(side,.5)
        image.paste(cover_crop(bg.convert("RGB"), W, photo_h, focus[0], focus[1], target_x, .382), (0,0))
        top, bottom, x0, x1 = photo_h - 60, height - 54, 108, W - 108
        draw.rounded_rectangle((54,top,W-54,bottom), radius=32, fill=bgc, outline=accent, width=3)
        y = top + 42
        draw.text((x0,y),kicker,font=font(22, typography=typography, role="body"),fill=accent); y += 42
        draw.rectangle((x0,y,x0+140,y+6),fill=accent); y += 30
        max_y = bottom - 130
        title_font, title_lines, title_step = fit(draw,title,x1-x0,(max_y-y)*.63,[78,72,66,60,54],True,1.15,3,typography)
        for line in title_lines:
            draw.text((x0,y),line,font=title_font,fill=ink); y += title_step
        y += 12
        body_font, body_lines, body_step = fit(draw,body,x1-x0,max(30,max_y-y),[30,28,26,24,22],False,1.35,4,typography)
        for line in body_lines:
            draw.text((x0,y),line,font=body_font,fill=muted); y += body_step
        draw.rectangle((x0,bottom-120,x1,bottom-118),fill=accent)
        if total > 1:
            draw.text((x1-100,bottom-98),f"{index+1:02d}/{total:02d}",font=font(24,True, typography=typography, role="title"),fill=accent)
        draw.text((x0,bottom-55),disclaimer,font=font(18, typography=typography, role="body"),fill=muted)
    else:
        x0, x1 = 88, W-88
        draw.rectangle((0,0,W,14),fill=accent)
        y = 105
        if total > 1:
            draw.text((x0,y),f"{index+1:02d}",font=font(90,True, typography=typography, role="title"),fill=accent)
            y += 135
        draw.text((x0,y),kicker,font=font(22, typography=typography, role="body"),fill=accent); y += 48
        draw.rectangle((x0,y,x0+125,y+6),fill=accent); y += 38
        title_font,title_lines,title_step=fit(draw,title,x1-x0,height*.30,[72,66,60,56,50],True,1.16,3,typography)
        for line in title_lines:
            draw.text((x0,y),line,font=title_font,fill=ink); y += title_step
        y += 32
        body_font,body_lines,body_step=fit(draw,body,x1-x0,max(80,height-300-y),[29,27,25,24,22],False,1.4,10,typography)
        for line in body_lines:
            draw.text((x0,y),line,font=body_font,fill=muted); y += body_step
        draw.text((x0,height-78),disclaimer,font=font(19, typography=typography, role="body"),fill=muted)
    if logo is not None:
        try:
            mark = logo.convert("RGBA")
            mark.thumbnail((170,75),Image.Resampling.LANCZOS)
            image.paste(mark,(W-88-mark.width,35),mark)
        except Exception:
            pass
    return image
