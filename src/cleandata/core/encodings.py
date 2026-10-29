"""One check for encoding names, shared by the profile, the command line and the writers."""

from __future__ import annotations

import codecs


def require_text_encoding(name: str) -> str:
    """Return ``name`` when it is a Python text encoding; raise ``ValueError`` with a usable message if not.

    ``codecs.lookup`` also finds bytes-to-bytes codecs such as ``rot13`` and ``zlib_codec``; those only fail
    when the first character is read or written. A round trip of one character settles it at once.
    """
    try:
        codecs.lookup(name)
    except (LookupError, ValueError):
        raise ValueError(f"unknown encoding {name!r}") from None
    try:
        "a".encode(name).decode(name)
    except (LookupError, ValueError):
        raise ValueError(f"{name!r} is not a Python text encoding; use one such as utf-8") from None
    return name
