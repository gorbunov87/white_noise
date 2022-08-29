from __future__ import annotations

from typing import Any


def decode_if_byte_string(value: Any, force_text: bool = False) -> str:
    result: str
    if isinstance(value, bytes):
        result = value.decode()
    elif force_text and not isinstance(value, str):
        result = str(value)
    else:
        result = value
    return result


# Follow Django in treating URLs as UTF-8 encoded (which requires undoing the
# implicit ISO-8859-1 decoding applied in Python 3). Strictly speaking, URLs
# should only be ASCII anyway, but UTF-8 can be found in the wild.
def decode_path_info(path_info: str) -> str:
    return path_info.encode("iso-8859-1", "replace").decode("utf-8", "replace")


def ensure_leading_trailing_slash(path: str | None) -> str:
    path = (path or "").strip("/")
    return f"/{path}/" if path else "/"
