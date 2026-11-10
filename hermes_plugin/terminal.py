"""Plain text for a surface that prints a command's answer as typed.

Hermes's classic terminal takes the Markdown out of a model's reply before showing it, but prints a
plugin command's answer as it comes, so `**NOT LOOKED UP**` and `# Matter status` would show with
their marks. /matter answers in Markdown everywhere else: Desktop, Telegram and the rest draw it.
"""
from __future__ import annotations

import os
import re

# The surfaces that print a command's answer as typed, by the `platform` Hermes hands a command.
# "cli" is `hermes chat --cli`. Other values are left alone until they are seen printing marks.
PLAIN_PLATFORMS = ("cli",)

_BOLD = re.compile(r"\*\*(?=\S)([^*\n]+?)(?<=\S)\*\*")
_CODE = re.compile(r"`([^`\n]+)`")
_HEAD = re.compile(r"^#{1,6} +(?=\S)")
_NOTE = re.compile(r"^(\s*(?:- )?(?:↳ )?)_(?=\S)(.*\S)_$")  # a whole-line note, or one that is a list item


def plain_text(text):
    """`text` with this plugin's Markdown marks taken out: bold, code ticks, a heading's hashes and
    an italic note that fills its line or its list item. Nothing else changes, so a file name with
    underscores, a cron line's stars and an indented script comment stay as they are. A table keeps
    its bars: the packet is a document, and there is no plain form of a table worth guessing at."""
    if not isinstance(text, str):
        return text
    out = []
    for line in text.split("\n"):
        line = _HEAD.sub("", line)
        note = _NOTE.match(line)
        if note:
            line = note.group(1) + note.group(2)
        out.append(_CODE.sub(r"\1", _BOLD.sub(r"\1", line)))
    return "\n".join(out)


def tilde(path: str) -> str:
    """`path` with the person's home folder written as ~, when the path is inside it. For showing
    a folder to a person; a path that goes into a command keeps its full form."""
    home = os.path.expanduser("~").rstrip(os.sep)
    if home and home != "~" and path.startswith(home + os.sep):
        return "~" + path[len(home):]
    return path
