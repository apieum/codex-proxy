"""Type aliases pour les corps de requêtes JSON manipulés par le proxy."""

type JSONValue = str | int | float | bool | None | list["JSONValue"] | dict[str, "JSONValue"]
type JSONDict = dict[str, JSONValue]
