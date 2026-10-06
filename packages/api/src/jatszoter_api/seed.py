import base64
import functools
import uuid
from dataclasses import dataclass
from importlib import resources
from typing import Any

import yaml

GUEST_AGENT = "qemu-guest-agent"
BASE = "/var/lib/jatszoter"
STEPS = ("install", "configure", "break")
DRIVER = "bootstrap"
AUTOLOGIN = "autologin.conf"


@functools.cache
def _guest(name: str) -> str:
    """A file of `guest/`, carried into the VM by the seed."""
    return resources.files("jatszoter_api").joinpath("guest", name).read_text()


@dataclass(frozen=True)
class Seed:
    """The two NoCloud files of one deployment, as text."""

    user_data: str
    meta_data: str


def _file(path: str, content: bytes, permissions: str = "0700") -> dict[str, str]:
    return {
        "path": path,
        "encoding": "b64",
        "content": base64.b64encode(content).decode(),
        "owner": "root:root",
        "permissions": permissions,
    }


def render(
    document: dict[str, Any],
    files: dict[str, bytes],
    vm_id: uuid.UUID,
    trainee_user: str,
    trainee_sudo: bool,
) -> Seed:
    """The seed of a checked scenario `document` whose bundle holds `files`."""
    trainee = document.get("trainee", {})
    user = trainee.get("user", trainee_user)
    account: dict[str, Any] = {"name": user, "lock_passwd": True}
    if trainee.get("sudo", trainee_sudo):
        account["sudo"] = "ALL=(ALL) NOPASSWD:ALL"
    packages = list(document.get("packages", []))
    if GUEST_AGENT not in packages:
        packages.append(GUEST_AGENT)
    steps = document["steps"]
    write_files = [
        _file(f"{BASE}/steps/{step}.sh", files[steps[step]]) for step in STEPS if step in steps
    ]
    autologin = _guest(AUTOLOGIN).format(user=user).encode()
    write_files.append(_file(f"{BASE}/{AUTOLOGIN}", autologin, "0600"))
    write_files.append(_file(f"{BASE}/{DRIVER}", _guest(DRIVER).encode()))
    config = {
        "users": ["default", account],
        "packages": packages,
        "write_files": write_files,
        "runcmd": [[f"{BASE}/{DRIVER}"]],
    }
    meta = {"instance-id": str(vm_id), "local-hostname": document["id"]}
    return Seed(
        user_data="#cloud-config\n" + yaml.safe_dump(config, sort_keys=False),
        meta_data=yaml.safe_dump(meta, sort_keys=False),
    )
