"""annot page — door (d), the GN page, rendered headlessly from THE SAME bytes.

    python3 -m annot page --gene Trp53            > dossier.html
    python3 -m annot page --tool traits_containing --term "body weight" --limit 5 > traits.html

`render_page()` calls core.dispatch() once and embeds those exact bytes in the page's
`<script id="annot-payload" type="application/json">` block. The page's own JavaScript — the same
file a browser gets from `GET /` — parses that block and renders it, so the page has exactly one
renderer and one data source. No second definition of a gene, a source or a count lives in HTML.

Nothing is written: the HTML goes to stdout.
"""
from __future__ import annotations

import os
import sys

from . import core

TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web", "gn_dossier.html")


def _escape_for_script(payload: bytes) -> str:
    """JSON that is safe inside <script>: the only sequence that can close the tag is `</`."""
    return payload.decode("utf-8").replace("</", "<\\/")


def render_page(tool: str, params: dict | None = None, store_path: str | None = None) -> bytes:
    with open(TEMPLATE, "r", encoding="utf-8") as fh:        # READ ONLY
        html = fh.read()
    payload = core.dispatch(tool, params or {}, store_path)
    block = ('<script id="annot-payload" type="application/json">%s</script>'
             % _escape_for_script(payload))
    if "<!--ANNOT_PAYLOAD-->" not in html:
        raise SystemExit("annot page: template has no <!--ANNOT_PAYLOAD--> marker: %s" % TEMPLATE)
    return html.replace("<!--ANNOT_PAYLOAD-->", block).encode("utf-8")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    tool, params, store_path, i = "gene_dossier", {}, None, 0
    while i < len(argv):
        tok = argv[i]
        if tok == "--tool":
            tool = argv[i + 1]
            i += 2
            continue
        if tok == "--store":
            store_path = argv[i + 1]
            i += 2
            continue
        params[tok[2:]] = argv[i + 1]
        i += 2
    sys.stdout.buffer.write(render_page(tool, params, store_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
