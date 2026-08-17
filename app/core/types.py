"""Shared type aliases used across infrastructure integrations."""

from typing import TypeAlias

from redis.typing import EncodableT


# Redis commands accept these values when writing hashes or Stream entries.
RedisFields: TypeAlias = dict[EncodableT, EncodableT]

# The application creates Redis clients with decode_responses=True, so values
# returned from Stream reads are ordinary strings.
RedisDecodedFields: TypeAlias = dict[str, str]
