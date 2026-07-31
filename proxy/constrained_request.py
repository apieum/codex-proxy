"""
Rewriting a request so narrating an action stops being expressible.

Constrained decoding replaces native tool calling: the provider emits only
text matching the schema, so `tools` is dead weight and the signatures have to
travel inside `instructions` instead. Measured on a real capture, those
schemas already cost ~2,700 tokens per request, so moving them is close to
cost-neutral.
"""
import json

from proxy.json_types import JSONDict, JSONValue

# Shaped for Cerebras constrained decoding: every object forbids extras, and
# `arguments` travels as a JSON string because a free-form object cannot
# satisfy `additionalProperties: false`. Codex represents call arguments the
# same way, so nothing is lost in translation.
TURN_SCHEMA: JSONDict = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "kind": {"type": "string", "enum": ["message", "tool_call"]},
        "text": {"type": "string", "description": "Set when kind is message."},
        "tool": {"type": "string", "description": "Set when kind is tool_call."},
        "arguments": {
            "type": "string",
            "description": "Set when kind is tool_call: the arguments as JSON.",
        },
    },
    "required": ["kind"],
}

# Without `strict`, Cerebras treats the schema as guidance only and narrating
# stays expressible -- which is the very thing this exists to prevent.
CONSTRAINED_TURN_FORMAT: JSONDict = {
    "format": {
        "type": "json_schema",
        "name": "codex_turn",
        "strict": True,
        "schema": TURN_SCHEMA,
    }
}

TOOL_PROTOCOL = (
    "\n\n# Answering protocol\n"
    "Every answer is a single JSON object, and nothing else.\n"
    'To speak to the user: {"kind":"message","text":"..."}\n'
    'To run a tool: {"kind":"tool_call","tool":"<name>","arguments":"{...}"}\n'
    "The arguments field is a JSON object serialised as a string.\n"
    "Never describe a tool call in prose: describing it does not run it.\n"
    "\n# Available tools\n"
)


def constrain_output(body: JSONDict) -> JSONDict:
    tools = body.get("tools")
    if not isinstance(tools, list) or not tools:
        return body

    constrained = dict(body)
    del constrained["tools"]
    constrained["text"] = CONSTRAINED_TURN_FORMAT
    constrained["instructions"] = _instructions(body.get("instructions"), tools)
    return constrained


def _instructions(original: JSONValue, tools: list[JSONValue]) -> str:
    preamble = original if isinstance(original, str) else ""
    return preamble + TOOL_PROTOCOL + "\n".join(_described(t) for t in tools)


def _described(tool: JSONValue) -> str:
    if not isinstance(tool, dict):
        return ""
    name = tool.get("name")
    description = tool.get("description")
    parameters = tool.get("parameters")
    described = f"- {name if isinstance(name, str) else '?'}"
    if isinstance(description, str) and description:
        described += f": {description}"
    if parameters is not None:
        described += f"\n  arguments: {json.dumps(parameters)}"
    return described
