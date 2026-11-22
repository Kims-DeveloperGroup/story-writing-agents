"""Render equivalent JSON schemas for Antigravity native output."""
from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from .agent_context.accounting import canonical_provider_json


def render_native_output_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Copy an equivalent native schema without changing instance data.

    Prepare this schema before ProviderInvocation when full schema-byte
    accounting is required. Rendering is idempotent and never re-encodes
    the invocation's captured payload.
    """

    if not isinstance(schema, Mapping):
        raise TypeError("Antigravity output schema must be a mapping")
    # Copy JSON values, including immutable mappings, in their wire order.
    rendered = json.loads(canonical_provider_json(schema))
    schema_maps = {
        "properties",
        "patternProperties",
        "$defs",
        "definitions",
        "dependentSchemas",
        "dependencies",
    }
    schema_arrays = {"allOf", "anyOf", "oneOf", "prefixItems"}
    schema_values = {
        "additionalProperties",
        "additionalItems",
        "unevaluatedProperties",
        "unevaluatedItems",
        "items",
        "contains",
        "propertyNames",
        "not",
        "if",
        "then",
        "else",
        "contentSchema",
    }

    def visit(node: object) -> None:
        if type(node) is not dict:
            return
        enumeration = node.get("enum")
        if type(enumeration) is list and len(enumeration) == 1:
            value = enumeration[0]
            if node.get("type") == "null" and value is None:
                del node["enum"]
            elif (
                node.get("type") == "integer"
                and type(value) is int
                and abs(value) <= 2**53 - 1
                and "minimum" not in node
                and "maximum" not in node
            ):
                entries = tuple(node.items())
                node.clear()
                for key, item in entries:
                    if key == "enum":
                        node["minimum"] = value
                        node["maximum"] = value
                    else:
                        node[key] = item

        for keyword, child in node.items():
            if keyword in schema_maps and type(child) is dict:
                for nested in child.values():
                    visit(nested)
            elif (
                keyword in schema_arrays or keyword == "items"
            ) and type(child) is list:
                for nested in child:
                    visit(nested)
            elif keyword in schema_values:
                visit(child)

    visit(rendered)
    return rendered
