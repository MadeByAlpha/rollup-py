import lib
import markupsafe
import requests
import socks  # from requests[socks]


def describe() -> dict[str, str]:
    return {
        "lib": lib.shout("hello"),
        "markupsafe": str(markupsafe.escape("<b>")),
        "requests": requests.__version__,
        "socks": socks.__name__,
    }


def main() -> None:
    print(describe())
