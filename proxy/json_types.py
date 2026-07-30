"""Type aliases for the JSON request bodies the proxy handles."""

type JSONValue = str | int | float | bool | None | list["JSONValue"] | dict[str, "JSONValue"]
type JSONDict = dict[str, JSONValue]
