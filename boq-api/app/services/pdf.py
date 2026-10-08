"""Printable Issue Note + Gate Pass. The QR code opens the issue note in the app so the site can receive it."""

import io

from reportlab.graphics import renderPDF
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.core.config import settings


def _qty(v) -> str:
    if v is None:
        return "—"
    return f"{float(v):,.3f}".rstrip("0").rstrip(".")


def _money(v) -> str:
    return "—" if v is None else f"₹ {float(v):,.2f}"


def _qr(text: str, size: float = 30 * mm) -> Drawing:
    w = QrCodeWidget(text)
    x1, y1, x2, y2 = w.getBounds()
    d = Drawing(size, size, transform=[size / (x2 - x1), 0, 0, size / (y2 - y1), 0, 0])
    d.add(w)
    return d


def issue_note_pdf(note, show_finance: bool) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=14 * mm, rightMargin=14 * mm, topMargin=12 * mm, bottomMargin=12 * mm)
    ss = getSampleStyleSheet()
    story = []

    url = f"{settings.PUBLIC_UI_URL.rstrip('/')}/issue-notes/{note.id}"
    head = Table(
        [
            [
                Paragraph(f"<b>{settings.COMPANY_NAME}</b><br/>Material Issue Note &amp; Gate Pass", ss["Title"]),
                _qr(url),
            ]
        ],
        colWidths=[150 * mm, 32 * mm],
    )
    head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    story += [head, Spacer(1, 4 * mm)]

    info = [
        ["Issue No.", note.issue_no, "Date", note.issue_date.strftime("%d-%b-%Y")],
        ["Request No.", note.request_no, "Status", note.status.replace("_", " ")],
        ["From", note.from_store.name, "To", f"{note.site.name} ({note.project_name})"],
        ["Vehicle", note.vehicle_no or "—", "Driver", f"{note.driver_name or '—'} {note.driver_phone or ''}".strip()],
    ]
    t = Table(info, colWidths=[28 * mm, 62 * mm, 22 * mm, 70 * mm])
    t.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
                ("BACKGROUND", (0, 0), (0, -1), colors.whitesmoke),
                ("BACKGROUND", (2, 0), (2, -1), colors.whitesmoke),
            ]
        )
    )
    story += [t, Spacer(1, 5 * mm)]

    header = ["#", "Material", "UOM", "Issued", "Received", "Short", "Damaged"]
    if show_finance:
        header += ["Rate", "Amount"]
    rows = [header]
    for i in note.items:
        row = [
            str(i.line_no),
            Paragraph(f"{i.material.name}<br/><font size=7 color='#666666'>{i.material.code}</font>", ss["BodyText"]),
            i.material.uom,
            _qty(i.issued_qty),
            _qty(i.received_qty),
            _qty(i.shortage_qty) if i.received_qty is not None else "",
            _qty(i.damaged_qty) if i.received_qty is not None else "",
        ]
        if show_finance:
            row += [_money(i.rate), _money(i.amount)]
        rows.append(row)
    if show_finance:
        rows.append(["", "Total", "", "", "", "", "", "", _money(note.total_value)])
    widths = [8 * mm, 62 * mm, 14 * mm, 20 * mm, 20 * mm, 16 * mm, 18 * mm] + ([22 * mm, 26 * mm] if show_finance else [])
    if not show_finance:
        widths[1] = 110 * mm
    items = Table(rows, colWidths=widths, repeatRows=1)
    items.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F3A5F")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                ("ALIGN", (3, 1), (-1, -1), "RIGHT"),
                ("GRID", (0, 0), (-1, -1), 0.3, colors.grey),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    story += [items, Spacer(1, 6 * mm)]
    if note.remarks:
        story += [Paragraph(f"<b>Remarks:</b> {note.remarks}", ss["BodyText"]), Spacer(1, 4 * mm)]

    sign = Table(
        [
            ["Issued by (Store)", "Security / Gate", "Received by (Site)"],
            ["\n\n\n" + (note.issued_by_name or ""), "\n\n\n", "\n\n\n" + (note.received_by_name or "")],
        ],
        colWidths=[60 * mm, 60 * mm, 62 * mm],
    )
    sign.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("BOX", (0, 0), (0, -1), 0.4, colors.grey),
                ("BOX", (1, 0), (1, -1), 0.4, colors.grey),
                ("BOX", (2, 0), (2, -1), 0.4, colors.grey),
            ]
        )
    )
    story += [sign, Spacer(1, 3 * mm), Paragraph(f"<font size=7 color='#666666'>Scan the QR code to open {url}</font>", ss["BodyText"])]
    doc.build(story)
    return buf.getvalue()


__all__ = ["issue_note_pdf", "renderPDF"]
