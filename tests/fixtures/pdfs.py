"""Generated PDF fixtures: a text-layer lease and a scanned (image-only) twin."""

import io
import textwrap

from fpdf import FPDF
from PIL import Image, ImageDraw, ImageFont


def make_text_pdf(text: str) -> bytes:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.multi_cell(w=180, text=text)
    return bytes(pdf.output())


def make_scanned_pdf(text: str) -> bytes:
    """The same content rasterised: a page image with no text layer."""
    image = Image.new("RGB", (1200, 1600), "white")
    font = ImageFont.load_default(size=32)
    wrapped = textwrap.fill(text, width=60)
    ImageDraw.Draw(image).multiline_text((60, 60), wrapped, fill="black", font=font)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    pdf = FPDF(unit="pt", format=(600, 800))
    pdf.add_page()
    pdf.image(buffer, x=0, y=0, w=600)
    return bytes(pdf.output())
