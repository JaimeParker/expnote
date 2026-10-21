"""Strict YAML frontmatter: every scalar stays a string, duplicate keys are errors."""

from __future__ import annotations

from functools import lru_cache

from expnote.research.errors import MissingDependency


class FrontmatterError(Exception):
    """Unparsable frontmatter; ``kind`` becomes the finding code suffix."""

    def __init__(self, message: str, kind: str = "parse"):
        super().__init__(message)
        self.kind = kind


def import_yaml():
    try:
        import yaml
    except ImportError:
        raise MissingDependency("pyyaml") from None
    if yaml is None:  # sys.modules['yaml'] = None also means "not installed"
        raise MissingDependency("pyyaml")
    return yaml


@lru_cache(maxsize=1)
def _strict_loader_class():
    yaml = import_yaml()

    class StrictLoader(yaml.BaseLoader):
        """BaseLoader keeps scalars as str; mappings reject duplicate keys."""

    def construct_mapping(loader, node, deep=False):
        seen = set()
        for key_node, _ in node.value:
            key = loader.construct_object(key_node, deep=True)
            line = key_node.start_mark.line + 1
            if not isinstance(key, str):
                raise FrontmatterError(f"non-string key (line {line})")
            if key in seen:
                raise FrontmatterError(
                    f"duplicate key {key!r} (line {line})", "duplicate_key"
                )
            seen.add(key)
        return yaml.constructor.BaseConstructor.construct_mapping(
            loader, node, deep=True
        )

    StrictLoader.add_constructor(
        yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, construct_mapping
    )
    return StrictLoader


def split_frontmatter(text: str) -> tuple[str | None, str]:
    lines = text.split("\n")
    if not lines or lines[0].rstrip() != "---":
        return None, text
    for index in range(1, len(lines)):
        if lines[index].rstrip() in ("---", "..."):
            return "\n".join(lines[1:index]), "\n".join(lines[index + 1 :])
    raise FrontmatterError("frontmatter is not closed (missing closing ---)")


def load_frontmatter(frontmatter_text: str) -> tuple[dict, dict]:
    """(data, key -> yaml node); nodes let callers inspect quoting style."""
    yaml = import_yaml()
    loader = _strict_loader_class()(frontmatter_text)
    try:
        node = loader.get_single_node()
        if node is None:
            return {}, {}
        if not isinstance(node, yaml.MappingNode):
            raise FrontmatterError("frontmatter is not a key/value mapping")
        data = loader.construct_document(node)
    except yaml.YAMLError as error:
        message = " ".join(str(error).split())
        raise FrontmatterError(f"YAML parse failure: {message[:200]}") from None
    finally:
        loader.dispose()
    nodes = {k.value: v for k, v in node.value if isinstance(k, yaml.ScalarNode)}
    return data, nodes


def scalar_is_quoted(node) -> bool:
    yaml = import_yaml()
    return isinstance(node, yaml.ScalarNode) and node.style in ('"', "'")


def has_nested_map(data: dict) -> bool:
    def nested(value) -> bool:
        if isinstance(value, dict):
            return True
        return isinstance(value, list) and any(nested(item) for item in value)

    return any(nested(value) for value in data.values())
