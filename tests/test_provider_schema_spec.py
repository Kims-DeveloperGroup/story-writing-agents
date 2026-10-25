"""Development-only Structured Outputs profile, not a native compiler certificate.

The documented subset is checked alongside a conservative repository regex grammar.
Lookaround rejection was observed from the provider; its complete dialect is not
documented. Unknown regex syntax therefore requires review rather than being
silently accepted. No production module imports this checker.
"""
from collections.abc import Mapping
import argparse
import hashlib
import json
import math
from pathlib import Path

SPEC_URL = "https://developers.openai.com/api/docs/guides/structured-outputs"
PROFILE = "structured-outputs-static-2026-10-26"
_TYPES = {"object", "array", "string", "number", "integer", "boolean", "null"}
_FORMATS = {"date-time", "time", "date", "duration", "email", "hostname", "ipv4", "ipv6", "uuid"}
_KEYWORDS = {"type", "properties", "required", "additionalProperties", "$defs", "$ref", "anyOf",
             "items", "enum", "const", "description", "title", "pattern", "format",
             "minLength", "maxLength", "minItems", "maxItems", "minimum", "maximum",
             "exclusiveMinimum", "exclusiveMaximum", "multipleOf"}
_INTEGERS = {"minLength", "maxLength", "minItems", "maxItems"}
_NUMBERS = {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf"}


class _Regex:
    """Parse the consuming grammar used by exported schemas; never match values."""

    def __init__(self, pattern):
        self.text, self.at, self.depth = pattern, 0, 0

    def peek(self):
        return self.text[self.at:self.at + 1]

    def fail(self, rule):
        raise ValueError(rule)

    def scalar(self, value):
        if value > 0x10FFFF or 0xD800 <= value <= 0xDFFF:
            self.fail("REGEX_SCALAR")
        return value

    def escape(self, in_class=False):
        self.at += 1
        value = self.peek()
        if not value:
            self.fail("REGEX_ESCAPE")
        self.at += 1
        if value in r"\.^$|?*+(){}[]-/":
            return ord(value)
        if value in "nrtfv":
            return ord({"n": "\n", "r": "\r", "t": "\t", "f": "\f", "v": "\v"}[value])
        if value in "sSdDwW":
            return None
        if value == "z" and not in_class:
            return -1
        if value in "xu":
            size = 2 if value == "x" else 4
            digits = self.text[self.at:self.at + size]
            if len(digits) != size or any(c not in "0123456789abcdefABCDEF" for c in digits):
                self.fail("REGEX_ESCAPE")
            self.at += size
            return self.scalar(int(digits, 16))
        self.fail("REGEX_UNREVIEWED_ESCAPE")

    def class_atom(self):
        if self.peek() == "\\":
            return self.escape(in_class=True)
        if self.peek() == "[" or self.text[self.at:self.at + 2] in ("&&", "--", "~~"):
            self.fail("REGEX_UNREVIEWED_CLASS")
        if self.peek() in ("", "]"):
            self.fail("REGEX_CLASS")
        value = self.scalar(ord(self.peek()))
        self.at += 1
        return value

    def character_class(self):
        self.at += 1
        if self.peek() == "^":
            self.at += 1
        count = 0
        while self.peek() != "]":
            left = self.class_atom()
            count += 1
            if self.text[self.at:self.at + 2] in ("&&", "--", "~~"):
                self.fail("REGEX_UNREVIEWED_CLASS")
            if self.peek() == "-" and self.text[self.at + 1:self.at + 2] != "]":
                self.at += 1
                right = self.class_atom()
                if left is None or right is None or left > right:
                    self.fail("REGEX_RANGE")
        if not count:
            self.fail("REGEX_CLASS")
        self.at += 1

    def atom(self):
        token = self.peek()
        if token == "(":
            self.at += 1
            if self.peek() == "?":
                if self.text[self.at:self.at + 2] != "?:":
                    lookaround = self.text[self.at:self.at + 2] in ("?=", "?!") or (
                        self.text[self.at:self.at + 3] in ("?<=", "?<!"))
                    self.fail("REGEX_LOOKAROUND" if lookaround else "REGEX_UNREVIEWED_GROUP")
                self.at += 2
            self.depth += 1
            if self.depth > 64:
                self.fail("REGEX_COMPLEXITY")
            self.expression()
            if self.peek() != ")":
                self.fail("REGEX_GROUP")
            self.at += 1
            self.depth -= 1
            return True
        if token == "[":
            self.character_class()
            return True
        if token == "\\":
            return self.escape() != -1
        if token in ("", "*", "+", "?", "{", "}", "]"):
            self.fail("REGEX_ATOM")
        self.scalar(ord(token))
        self.at += 1
        return token not in "^$"

    def decimal(self):
        start = self.at
        while self.peek() and "0" <= self.peek() <= "9":
            self.at += 1
        if self.at == start or self.at - start > 9:
            self.fail("REGEX_QUANTIFIER")
        return int(self.text[start:self.at])

    def expression(self):
        while True:
            while self.peek() and self.peek() not in ")|":
                quantifiable = self.atom()
                if self.peek() and self.peek() in "*+?{":
                    if not quantifiable:
                        self.fail("REGEX_QUANTIFIER")
                    if self.peek() == "{":
                        self.at += 1
                        low = self.decimal()
                        high = low
                        if self.peek() == ",":
                            self.at += 1
                            high = None if self.peek() == "}" else self.decimal()
                        if self.peek() != "}" or (high is not None and low > high):
                            self.fail("REGEX_QUANTIFIER")
                    self.at += 1
                    if self.peek() and self.peek() in "*+?{":
                        self.fail("REGEX_UNREVIEWED_QUANTIFIER")
            if self.peek() != "|":
                return
            self.at += 1

    def parse(self):
        self.expression()
        if self.at != len(self.text):
            self.fail("REGEX_GROUP")


def _path(path, key):
    return path + "." + key if isinstance(key, str) and (key.isidentifier() or key == "$defs") else (
        path + "[" + json.dumps(key, ensure_ascii=True) + "]")


def _reference(root, value):
    if value == "#":
        return root
    if not isinstance(value, str) or not value.startswith("#/"):
        raise ValueError("local reference required")
    current = root
    for part in value[2:].split("/"):
        at = 0
        while at < len(part):
            if part[at] == "~":
                if part[at:at + 2] not in ("~0", "~1"):
                    raise ValueError("invalid JSON pointer escape")
                at += 1
            at += 1
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, (list, tuple)):
            if not part.isascii() or not part.isdigit() or (len(part) > 1 and part[0] == "0"):
                raise ValueError("invalid array pointer")
            current = current[int(part)]
        else:
            current = current[part]
    return current


