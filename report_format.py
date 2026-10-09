"""Conservative plain-text rendering of XML/SOAP SQL report narratives.

Presentation only: the original narrative remains untouched for audit and SHA256.
No XML stylesheets, external references, DTDs, or embedded markup are executed.
"""
import re
import xml.etree.ElementTree as ET

MAX_CHARS = 2 * 1024 * 1024
WRAPPERS = {"envelope", "body", "return", "labresult", "facilityinfo"}
SECTIONS = {"findings", "impression", "conclusion", "technique", "history",
            "clinicalhistory", "reasonforexam", "comparison", "recommendation",
            "narrative", "report", "reporttext", "resulttext"}

def _name(tag):
    local = str(tag).rsplit("}", 1)[-1].split(":")[-1]
    return re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", local).replace("_", " ").strip()

def _clean(value):
    # Keep internal line breaks in radiology narratives; remove XML indent noise.
    return "\n".join(part.strip() for part in re.split(r"\r\n?|\n", value or "")
                     if part.strip())


REPORT_HEADINGS = {
    "CLINICAL HISTORY": "CLINICAL HISTORY",
    "HISTORY": "CLINICAL HISTORY",
    "INDICATION": "INDICATION",
    "REASON FOR EXAM": "INDICATION",
    "COMPARISON": "COMPARISON",
    "TECHNIQUE": "TECHNIQUE",
    "FINDINGS": "FINDINGS",
    "CONCLUSION": "CONCLUSION",
    "IMPRESSION": "IMPRESSION",
    "RECOMMENDATION": "RECOMMENDATION",
}
_HEADING = re.compile(
    r"^(CLINICAL HISTORY|HISTORY|INDICATION|REASON FOR EXAM|COMPARISON|TECHNIQUE|FINDINGS|CONCLUSION|IMPRESSION|RECOMMENDATION)\s*:\s*(.*)$",
    re.I | re.S,
)
_DIVIDER = re.compile(r"^[_=\-]{5,}$")


def _radiology_layout(raw_lines):
    """Format notes as sections, joining original wrapped lines only."""
    title = ""
    sections = []
    field_lines = []
    active_header = None
    paragraph_lines = []

    def finish():
        nonlocal paragraph_lines
        if paragraph_lines:
            value = " ".join(paragraph_lines).strip()
            if active_header:
                sections.append((active_header, value))
            else:
                field_lines.append(value)
            paragraph_lines = []

    def section_start(header, remainder):
        nonlocal active_header
        finish()
        active_header = REPORT_HEADINGS[header.upper()]
        if remainder.strip():
            paragraph_lines.append(remainder.strip())

    for candidate in raw_lines:
        line = candidate.strip()
        if not line:
            finish()
            continue
        if _DIVIDER.fullmatch(line):
            finish()
            continue
        match = _HEADING.match(line)
        if match:
            section_start(match.group(1), match.group(2))
            continue
        if not title and re.search(r"\b(?:MRI|MR|CT|XRAY|X-RAY|ULTRASOUND|RADIOGRAPH)\b", line, re.I):
            title = line
            continue
        if title and line.casefold() == title.casefold():
            continue
        paragraph_lines.append(line)
    finish()
    if not sections:
        return ""
    result = [title] if title else []
    if field_lines:
        result.append("\n\n".join(field_lines))
    for heading, prose in sections:
        if result and result[-1].startswith(heading + "\n"):
            result[-1] += "\n\n" + prose
        else:
            result.append(heading + "\n" + prose)
    return "\n\n".join(result)

def format_report(original):
    """Return (formatted text, status). Never changes the original input."""
    if not isinstance(original, str):
        raise TypeError("Expected a text narrative")
    trimmed = original.lstrip("\ufeff \t\r\n")
    if not trimmed.startswith("<"):
        return original, "plain"
    if len(original) > MAX_CHARS:
        return original, "unparsed"
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", original, re.I):
        return original, "unparsed"
    try:
        root = ET.fromstring(trimmed)
    except ET.ParseError:
        return original, "unparsed"

    lines = []
    note_lines = []
    def walk(element, depth=0):
        if depth > 48 or len(lines) > 10000:
            raise ValueError("XML nesting/element limit reached")
        tag = str(element.tag).rsplit("}", 1)[-1].lower()
        label = _name(element.tag)
        children = list(element)
        # Preserve all text in document order; never discard mixed content.
        leading = _clean(element.text)
        if leading and tag == "notes":
            note_lines.extend(leading.splitlines())
        elif leading:
            lines.append((label.upper() + ":\n" if tag in SECTIONS else
                          (label + ": " if tag not in WRAPPERS else "")) + leading)
        for child in children:
            walk(child, depth + 1)
            tail = _clean(child.tail)
            if tail:
                lines.append(tail)
    try:
        walk(root)
    except ValueError:
        return original, "unparsed"
    # If the XML carries line-wrapped <notes> values, render those as a
    # conventional report instead of repeating an XML field label.
    radiology = _radiology_layout(note_lines) if note_lines else ""
    if radiology:
        return radiology, "xml"
    rendered = "\n\n".join(lines).strip()
    if not rendered:
        return original, "unparsed"
    return rendered, "xml"
