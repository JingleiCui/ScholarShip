"""Identifiers extracted locally are candidates, not publisher verification."""

import re


def normalize_identifiers(text):
    result = []
    for value in re.findall(r'10\.\d{4,9}/[^\s<>"\]]+', text, re.I):
        pair = {"scheme": "doi", "normalized_value": value.rstrip(".,;:)").lower()}
        if pair not in result:
            result.append(pair)
    for value in re.findall(r"(?<!\d)(\d{4}\.\d{4,5})(?:v\d+)?", text, re.I):
        if 1 <= int(value[2:4]) <= 12:
            pair = {"scheme": "arxiv", "normalized_value": value}
            if pair not in result:
                result.append(pair)
    return result
