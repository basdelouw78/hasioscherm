#!/usr/bin/env python3
"""Maakt uitslijpmal.pdf: een A4 (liggend) op ware grootte met de uitsparing in de
muur, de opening van de bak en de plek van het glas.

De maten komen uit hasioscherm-frame.scad (via OpenSCAD), zodat mal en bak altijd
bij elkaar passen. Gebruik: python3 maak_uitslijpmal.py
"""
import re
import subprocess
import sys
from pathlib import Path

from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

HIER = Path(__file__).resolve().parent
SCAD = HIER / "hasioscherm-frame.scad"
SPECIE = 2   # ruimte rondom de flens voor specie/gips (mm)


def maten() -> dict:
    out = subprocess.run(["openscad", "-o", "/tmp/_maten.csg", "-D", 'deel="maten"', str(SCAD)],
                         capture_output=True, text=True).stderr
    m = re.search(r"ECHO: (.*)", out)
    if not m:
        sys.exit("Kon maten niet uit OpenSCAD halen:\n" + out)
    return {k.strip(): float(v) for k, v in (p.split("=") for p in m.group(1).split(","))}


def main() -> None:
    M = maten()
    sb, sh = M["flens_b"] + 2 * SPECIE, M["flens_h"] + 2 * SPECIE
    diep = M["bak_d"] + 4
    pw, ph = landscape(A4)
    c = canvas.Canvas(str(HIER / "uitslijpmal.pdf"), pagesize=landscape(A4))
    c.setTitle("hasioscherm - uitslijpmal (afdrukken op 100%)")
    cx, cy = pw / 2, ph / 2
    P = lambda x, y: (cx + x * mm, cy + y * mm)

    def rect(b, h, fill=None, dash=None, lw=0.8, rgb=(0, 0, 0)):
        c.setLineWidth(lw)
        c.setStrokeColorRGB(*rgb)
        c.setDash(*dash) if dash else c.setDash()
        if fill:
            c.setFillColorRGB(*fill)
        x0, y0 = P(-b / 2, -h / 2)
        c.rect(x0, y0, b * mm, h * mm, stroke=1, fill=1 if fill else 0)
        c.setDash()

    rect(sb, sh, fill=(0.93, 0.95, 1))                                     # uitsparing
    rect(248.1, 166.9, dash=(2, 2), lw=0.5, rgb=(0.2, 0.2, 0.2))          # glas
    rect(M["opening_b"], M["opening_h"], lw=1.2, rgb=(0.8, 0.3, 0.1))      # opening bak

    c.setStrokeColorRGB(0.5, 0.5, 0.5)
    c.setLineWidth(0.3)
    c.line(*P(-sb / 2, 0), *P(sb / 2, 0))
    c.line(*P(0, -sh / 2), *P(0, sh / 2))

    c.setFillColorRGB(0, 0, 0)
    c.setFont("Helvetica-Bold", 13)
    c.drawCentredString(*P(0, 30), "hasioscherm - uitslijpmal (liggend)")
    c.setFont("Helvetica", 9)
    regels = [
        f"Lichtblauw = uitsparing in de steen: {sb:.0f} x {sh:.0f} mm, {diep:.0f} mm diep vanaf het afgewerkte stucwerk.",
        f"Oranje = opening van de bak ({M['opening_b']:.1f} x {M['opening_h']:.1f} mm), komt gelijk met het stucwerk.",
        "Stippellijn = waar het glas komt te liggen (248,1 x 166,9 mm). Het glas dekt de bakrand af.",
        "Horizontale lijn waterpas zetten. Afdrukken op 100% (niet 'passend maken'): controleer de 100 mm-lijn.",
    ]
    for i, r in enumerate(regels):
        c.drawCentredString(*P(0, 20 - i * 5.5), r)

    c.setLineWidth(1)
    c.setStrokeColorRGB(0, 0, 0)
    c.line(*P(-50, -30), *P(50, -30))
    for x in (-50, 50):
        c.line(*P(x, -32), *P(x, -28))
    c.drawCentredString(*P(0, -36), "deze lijn moet precies 100 mm zijn")
    c.showPage()
    c.save()
    print(f"uitslijpmal.pdf gemaakt: uitsparing {sb:.0f} x {sh:.0f} x {diep:.0f} mm")


if __name__ == "__main__":
    main()
