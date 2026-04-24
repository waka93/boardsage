import re


def normalize(name: str) -> str:
    return re.sub(r"[\s\W_]+", "", name).lower()
