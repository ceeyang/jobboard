"""Deep links into Telegram."""


def post_url(chat_id: int, username: str | None, msg_id: int) -> str | None:
    """Link to one post. Public chats use t.me/<username>/<id>; private channels and
    supergroups (ids like -100xxxxxxxxxx) use t.me/c/<id>/<msg>. Basic groups can't be linked."""
    if username:
        return f"https://t.me/{username}/{msg_id}"
    if chat_id <= -1_000_000_000_000:
        return f"https://t.me/c/{-chat_id - 1_000_000_000_000}/{msg_id}"
    return None


def channel_url(chat_id: int, username: str | None) -> str | None:
    if username:
        return f"https://t.me/{username}"
    if chat_id <= -1_000_000_000_000:
        return f"https://t.me/c/{-chat_id - 1_000_000_000_000}"
    return None
