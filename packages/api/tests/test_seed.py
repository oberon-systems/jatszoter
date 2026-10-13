import base64
import re
import stat
import subprocess
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from jatszoter_api import bundles, seed

LIMIT = 1024 * 1024
VM_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")
DRIVER = "/var/lib/jatszoter/bootstrap"
# Every absolute path the driver touches, moved under a test root to run it unprivileged.
ROOTED = (
    "/var/lib/jatszoter",
    "/var/lib/cloud/instance",
    "/etc/systemd/system",
    "/etc/sysconfig/qemu-ga",
    "/run/cloud-init",
    "/dev/ttyS0",
)
Pack = Callable[..., bytes]


def document(**fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "alpha",
        "steps": {"install": "i.sh", "configure": "c.sh", "break": "b.sh"},
    }
    return base | fields


FILES = {"i.sh": b"install\n", "c.sh": b"configure\n", "b.sh": b"break\n"}


def config(rendered: seed.Seed) -> dict[str, Any]:
    assert rendered.user_data.startswith("#cloud-config\n")
    loaded: dict[str, Any] = yaml.safe_load(rendered.user_data)
    return loaded


def written(rendered: seed.Seed) -> dict[str, bytes]:
    return {
        entry["path"]: base64.b64decode(entry["content"])
        for entry in config(rendered)["write_files"]
    }


def render(
    doc: dict[str, Any],
    files: dict[str, bytes] = FILES,
    trainee_user: str = "trainee",
    trainee_sudo: bool = False,
) -> seed.Seed:
    return seed.render(doc, files, VM_ID, trainee_user, trainee_sudo)


def test_meta_data_names_the_vm_and_the_scenario() -> None:
    meta = yaml.safe_load(render(document()).meta_data)

    assert meta == {"instance-id": str(VM_ID), "local-hostname": "alpha"}


@pytest.mark.parametrize(
    ("packages", "expected"),
    [
        (None, ["qemu-guest-agent"]),
        (["nginx"], ["nginx", "qemu-guest-agent"]),
        (["qemu-guest-agent", "nginx"], ["qemu-guest-agent", "nginx"]),
    ],
)
def test_packages_gain_the_guest_agent_once(
    packages: list[str] | None, expected: list[str]
) -> None:
    doc = document() if packages is None else document(packages=packages)

    assert config(render(doc))["packages"] == expected


def test_the_trainee_gets_passwordless_sudo_only_when_asked() -> None:
    with_sudo = config(render(document(trainee={"user": "beta", "sudo": True})))
    without = config(render(document(trainee={"user": "beta", "sudo": False})))

    assert with_sudo["users"] == [
        "default",
        {"name": "beta", "lock_passwd": True, "sudo": "ALL=(ALL) NOPASSWD:ALL"},
    ]
    assert without["users"] == ["default", {"name": "beta", "lock_passwd": True}]


@pytest.mark.parametrize(
    ("trainee", "user", "sudo"),
    [({}, "gamma", True), ({"user": "beta"}, "beta", True), ({"sudo": False}, "gamma", False)],
)
def test_the_trainee_falls_back_field_by_field(
    trainee: dict[str, Any], user: str, sudo: bool
) -> None:
    rendered = render(document(trainee=trainee), trainee_user="gamma", trainee_sudo=True)

    [_, account] = config(rendered)["users"]
    assert account["name"] == user
    assert ("sudo" in account) is sudo
    assert f"--autologin {user} " in written(rendered)["/var/lib/jatszoter/autologin.conf"].decode()


def test_each_step_decodes_back_to_the_bundle(pack: Pack) -> None:
    bundle = bundles.read(pack(), LIMIT)

    files = written(seed.render(bundle.document, bundle.files, VM_ID, "trainee", False))

    for step, name in bundle.document["steps"].items():
        assert files[f"/var/lib/jatszoter/steps/{step}.sh"] == bundle.files[name]


def test_files_are_root_only_and_only_the_driver_runs() -> None:
    rendered = config(render(document(steps={"break": "b.sh"})))

    paths = [entry["path"] for entry in rendered["write_files"]]
    assert paths == [
        "/var/lib/jatszoter/steps/break.sh",
        "/var/lib/jatszoter/autologin.conf",
        DRIVER,
    ]
    modes = [entry["permissions"] for entry in rendered["write_files"]]
    assert modes == ["0700", "0600", "0700"]
    for entry in rendered["write_files"]:
        assert entry["encoding"] == "b64"
        assert entry["owner"] == "root:root"
    assert rendered["runcmd"] == [[DRIVER]]


def test_scenario_text_never_appears_in_clear() -> None:
    hostile = b"#!/bin/sh\necho $(id) `uname` '; reboot\n"

    rendered = render(document(steps={"break": "b.sh"}), {"b.sh": hostile})

    assert b"reboot" not in rendered.user_data.encode()
    assert written(rendered)["/var/lib/jatszoter/steps/break.sh"] == hostile


