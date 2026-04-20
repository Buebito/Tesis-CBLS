"""Convierte reporte_modelo_financiero.md a PDF usando markdown + weasyprint.

Uso:
    python generate_pdf.py [input.md] [output.pdf]

Por defecto lee reporte_modelo_financiero.md y genera reporte_modelo_financiero.pdf
"""
import sys
import os
import base64
import mimetypes
import re

import markdown
from weasyprint import HTML


# ── CSS profesional ──
CSS = """
@page {
    size: A4;
    margin: 2cm 2.5cm;
    @bottom-center {
        content: "Pagina " counter(page) " de " counter(pages);
        font-size: 9px;
        color: #888;
    }
}

body {
    font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif;
    font-size: 11pt;
    line-height: 1.55;
    color: #222;
    max-width: 100%;
}

h1 {
    font-size: 22pt;
    color: #003049;
    border-bottom: 3px solid #003049;
    padding-bottom: 8px;
    margin-top: 0;
}

h2 {
    font-size: 16pt;
    color: #003049;
    border-bottom: 1.5px solid #d62828;
    padding-bottom: 5px;
    margin-top: 28px;
    page-break-after: avoid;
}

h3 {
    font-size: 13pt;
    color: #d62828;
    margin-top: 20px;
    page-break-after: avoid;
}

blockquote {
    border-left: 4px solid #f77f00;
    margin: 12px 0;
    padding: 8px 16px;
    background: #fff8f0;
    font-size: 10pt;
}

blockquote p { margin: 4px 0; }

table {
    width: 100%;
    border-collapse: collapse;
    margin: 14px 0;
    font-size: 9.5pt;
    page-break-inside: avoid;
}

th {
    background: #003049;
    color: white;
    padding: 7px 10px;
    text-align: left;
    font-weight: 600;
}

td {
    padding: 6px 10px;
    border-bottom: 1px solid #ddd;
}

tr:nth-child(even) td {
    background: #f7f7f7;
}

code {
    font-family: 'Courier New', monospace;
    background: #f4f4f4;
    padding: 2px 5px;
    border-radius: 3px;
    font-size: 9pt;
}

pre {
    background: #f4f4f4;
    padding: 12px 16px;
    border-radius: 5px;
    font-size: 9pt;
    overflow-x: auto;
    page-break-inside: avoid;
}

pre code {
    background: none;
    padding: 0;
}

img {
    max-width: 100%;
    height: auto;
    display: block;
    margin: 14px auto;
    page-break-inside: avoid;
}

hr {
    border: none;
    border-top: 1px solid #ccc;
    margin: 24px 0;
}

p { margin: 8px 0; }

strong { color: #003049; }

em { font-style: italic; }
"""


def embed_images(html: str, base_dir: str) -> str:
    """Reemplaza src de imagenes locales con data URIs base64."""
    def replace_src(match):
        src = match.group(1)
        # Skip URLs
        if src.startswith(("http://", "https://", "data:")):
            return match.group(0)
        path = os.path.join(base_dir, src)
        if os.path.isfile(path):
            mime, _ = mimetypes.guess_type(path)
            if mime is None:
                mime = "image/png"
            with open(path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
            return f'src="data:{mime};base64,{b64}"'
        return match.group(0)

    return re.sub(r'src="([^"]+)"', replace_src, html)


def md_to_pdf(md_path: str, pdf_path: str):
    """Lee markdown, convierte a HTML con estilos, genera PDF."""
    base_dir = os.path.dirname(os.path.abspath(md_path))

    with open(md_path, "r", encoding="utf-8") as f:
        md_text = f.read()

    # Markdown -> HTML
    body = markdown.markdown(
        md_text,
        extensions=["tables", "fenced_code", "codehilite", "toc"],
    )

    # Embeber imagenes como base64
    body = embed_images(body, base_dir)

    # HTML completo
    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<style>{CSS}</style>
</head>
<body>
{body}
</body>
</html>"""

    # Generar PDF
    HTML(string=html, base_url=base_dir).write_pdf(pdf_path)
    print(f"PDF generado: {pdf_path}")


if __name__ == "__main__":
    md_in = sys.argv[1] if len(sys.argv) > 1 else "reporte_modelo_financiero.md"
    pdf_out = sys.argv[2] if len(sys.argv) > 2 else md_in.replace(".md", ".pdf")
    md_to_pdf(md_in, pdf_out)
