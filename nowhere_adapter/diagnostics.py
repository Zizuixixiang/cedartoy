"""Value-free internal diagnostics. Never format an exception or its instance."""
import json
from pathlib import Path
import re


_MANIFEST = json.loads(Path(__file__).with_name("schema.json").read_text())
_FIELDS = {key for item in _MANIFEST for key in item["inputSchema"]["properties"]}
_FIELDS.update({"action", "traveler_name", "cotraveler", "method", "path", "body",
                "content", "files", "journey.json", "runtime", "metadata", "pos"})
_VALIDATORS = {"type", "required", "additionalProperties", "enum", "const", "anyOf",
               "oneOf", "allOf", "not", "minimum", "maximum", "exclusiveMinimum",
               "exclusiveMaximum", "multipleOf", "minLength", "maxLength", "pattern",
               "format", "minItems", "maxItems", "uniqueItems", "minProperties",
               "maxProperties", "contains"}
_STAGES = {"initialize", "decode", "restore", "prepare", "validate", "action", "snapshot"}
PUBLIC_ERROR = "乌有乡动作或存档校验失败；私人存档未提交"


def _identifier(value):
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z_][A-Za-z_0-9.]{0,119}", value) else "unknown"


def value_type(value):
    return {dict: "object", list: "array", str: "string", bool: "boolean",
            int: "integer", float: "number", type(None): "null"}.get(type(value), "other")


def internal_error(exc, request, stage):
    metadata = {"module": _identifier(type(exc).__module__),
                "type": _identifier(type(exc).__name__),
                "stage": stage if stage in _STAGES else "unknown",
                "request_type": value_type(request)}
    if isinstance(request, dict):
        # Unknown keys can themselves be private text or credentials.
        metadata["request_keys"] = sorted(key for key in request if key in _FIELDS)
        metadata["request_types"] = {key: value_type(request[key]) for key in metadata["request_keys"]}
        metadata["unknown_key_count"] = sum(key not in _FIELDS for key in request)
    if any(cls.__module__ == "jsonschema.exceptions" and cls.__name__ == "ValidationError"
           for cls in type(exc).__mro__):
        validator = exc.validator if isinstance(exc.validator, str) and exc.validator in _VALIDATORS else "unknown"
        metadata["validation"] = {
            "validator": validator,
            "path": [part if type(part) is int or (isinstance(part, str) and part in _FIELDS)
                     else "<redacted>" for part in list(exc.absolute_path)[:20]],
            # jsonschema's message/repr embed instance values (even passwords).
            "message": validator + " constraint failed (values omitted)",
            "instance_type": value_type(exc.instance),
        }
    return metadata


def action_name(request):
    action = request.get("action") if isinstance(request, dict) else None
    allowed = {item["name"] for item in _MANIFEST} | {"_web", "validate_import"}
    return action if isinstance(action, str) and action in allowed else "unknown"