class Guest:
    """A rendered seed laid out under `root` as cloud-init would, with its driver runnable there."""

    def __init__(self, root: Path, codes: dict[str, int]) -> None:
        self.root = root
        self.events = root / "events"
        steps = {step: f"{step}.sh" for step in codes}
        files = {
            f"{step}.sh": f'#!/bin/sh\necho {step} >>"{self.events}"\nexit {code}\n'.encode()
            for step, code in codes.items()
        }
        rendered = seed.render(document(steps=steps), files, VM_ID, "beta", False)
        for path, content in written(rendered).items():
            if path == DRIVER:
                content = self._rooted(content.decode()).encode()
            self.put(path, content, 0o700)
        self.put("/etc/sysconfig/qemu-ga", b'FILTER_RPC_ARGS="--allow-rpcs=guest-ping"\n')
        self.put("/dev/ttyS0", b"")
        for name in ("user-data.txt", "user-data.txt.i", "cloud-config.txt", "obj.pkl"):
            self.put(f"/var/lib/cloud/instance/{name}", b"seed")
        self.put("/var/lib/cloud/instance/scripts/runcmd", b"seed")
        self.put("/var/lib/cloud/instance/sem/config_runcmd", b"")
        self.put("/run/cloud-init/combined-cloud-config.json", b"seed")
        self._stubs()

    def path(self, path: str) -> Path:
        return self.root / path.lstrip("/")

    def put(self, path: str, content: bytes, mode: int = 0o600) -> None:
        target = self.path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        target.chmod(mode)

    def _rooted(self, text: str) -> str:
        for path in ROOTED:
            assert path in text, path
        pattern = "|".join(re.escape(path) for path in ROOTED)
        return re.sub(pattern, lambda match: f"{self.root}{match.group(0)}", text)

    def _stubs(self) -> None:
        marker = self.path("/var/lib/jatszoter/marker")
        for name in ("systemctl", "setsebool", "selinuxenabled", "semodule"):
            stub = self.root / "bin" / name
            stub.parent.mkdir(exist_ok=True)
            stub.write_text(
                f'#!/bin/sh\n[ -e "{marker}" ] && echo "marker before {name}" >>"{self.events}"\n'
                f'echo "{name} $*" | sed "s/ *$//" >>"{self.events}"\n'
            )
            stub.chmod(stub.stat().st_mode | stat.S_IXUSR)

    def run(self) -> list[str]:
        subprocess.run(  # noqa: S603
            ["/bin/sh", str(self.path(DRIVER))],
            env={"PATH": f"{self.root / 'bin'}:/usr/bin:/bin"},
            check=True,
            timeout=30,
        )
        return self.events.read_text().splitlines()


GUEST_AGENT = [
    "selinuxenabled",
    "setsebool -P virt_qemu_ga_read_nonsecurity_files 1",
    "semodule -i jatszoter_guest_agent.cil",
    "systemctl enable qemu-guest-agent",
    "systemctl restart qemu-guest-agent",
]


def test_the_driver_runs_the_steps_in_order_and_writes_the_marker_last(tmp_path: Path) -> None:
    guest = Guest(tmp_path, {"install": 0, "configure": 0, "break": 0})

    events = guest.run()

    assert events == GUEST_AGENT + [
        "install",
        "configure",
        "break",
        "systemctl daemon-reload",
        "systemctl restart serial-getty@ttyS0.service",
    ]
    assert guest.path("/var/lib/jatszoter/marker").read_text() == "ok\n"
    assert sorted(p.name for p in guest.path("/var/lib/jatszoter").iterdir()) == ["marker"]
    dropin = guest.path("/etc/systemd/system/serial-getty@ttyS0.service.d/autologin.conf")
    assert "--autologin beta " in dropin.read_text()
    assert guest.path("/dev/ttyS0").read_bytes() == b"\033[H\033[2J"
    assert guest.path("/etc/sysconfig/qemu-ga").read_text() == "FILTER_RPC_ARGS=\n"


def test_the_driver_removes_every_copy_of_the_seed_but_the_semaphores(tmp_path: Path) -> None:
    guest = Guest(tmp_path, {"break": 0})

    guest.run()

    instance = guest.path("/var/lib/cloud/instance")
    assert [p.name for p in instance.iterdir()] == ["sem"]
    assert (instance / "sem" / "config_runcmd").exists()
    assert not guest.path("/run/cloud-init/combined-cloud-config.json").exists()


def test_a_failed_step_stops_the_bootstrap_and_opens_no_console(tmp_path: Path) -> None:
    guest = Guest(tmp_path, {"install": 0, "configure": 3, "break": 0})

    events = guest.run()

    assert events == GUEST_AGENT + ["install", "configure"]
    assert guest.path("/var/lib/jatszoter/marker").read_text() == "failed configure 3\n"
    assert sorted(p.name for p in guest.path("/var/lib/jatszoter").iterdir()) == ["marker"]
    assert not guest.path("/etc/systemd/system").exists()
    assert [p.name for p in guest.path("/var/lib/cloud/instance").iterdir()] == ["sem"]


def test_a_missing_step_is_skipped(tmp_path: Path) -> None:
    guest = Guest(tmp_path, {"break": 0})

    assert guest.run()[len(GUEST_AGENT)] == "break"