def _primitive(value, types):
    if value is None:
        return "null" in types
    if type(value) is bool:
        return "boolean" in types
    if type(value) is int:
        return "integer" in types or "number" in types
    if type(value) is float:
        return "number" in types and math.isfinite(value)
    return isinstance(value, str) and "string" in types and not any(0xD800 <= ord(c) <= 0xDFFF for c in value)


def check_schema(schema):
    """Return deterministic (path, rule) findings for this static repo profile.

    Reference targets are checked without expansion: recursive schemas are legal.
    The ten-level check covers inline containers, not reference-expanded depth.
    Success is neither full regex-dialect validation nor native admission.
    """
    findings, references, schema_nodes = [], [], set()
    properties = strings = enum_values = 0
    pending = [(schema, "$", 0, frozenset())]
    if not isinstance(schema, Mapping) or schema.get("type") != "object" or "anyOf" in schema:
        findings.append(("$", "ROOT_OBJECT"))
    while pending:
        node, path, depth, ancestors = pending.pop()
        if not isinstance(node, Mapping) or id(node) in ancestors:
            findings.append((path, "SCHEMA_OBJECT"))
            continue
        schema_nodes.add(id(node))
        ancestors = ancestors | {id(node)}
        for key in node:
            if key not in _KEYWORDS:
                findings.append((_path(path, key), "SCHEMA_UNREVIEWED_KEYWORD"))
        kind = node.get("type")
        types = kind if isinstance(kind, (list, tuple)) else [kind]
        if kind is not None and (not types or any(not isinstance(t, str) or t not in _TYPES for t in types)):
            findings.append((_path(path, "type"), "SCHEMA_TYPE"))
        elif kind is not None and len(set(types)) != len(types):
            findings.append((_path(path, "type"), "SCHEMA_TYPE"))
        if kind is None and "$ref" not in node and "anyOf" not in node:
            findings.append((path, "SCHEMA_TYPE"))
        if "object" in types or "array" in types:
            depth += 1
            if depth > 10:
                findings.append((path, "INLINE_NESTING_LIMIT"))
        for key in ("description", "title"):
            if key in node and not isinstance(node[key], str):
                findings.append((_path(path, key), "STRING_ANNOTATION"))
        if "object" in types or any(k in node for k in ("properties", "required", "additionalProperties")):
            if "object" not in types:
                findings.append((path, "OBJECT_KEYWORD_TYPE"))
            props, required = node.get("properties"), node.get("required")
            if not isinstance(props, Mapping):
                findings.append((_path(path, "properties"), "PROPERTIES_OBJECT"))
                props = {}
            if not isinstance(required, (list, tuple)) or any(not isinstance(k, str) for k in required) or (
                len(set(required)) != len(required) or set(required) != set(props)):
                findings.append((_path(path, "required"), "ALL_FIELDS_REQUIRED"))
            if node.get("additionalProperties") is not False:
                findings.append((_path(path, "additionalProperties"), "CLOSED_OBJECT"))
            properties += len(props)
        for key in _INTEGERS & node.keys():
            if type(node[key]) is not int or node[key] < 0:
                findings.append((_path(path, key), "NONNEGATIVE_INTEGER"))
        for low, high in (("minLength", "maxLength"), ("minItems", "maxItems")):
            if type(node.get(low)) is int and type(node.get(high)) is int and node[low] > node[high]:
                findings.append((path, "SCHEMA_RANGE"))
        for key in _NUMBERS & node.keys():
            value = node[key]
            if type(value) not in (int, float) or (type(value) is float and not math.isfinite(value)) or (key == "multipleOf" and value <= 0):
                findings.append((_path(path, key), "FINITE_NUMBER"))
        for low in ("minimum", "exclusiveMinimum"):
            for high in ("maximum", "exclusiveMaximum"):
                if type(node.get(low)) in (int, float) and type(node.get(high)) in (int, float) and (
                    node[low] > node[high] or node[low] == node[high] and (low != "minimum" or high != "maximum")):
                    findings.append((path, "SCHEMA_RANGE"))
        for keys, allowed, rule in (({"pattern", "format", "minLength", "maxLength"}, {"string"}, "STRING_KEYWORD_TYPE"),
                                    (_NUMBERS, {"integer", "number"}, "NUMBER_KEYWORD_TYPE"),
                                    ({"items", "minItems", "maxItems"}, {"array"}, "ARRAY_KEYWORD_TYPE")):
            if keys.intersection(node) and not any(t in types for t in allowed):
                findings.append((path, rule))
        if "format" in node and (not isinstance(node["format"], str) or node["format"] not in _FORMATS):
            findings.append((_path(path, "format"), "STRING_FORMAT"))
        if "pattern" in node:
            if not isinstance(node["pattern"], str):
                findings.append((_path(path, "pattern"), "REGEX_STRING"))
            else:
                try:
                    _Regex(node["pattern"]).parse()
                except ValueError as failure:
                    findings.append((_path(path, "pattern"), str(failure)))
        if "enum" in node:
            values = node["enum"]
            if not isinstance(values, (list, tuple)) or not values:
                findings.append((_path(path, "enum"), "NONEMPTY_ENUM"))
            else:
                enum_values += len(values)
                if any(not _primitive(value, types) for value in values):
                    findings.append((_path(path, "enum"), "PRIMITIVE_LITERAL_TYPE"))
                chars = sum(len(v) for v in values if isinstance(v, str))
                strings += chars
                if len(values) > 250 and all(isinstance(v, str) for v in values) and chars > 15_000:
                    findings.append((_path(path, "enum"), "LARGE_ENUM_STRING_LIMIT"))
        if "const" in node:
            if not _primitive(node["const"], types):
                findings.append((_path(path, "const"), "PRIMITIVE_LITERAL_TYPE"))
            if isinstance(node["const"], str):
                strings += len(node["const"])
        if "$ref" in node:
            references.append((_path(path, "$ref"), node["$ref"]))
        for key in ("properties", "$defs"):
            if key in node:
                values = node[key]
                if not isinstance(values, Mapping):
                    findings.append((_path(path, key), "SCHEMA_MAP"))
                    continue
                for name, value in reversed(tuple(values.items())):
                    if not isinstance(name, str):
                        findings.append((_path(path, key), "SCHEMA_NAME"))
                        continue
                    strings += len(name)
                    pending.append((value, _path(_path(path, key), name), 0 if key == "$defs" else depth, ancestors))
        if "anyOf" in node:
            values = node["anyOf"]
            if not isinstance(values, (list, tuple)) or not values:
                findings.append((_path(path, "anyOf"), "NONEMPTY_ANYOF"))
            else:
                pending.extend((value, f"{path}.anyOf[{i}]", depth, ancestors) for i, value in reversed(tuple(enumerate(values))))
        if "items" in node:
            pending.append((node["items"], _path(path, "items"), depth, ancestors))
        elif "array" in types:
            findings.append((_path(path, "items"), "ARRAY_ITEMS"))
    for path, value in references:
        try:
            target = _reference(schema, value)
            if not isinstance(target, Mapping) or id(target) not in schema_nodes:
                raise ValueError("reference must name a schema node")
        except (KeyError, TypeError, ValueError, IndexError):
            findings.append((path, "LOCAL_SCHEMA_REFERENCE"))
    for value, limit, rule in ((properties, 5000, "PROPERTY_LIMIT"), (strings, 120_000, "SCHEMA_STRING_LIMIT"),
                               (enum_values, 1000, "ENUM_LIMIT")):
        if value > limit:
            findings.append(("$", rule))
    return tuple(sorted(set(findings)))


def _unique_pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _invalid_constant(_value):
    raise ValueError("nonfinite JSON number")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("schema", type=Path)
    args = parser.parse_args(argv)
    report = {"profile": PROFILE, "specification": SPEC_URL, "native_admitted": False,
              "limits": "Audited regex grammar; inline nesting only; native compiler remains authoritative."}
    try:
        raw = args.schema.read_bytes()
        schema = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs, parse_constant=_invalid_constant)
        report.update(schema_sha256=hashlib.sha256(raw).hexdigest(), findings=check_schema(schema))
        code = int(bool(report["findings"]))
    except (OSError, UnicodeError, ValueError, TypeError, RecursionError) as failure:
        report["input_error"] = type(failure).__name__
        code = 2
    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
