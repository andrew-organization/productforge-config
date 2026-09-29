"""The local port block each ProductForge product owns, worked out from its slot.

Every product takes one slot, shared by its API and its web app, and the slot
fixes every port either of them listens on: the block starts at 6100 and is 20
ports wide, so slot 0 is 6100-6119, slot 1 is 6120-6139, and so on. The offsets
inside a block are the same for every product. `kits/common/common.mk` does the
same arithmetic for `make`; a test holds the two together.
"""

BASE_PORT = 6100
BLOCK_SIZE = 20

# The offsets a block hands out, by name. 7-10 and 12-19 are reserved for what a
# kit needs next, so a later kit never has to move a port an earlier one used.
OFFSETS: dict[str, int] = {
    "API": 0,
    "POSTGRES": 1,
    "REDIS": 2,
    "FLOWER": 3,
    "MAILPIT_SMTP": 4,
    "MAILPIT_UI": 5,
    "WEB": 6,
    "TEST_POSTGRES": 11,
}

SERVICES: dict[str, str] = {
    "API": "the API",
    "POSTGRES": "Postgres",
    "REDIS": "Redis",
    "FLOWER": "Flower",
    "MAILPIT_SMTP": "Mailpit (SMTP)",
    "MAILPIT_UI": "Mailpit (web UI)",
    "WEB": "the web dev server, and a served build",
    "TEST_POSTGRES": "Postgres for the integration tests",
}

# The ports Chromium refuses to connect to (net/base/port_util.cc) that lie above 6100, the
# lowest port a block starts at. A block that holds one of these would leave a browser unable to
# reach a service; reserved offsets count, so a later kit's port is never one either.
CHROMIUM_RESTRICTED_PORTS = frozenset({6566, 6665, 6666, 6667, 6668, 6669, 6679, 6697, 10080})

# The last slot whose whole block stays below 32768, where the operating systems start handing out
# ephemeral ports, so a slot's ports never collide with an outgoing connection's.
MAX_SLOT = (32768 - BASE_PORT - BLOCK_SIZE) // BLOCK_SIZE


class PortError(ValueError):
    """A slot that can't be given a block of ports."""


def block(slot: int) -> range:
    """Every port a slot owns, reserved offsets included. Refuses a slot whose block can't be used."""
    if isinstance(slot, bool) or not isinstance(slot, int) or slot < 0:
        raise PortError(f"slot must be a whole number from 0 to {MAX_SLOT}, not {slot!r}")
    if slot > MAX_SLOT:
        raise PortError(f"slot {slot} is too large: the highest slot is {MAX_SLOT}, so its ports stay below 32768")
    first = BASE_PORT + BLOCK_SIZE * slot
    ports = range(first, first + BLOCK_SIZE)
    restricted = sorted(CHROMIUM_RESTRICTED_PORTS.intersection(ports))
    if restricted:
        listed = ", ".join(str(p) for p in restricted)
        raise PortError(
            f"slot {slot} owns ports {first}-{first + BLOCK_SIZE - 1}, which include {listed}: "
            "Chromium refuses to connect to those, so pick another slot"
        )
    return ports


def ports_for(slot: int) -> dict[str, int]:
    """The port for each named service at `slot`, keyed by OFFSETS' names."""
    first = block(slot)[0]
    return {name: first + offset for name, offset in OFFSETS.items()}


def env_lines(slot: int) -> list[str]:
    """The slot's ports as `PF_PORT_<NAME>=<port>` lines, the names `common.mk` exports."""
    return [f"PF_PORT_{name}={port}" for name, port in ports_for(slot).items()]


def table(slot: int) -> list[str]:
    """The slot's ports as aligned, human-readable lines."""
    ports = ports_for(slot)
    return [f"{port}  {SERVICES[name]}" for name, port in ports.items()]
