"""The offline fixture adapter — the SAME two file formats, read from disk instead of the
network, so the selftest's fixtures exercise the real parse, the real refusals and the real
conflict rule with zero requests. The release stamp of a local fixture file is the file's own
mtime (the local analogue of a release file's Last-Modified), so a fixture store is
deterministic within a run pair."""
from __future__ import annotations

import datetime as dt
import hashlib
import os


def _payload(path: str) -> tuple[str, dict]:
    with open(path, "rb") as fh:
        body = fh.read()
    st = os.stat(path)
    facts = dict(url=path, bytes=len(body), sha256=hashlib.sha256(body).hexdigest(),
                 release=dt.datetime.fromtimestamp(st.st_mtime, dt.timezone.utc)
                        .strftime("%a, %d %b %Y %H:%M:%S GMT"),
                 requests=0, waits=[], head_status="local")
    return body.decode("utf-8", "replace"), facts


class LocalVocab:
    """term_authority over a local VOC-format file."""
    name = "local_vocab"
    role = "term_authority"

    def configure(self, cfg: dict) -> None:
        self.cfg = cfg
        self.path = cfg["paths"]["terms"]

    def fetch_payload(self) -> tuple[str, dict]:
        return _payload(self.path)


class LocalAssoc:
    """association_provider over a local GenePheno-format file (8 fields, or 9 with an
    evidence-code column)."""
    name = "local_assoc"
    role = "association_provider"

    def configure(self, cfg: dict) -> None:
        self.cfg = cfg
        self.path = cfg["paths"]["associations"]

    def fetch_payload(self) -> tuple[str, dict]:
        return _payload(self.path)
