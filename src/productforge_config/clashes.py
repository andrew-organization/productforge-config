"""Find what several ProductForge checkouts would take from one machine at the same time.

`productforge-config clashes <checkout> <checkout> [...]` reads each checkout's `productforge.env`,
works out the `PF_` values its kit's makefile works out, and, for an API, has Docker Compose resolve
the compose model the checkout would start (`docker compose config`, which starts nothing). It then
reports every host port, Compose project, built image and named volume that two or more checkouts
would both take, naming the checkouts, whether they belong to two products or to one product's own API
and web app. It writes nothing and starts nothing.

Without Docker it compares host ports only, from the ports each kit uses in the slot's block, and
says so. Images pulled from a registry, which projects share, are not compared.
"""

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from productforge_config import kits, ports

PORT = "host port"
PROJECT = "Compose project"
IMAGE = "image"
VOLUME = "volume"
KINDS = (PORT, PROJECT, IMAGE, VOLUME)

# The ports of the slot's block each kit publishes, by the names in ports.OFFSETS. The API's are
# what its compose files publish, the integration tests' Postgres included; without Docker they
# are all the check has to go on.
KIT_PORTS: dict[str, tuple[str, ...]] = {
    "django-api": ("API", "POSTGRES", "REDIS", "FLOWER", "MAILPIT_SMTP", "MAILPIT_UI", "TEST_POSTGRES"),
    "flutter-web": ("WEB",),
}

COMPOSE_FILE = ".productforge/compose.yml"
TEST_COMPOSE_FILE = ".productforge/compose.test.yml"
OVERLAY_FILE = "docker-compose.local.yml"
DOTENV_FILE = ".env"


class ClashError(ValueError):
    """A checkout the check can't read, which it refuses rather than guess at."""


@dataclass(frozen=True)
class Claim:
    """One value a checkout would take: a kind and the value itself."""

    kind: str
    value: str


@dataclass(frozen=True)
class Clash:
    """A value two or more checkouts would take."""

    kind: str
    value: str
    checkouts: tuple[str, ...]

    def line(self) -> str:
        return f"{self.kind} {self.value}: {', '.join(self.checkouts)}"

    def as_json(self) -> dict[str, object]:
        return {"kind": self.kind, "value": self.value, "checkouts": list(self.checkouts)}


@dataclass(frozen=True)
class Checkout:
    """A checkout as the check read it: its name as given, its declaration and what it would take."""

    label: str
    declaration: kits.Declaration
    claims: frozenset[Claim]

    @property
    def ports(self) -> list[int]:
        return sorted(int(claim.value) for claim in self.claims if claim.kind == PORT)


def docker_compose_available() -> bool:
    """Whether `docker compose` runs here, so the compose model can be resolved."""
    if not shutil.which("docker"):
        return False
    return subprocess.run(["docker", "compose", "version"], capture_output=True).returncode == 0


def _same_path(one: str, other: str) -> bool:
    """Whether two spellings name one directory, whatever the case, links or dots in them."""
    try:
        return os.path.samefile(one, other)
    except OSError:
        return False


def refuse_repeats(given: list[str]) -> None:
    """A ClashError naming a checkout given more than once, which would clash with itself on every value."""
    seen: list[str] = []
    for label in given:
        same = next((earlier for earlier in seen if _same_path(earlier, label)), None)
        if same is not None:
            spelled = f"as {same} and as {label}" if same != label else "as the same path"
            raise ClashError(f"{label}: given twice, {spelled}")
        seen.append(label)


def read_declaration(*, label: str, root: Path) -> kits.Declaration:
    """The checkout's declaration, or a ClashError naming the checkout and what is wrong."""
    if not root.is_dir():
        raise ClashError(f"{label}: is not a directory")
    try:
        declaration = kits.read_env(root)
    except (OSError, UnicodeDecodeError) as exc:
        raise ClashError(f"{label}: {kits.ENV_FILE} can't be read: {exc}") from exc
    except kits.EnvError as exc:
        raise ClashError(f"{label}: {exc}") from exc
    if declaration is None:
        raise ClashError(f"{label}: has no {kits.ENV_FILE}")
    if declaration.product_kit is None or declaration.slot is None:
        raise ClashError(f"{label}: takes no product kit ({' or '.join(kits.PRODUCT_KITS)}), so owns no ports")
    return declaration


