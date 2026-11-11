"""annot CLI — door (a). A thin reader: it parses argv FROM core.SPECS and prints core.dispatch() bytes.

    python3 -m annot --tools                                  the surface, as JSON (names + inputSchema)
    python3 -m annot gene_dossier --gene Trp53                one tool, named parameters
    python3 -m annot call gene_dossier --json '{"gene":"Trp53"}'   the canonical form the MCP uses
    python3 -m annot page --gene Trp53                        door (d): the GN page, as HTML

Exit codes: 0 = a response was produced (status ok, empty, or refused — a refusal names its
findings, it is not a failure), 2 = usage error.
Stdout bytes are exactly core.dispatch()'s bytes — the CLI adds nothing, not even a newline.
"""
from __future__ import annotations

import json
import sys

from . import core


def usage(code: int = 2) -> int:
    sys.stderr.write(
        "annot — the ship's one core, on the command line\n\n"
        "  python3 -m annot --tools\n"
        "  python3 -m annot <tool> [--param VALUE ...]\n"
        "  python3 -m annot call <tool> --json '{...}'\n"
        "  python3 -m annot page [--tool TOOL] [--param VALUE ...]\n\n"
        "tools: " + ", ".join(core.TOOLS) + "\n"
        "global: --store DIR (default $ANNOT_STORE or symbiont/store)\n")
    return code


def parse(argv: list[str]) -> tuple[str, dict, str | None]:
    """argv -> (command, params, store). Params are validated by core.normalize, not here."""
    store_path = None
    out: list[str] = []
    i = 0
    while i < len(argv):
        if argv[i] == "--store":
            store_path = argv[i + 1]
            i += 2
            continue
        out.append(argv[i])
        i += 1
    if not out:
        raise SystemExit(usage())
    cmd, rest = out[0], out[1:]
    params: dict = {}
    if cmd == "call":
        if not rest:
            raise SystemExit(usage())
        tool = rest[0]
        rest = rest[1:]
        if rest[:1] == ["--json"]:
            params = json.loads(rest[1])
        return "call:" + tool, params, store_path
    j = 0
    while j < len(rest):
        tok = rest[j]
        if not tok.startswith("--"):
            raise SystemExit(usage())
        params[tok[2:]] = rest[j + 1]
        j += 2
    return cmd, params, store_path


def main(argv: list[str]) -> int:
    if argv and argv[0] in ("-h", "--help"):
        return usage(0)
    if argv and argv[0] == "--tools":
        body = [{"name": t, "purpose": core.SPECS[t]["purpose"], "inputSchema": core.input_schema(t)}
                for t in core.TOOLS]
        sys.stdout.buffer.write(core.dumps({
            "annot": {"core": core.CORE, "core_version": core.CORE_VERSION},
            "tools": body, "count": len(body),
            "provenance": core.provenance(core.store()),
        }))
        return 0

    cmd, params, store_path = parse(argv)

    if cmd == "page":
        from .page import render_page
        tool = params.pop("tool", "gene_dossier")
        sys.stdout.buffer.write(render_page(tool, params, store_path))
        return 0

    tool = cmd[5:] if cmd.startswith("call:") else cmd
    if tool not in core.SPECS:
        sys.stderr.write("annot: unknown tool %r (the surface is %s)\n" % (tool, ", ".join(core.TOOLS)))
        return usage()

    payload = core.dispatch(tool, params, store_path)
    sys.stdout.buffer.write(payload)
    try:
        status = json.loads(payload.decode("utf-8")).get("status")
    except ValueError:
        status = "error"
    return 0 if status in ("ok", "empty", "refused") else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
