import six


def shout(text: str) -> str:
    return six.ensure_str(text).upper()
