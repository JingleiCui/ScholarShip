"""Dynamic quote enums: generation and validation use the same evidence menu."""


def citation_schema(evidence):
    variants = [
        {
            "type": "object",
            "properties": {
                "evidence_id": {"type": "string", "const": e.id},
                "quote_id": {"type": "string", "enum": [f"q{i}" for i in range((len(e.text) + 179) // 180)]},
            },
            "required": ["evidence_id", "quote_id"],
            "additionalProperties": False,
        }
        for e in evidence
    ]
    return {
        "type": "object",
        "properties": {
            "answer": {"type": "string"},
            "insufficient_evidence": {"type": "boolean"},
            "claims": {
                "type": "array",
                "maxItems": 16,
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "citations": {"type": "array", "minItems": 1, "maxItems": 8, "items": {"anyOf": variants}},
                    },
                    "required": ["text", "citations"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["answer", "insufficient_evidence", "claims"],
        "additionalProperties": False,
    }
