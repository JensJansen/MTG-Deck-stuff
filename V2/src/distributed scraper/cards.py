"""
In-memory card name <-> id resolver.

v2.cards carries no index on card_name (only the integer PK), so name-based
lookups must never hit SQL. This resolver loads the entire (id, card_name)
table once at startup into two dicts and serves all translation in-process:

    name -> id   the write path: scraped card names -> integer FKs
    id -> name   the lease path: card_format_status.card_id -> the name a
                 discovery node searches Moxfield on

The seeder guarantees one row per card_name, so name -> id is unambiguous.
Reloading (after seeding new cards) swaps both dicts atomically.
"""

import threading


class CardResolver:
    def __init__(self) -> None:
        self._name_to_id: dict[str, int] = {}
        self._id_to_name: dict[int, str] = {}
        self._loaded = False
        self._lock = threading.Lock()

    def load(self, conn) -> int:
        """(Re)load the map from v2.cards. Returns the number of cards loaded."""
        with conn.cursor() as cur:
            cur.execute("SELECT id, card_name FROM v2.cards")
            rows = cur.fetchall()

        name_to_id = {name: cid for cid, name in rows}
        id_to_name = {cid: name for cid, name in rows}

        with self._lock:
            self._name_to_id = name_to_id
            self._id_to_name = id_to_name
            self._loaded = True
        return len(rows)

    @property
    def loaded(self) -> bool:
        return self._loaded

    @property
    def count(self) -> int:
        return len(self._name_to_id)

    def id_for(self, card_name: str) -> int | None:
        return self._name_to_id.get(card_name)

    def name_for(self, card_id: int) -> str | None:
        return self._id_to_name.get(card_id)


# Process-wide singleton shared by all request threads.
resolver = CardResolver()
