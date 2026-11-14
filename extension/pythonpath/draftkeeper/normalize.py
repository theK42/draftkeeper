"""Make LibreOffice's Flat ODT output stable and diff-friendly.

Three goals:

1. One paragraph per line, so git diffs and deltas line up with prose.
2. Remove ``officeooo:rsid`` / ``officeooo:paragraph-rsid`` attributes, which
   LibreOffice rewrites on every editing session and which would otherwise
   make unchanged paragraphs look changed.
3. Never change the document's meaning. ODF collapses whitespace inside
   paragraphs (like HTML), so indentation is only ever added between
   *structural* elements, never inside ``text:p`` / ``text:h``.

``extract_text`` is used by tests (and later by search) to check that the
visible text survives normalization.
"""

import io
import re
import xml.etree.ElementTree as ET
from typing import Dict, List

NS_TEXT = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
NS_OFFICEOOO = "http://openoffice.org/2009/office"

PARAGRAPH_TAGS = {"{%s}p" % NS_TEXT, "{%s}h" % NS_TEXT}
STRIP_ATTRS = {
    "{%s}rsid" % NS_OFFICEOOO,
    "{%s}paragraph-rsid" % NS_OFFICEOOO,
}

INDENT = " "


def normalize(data: bytes) -> bytes:
    namespaces = _collect_namespaces(data)
    for prefix, uri in namespaces.items():
        try:
            ET.register_namespace(prefix, uri)
        except ValueError:
            pass  # prefixes like "ns0" are reserved by ElementTree

    root = ET.fromstring(data)
    for elem in root.iter():
        for attr in STRIP_ATTRS:
            if attr in elem.attrib:
                del elem.attrib[attr]
    _drop_volatile(root)
    _indent(root, 0)

    out = io.BytesIO()
    out.write(b'<?xml version="1.0" encoding="UTF-8"?>\n')
    ET.ElementTree(root).write(out, encoding="utf-8", xml_declaration=False)
    out.write(b"\n")
    return out.getvalue()


NS_META = "urn:oasis:names:tc:opendocument:xmlns:meta:1.0"
NS_DC = "http://purl.org/dc/elements/1.1/"
NS_CONFIG = "urn:oasis:names:tc:opendocument:xmlns:config:1.0"

# Elements that change on every save without the text changing. Snapshot
# times are recorded by the commit itself.
VOLATILE_TAGS = {
    "{%s}date" % NS_DC,
    "{%s}editing-cycles" % NS_META,
    "{%s}editing-duration" % NS_META,
}
# config:name values to drop from office:settings. view-settings holds
# cursor/scroll/zoom; Rsid is a per-session random number. Layout and
# compatibility settings are kept.
VOLATILE_CONFIG = {"ooo:view-settings", "Rsid"}


def _drop_volatile(root: ET.Element) -> None:
    config_name = "{%s}name" % NS_CONFIG
    for parent in root.iter():
        for child in list(parent):
            if child.tag in VOLATILE_TAGS or child.get(config_name) in VOLATILE_CONFIG:
                parent.remove(child)


def _collect_namespaces(data: bytes) -> Dict[str, str]:
    found = {}
    for _event, (prefix, uri) in ET.iterparse(io.BytesIO(data), events=("start-ns",)):
        found.setdefault(prefix, uri)
    return found


def _is_blank(s) -> bool:
    return s is None or not s.strip()


def _indent(elem: ET.Element, level: int) -> None:
    if elem.tag in PARAGRAPH_TAGS:
        return  # whitespace inside a paragraph is content
    children = list(elem)
    if not children:
        return
    # Mixed content (real text next to child elements): leave it alone.
    if not _is_blank(elem.text) or any(not _is_blank(c.tail) for c in children):
        return
    inner = "\n" + INDENT * (level + 1)
    elem.text = inner
    for child in children:
        child.tail = inner
        _indent(child, level + 1)
    children[-1].tail = "\n" + INDENT * level


# -- plain text -------------------------------------------------------------

_T = "{%s}" % NS_TEXT


def text_key(data: bytes) -> str:
    """Identity of a version as she'd see it: its words, ignoring formatting and metadata.

    Used to spot duplicates when importing old files. Byte comparison isn't
    enough: the same text saved by different LibreOffice sessions differs in
    style names, IDs and dates. The trade-off: two old files that differ only
    in formatting count as one version.
    """
    import hashlib
    return hashlib.sha1(extract_text(data).encode("utf-8")).hexdigest()


def extract_text(data: bytes) -> str:
    """Visible text of the document body, one paragraph per line."""
    root = ET.fromstring(data)
    lines: List[str] = []
    for elem in root.iter():
        if elem.tag in PARAGRAPH_TAGS:
            lines.append(_paragraph_text(elem))
    return "\n".join(lines)


def _paragraph_text(p: ET.Element) -> str:
    # ODF whitespace rules: runs of space/tab/newline in character data
    # collapse to a single space; text:s / text:tab / text:line-break are
    # the explicit forms.
    parts: List[str] = []

    def chars(s):
        if s:
            parts.append(re.sub(r"[ \t\r\n]+", " ", s))

    def walk(e):
        tag = e.tag
        if tag == _T + "s":
            parts.append("\x00" * int(e.get(_T + "c", "1")))
        elif tag == _T + "tab":
            parts.append("\t")
        elif tag == _T + "line-break":
            parts.append("\n")
        elif tag in (_T + "note", "{urn:oasis:names:tc:opendocument:xmlns:office:1.0}annotation"):
            pass  # footnotes/comments are not inline text
        else:
            chars(e.text)
            for c in e:
                walk(c)
        chars(e.tail)  # tail text follows the element in the parent's flow

    chars(p.text)
    for c in p:
        walk(c)
    text = "".join(parts)
    text = re.sub(r" {2,}", " ", text).strip(" ")
    return text.replace("\x00", " ")
