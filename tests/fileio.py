"""Read a whole file and close it, so a test run with warnings on prints no unclosed-file ResourceWarning."""


def read_text(path, encoding="utf-8", errors=None):
    with open(path, encoding=encoding, errors=errors) as f:
        return f.read()


def read_bytes(path):
    with open(path, "rb") as f:
        return f.read()
