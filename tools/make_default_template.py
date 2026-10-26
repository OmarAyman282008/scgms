"""Build the default auto-fill MOM template from the school's original template.

Keeps the original header (logo + title line), page size, margins and border, and
rebuilds the body with {{placeholders}} that apps/documents/momtemplate.py fills.

    python tools/make_default_template.py docs/source/MOM_template_original.docx \
        apps/documents/default_templates/mom_template.docx
"""
import os
import re
import shutil
import sys
import tempfile
import zipfile
from xml.sax.saxutils import escape

SRC, OUT = sys.argv[1], sys.argv[2]
F = '<w:rFonts w:asciiTheme="minorHAnsi" w:hAnsiTheme="minorHAnsi" w:cstheme="minorHAnsi"/>'


def run(text, bold=False, size=24):
    b = "<w:b/><w:bCs/>" if bold else ""
    return (f'<w:r><w:rPr>{F}{b}<w:sz w:val="{size}"/><w:szCs w:val="{size}"/><w:rtl/></w:rPr>'
            f'<w:t xml:space="preserve">{escape(text)}</w:t></w:r>')


def para(parts, before=0, after=60, keep=False):
    if isinstance(parts, str):
        parts = [(parts, False)]
    kn = "<w:keepNext/>" if keep else ""
    return (f'<w:p><w:pPr>{kn}<w:bidi/><w:spacing w:before="{before}" w:after="{after}"/>'
            f'<w:rPr>{F}<w:rtl/></w:rPr></w:pPr>{"".join(run(t, b) for t, b in parts)}</w:p>')


def heading(text):
    return para([(text, True)], before=200, after=80, keep=True)


body = [
    para([("محضر اجتماع رقم ({{meeting_number}}) لعام {{year}}", True)], after=120),
    para([("التاريخ: ", True), ("{{day}} {{date}}", False), ("        الوقت: ", True), ("{{time}}", False),
          ("        المكان: ", True), ("{{venue}}", False)]),
    para([("نوع الاجتماع: ", True), ("{{meeting_type}}", False)]),
    heading("بحضور كلٍ من:"),
    para("{{attendees_table}}"),
    para([("المعتذرون / الغائبون: ", True), ("{{absentees}}", False)], before=120),
    heading("أجندة الاجتماع:"),
    para("{{agenda}}"),
    heading("متابعة قرارات الاجتماعات السابقة:"),
    para("{{previous_actions_table}}"),
    heading("ملخص المناقشات:"),
    para("{{discussion}}"),
    heading("القرارات والتوصيات (ما تم الاتفاق عليه):"),
    para("{{decisions_table}}"),
    para([("رئيس اللجنة: ", True), ("{{chair}}", False), ("            التوقيع: ....................", True)],
         before=320),
    para([("مقرر اللجنة: ", True), ("{{secretary}}", False), ("            التوقيع: ....................", True)],
         before=160),
    para([("تحريرًا في: ", True), ("{{issue_date}}", False)], before=160),
]

tmp = tempfile.mkdtemp()
with zipfile.ZipFile(SRC) as z:
    z.extractall(tmp)
doc_p = os.path.join(tmp, "word", "document.xml")
s = open(doc_p, encoding="utf8").read()
sect = re.search(r"<w:sectPr [^>]*>.*?</w:sectPr>", s, re.S).group(0)  # first one carries the header reference
start = s.index("<w:body>") + len("<w:body>")
end = s.index("</w:body>")
s = s[:start] + "".join(body) + sect + s[end:]
open(doc_p, "w", encoding="utf8").write(s)

hdr_p = os.path.join(tmp, "word", "header1.xml")
h = open(hdr_p, encoding="utf8").read()
h = re.sub(r"<w:t( xml:space=\"preserve\")?>\.{5,}</w:t>", '<w:t xml:space="preserve">{{committee_name}} - </w:t>', h, count=1)
open(hdr_p, "w", encoding="utf8").write(h)

os.makedirs(os.path.dirname(OUT), exist_ok=True)
if os.path.exists(OUT):
    os.remove(OUT)
with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
    for root, _d, files in os.walk(tmp):
        for f in files:
            full = os.path.join(root, f)
            if os.path.islink(full):
                continue
            z.write(full, os.path.relpath(full, tmp))
shutil.rmtree(tmp)
print("written", OUT)
