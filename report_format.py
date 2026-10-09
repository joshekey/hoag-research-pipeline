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
    def walk(element, depth=0):
        if depth > 48 or len(lines) > 10000:
            raise ValueError("XML nesting/element limit reached")
        tag = str(element.tag).rsplit("}", 1)[-1].lower()
        label = _name(element.tag)
        children = list(element)
        # Preserve all text in document order; never discard mixed content.
        leading = _clean(element.text)
        if leading:
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
    rendered = "\n\n".join(lines).strip()
    if not rendered:
        return original, "unparsed"
    return rendered, "xml"