def compose_environment(declaration: kits.Declaration) -> dict[str, str]:
    """The `PF_` values the kits' makefile exports to Compose, from the declaration."""
    assert declaration.slot is not None
    values = {
        "PF_NAME": declaration.name,
        "PF_SLOT": str(declaration.slot),
        "PF_DJANGO_PROJECT": declaration.django_project,
        "PF_POSTGRES_DB": declaration.postgres_db,
    }
    for line in ports.env_lines(declaration.slot):
        name, _, port = line.partition("=")
        values[name] = port
    return values


def resolve_compose(*, label: str, root: Path, declaration: kits.Declaration, test: bool) -> dict:
    """The compose model `make up` would start, or with `test` the integration tests' one, resolved and not started."""
    command = ["docker", "compose", "--env-file", kits.ENV_FILE]
    if (root / DOTENV_FILE).exists():
        command += ["--env-file", DOTENV_FILE]
    command += ["-f", TEST_COMPOSE_FILE if test else COMPOSE_FILE]
    if not test and (root / OVERLAY_FILE).exists():
        command += ["-f", OVERLAY_FILE]
    command += ["--project-directory", ".", "config", "--format", "json"]
    result = subprocess.run(
        command,
        cwd=root,
        env={**os.environ, **compose_environment(declaration)},
        capture_output=True,
        text=True,
    )
    if result.returncode:
        detail = result.stderr.strip() or "docker compose config failed"
        raise ClashError(f"{label}: its compose model does not resolve: {detail}")
    try:
        model = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ClashError(f"{label}: its compose model does not resolve: {exc}") from exc
    if not isinstance(model, dict) or "name" not in model:
        raise ClashError(f"{label}: its compose model does not resolve: it names no project")
    return model


def published_ports(model: dict) -> set[int]:
    """Every host port a service of the model publishes, a range given as `first-last` included."""
    found: set[int] = set()
    for service in model.get("services", {}).values():
        for port in service.get("ports") or []:
            first, _, last = str(port.get("published") or "").partition("-")
            if first.isdigit() and (last or first).isdigit():
                found.update(range(int(first), int(last or first) + 1))
    return found


def claims_from_slot(declaration: kits.Declaration) -> set[Claim]:
    """The ports of the slot's block the checkout's kit uses."""
    assert declaration.slot is not None and declaration.product_kit is not None
    block = ports.ports_for(declaration.slot)
    return {Claim(PORT, str(block[name])) for name in KIT_PORTS[declaration.product_kit]}


def claims_from_model(model: dict) -> set[Claim]:
    """What a resolved compose model would take: its published ports, project, built images and own volumes."""
    claims = {Claim(PORT, str(port)) for port in published_ports(model)}
    claims.add(Claim(PROJECT, str(model["name"])))
    for service in model.get("services", {}).values():
        if service.get("build") and service.get("image"):
            claims.add(Claim(IMAGE, service["image"]))
    for volume in model.get("volumes", {}).values():
        if volume and volume.get("name") and not volume.get("external"):
            claims.add(Claim(VOLUME, volume["name"]))
    return claims


def read_checkout(*, label: str, resolve: bool, root: Path) -> Checkout:
    """What the checkout would take, resolving its compose model when `resolve` and it is an API."""
    declaration = read_declaration(label=label, root=root)
    if resolve and declaration.product_kit == "django-api":
        claims = set()
        for test in (False, True):
            claims |= claims_from_model(resolve_compose(label=label, root=root, declaration=declaration, test=test))
    else:
        claims = claims_from_slot(declaration)
    return Checkout(label, declaration, frozenset(claims))


def find_clashes(checkouts: list[Checkout]) -> list[Clash]:
    """Every value two or more of the checkouts would take, by kind, then ports in numeric order, then by name."""
    takers: dict[Claim, list[str]] = {}
    for checkout in checkouts:
        for claim in checkout.claims:
            takers.setdefault(claim, []).append(checkout.label)
    shared = [Clash(c.kind, c.value, tuple(sorted(labels))) for c, labels in takers.items() if len(labels) > 1]
    return sorted(shared, key=lambda c: (KINDS.index(c.kind), int(c.value) if c.kind == PORT else 0, c.value))


def summary(*, checkouts: list[Checkout], resolved: bool) -> str:
    """The one line saying what was compared, each checkout's host ports included."""
    kinds = "host ports, Compose projects, images and volumes" if resolved else "host ports only"
    each = ", ".join(f"{c.label} ({' '.join(str(p) for p in c.ports)})" for c in checkouts)
    line = f"compared {kinds} across {len(checkouts)} checkouts: {each}"
    if not resolved:
        line += "; docker compose is not available, so the compose models were not resolved"
    return line
