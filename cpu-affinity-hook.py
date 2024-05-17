#!/usr/bin/env python3

"""Set CPU governor states for a VM on start up and shutdown."""

# /// pyproject
# [run]
# requires-python = '>=3.11'
# dependencies = [
#   "proxmoxer",
#   "requests"
# ]
# [tool.flake8]
# ignore = ["D105", "D107"]
# [tool.black]
# skip-string-normalization = true
# ///

import contextlib
import enum
import os
import pathlib
import sys
import tomllib
from dataclasses import dataclass
from typing import Any, Generator

import proxmoxer

_GOVERNOR_PATH_FMT = (
    '/sys/devices/system/cpu/cpu{cpu_num}/cpufreq/scaling_governor'  # nofmt
)
_CONFIG_PATH = pathlib.Path(
    f'/etc/proxmox-hook-{os.path.splitext(os.path.basename(__file__))[0]}.toml'
)


class GovState(enum.StrEnum):
    """CPU governor states.

    As specified in the linux kernel.
    """

    PERFORMANCE = enum.auto()
    POWERSAVE = enum.auto()
    USERSPACE = enum.auto()
    ONDEMAND = enum.auto()
    CONSERVATIVE = enum.auto()
    SCHEDUTIL = enum.auto()


@dataclass
class Config:
    """Configurations for this snippet.

    Arguments:
        user: The proxmox user with the required permissions.
        password: The password of the given user.
        started_state (optional): The `GovState` corresponding to the desired
            CPU governor state when the VM is running. Defaults to
            `GovState.PERFORMANCE`.
        stopped_state (optional): The `GovState` corresponding to the desired
            CPU governor state to restore when the VM shuts down. Defaults to
            `GovState.SCHEDUTIL`.
        hostname (optional): The hostname of the Proxmox cluster node to
            connect to. Defaults to `localhost`.
        verify_tls (optional): If `True` verify the TLS cert against the CA.
            Otherwise accept the TLS cert as valid. Defaults to `False` (since
            the default `hostname` is ``localhost``). If the hostname is not
            ``localhost`` this should be set to `True`.
    """

    user: str
    password: str
    started_state: GovState = GovState.PERFORMANCE
    stopped_state: GovState = GovState.SCHEDUTIL
    hostname: str = 'localhost'
    verify_tls: bool = False

    def __post_init__(self):
        self.started_state = GovState(self.started_state)
        if self.stopped_state is not None:
            self.stopped_state = GovState(self.stopped_state)

    @classmethod
    def load(cls, path: pathlib.Path):
        """Load the config from a given file."""
        config = tomllib.load(path.open('rb'))
        return cls(**config)


class ProxmoxVMs:
    """A group of Proxmox VMs."""

    def __init__(self, config: Config):
        self._config = config
        self.api = proxmoxer.ProxmoxAPI(
            self._config.hostname,
            user=self._config.user,
            password=self._config.password,
            verify_ssl=self._config.verify_tls,
        )

    def affinities(self, vm_id: int) -> Generator[int, None, None]:
        """Return a generator of the CPU affinities for a given VM."""
        vm_config = self[vm_id]
        if not vm_config.get('affinity'):
            return []
        ranges = vm_config.get('affinity').split(',')
        for cores in ranges:
            # It's really one core
            if cores.isnumeric():
                yield int(cores)
                continue
            # It's a range of cores
            start_core, end_core = cores.split('-', 1)
            for core in range(int(start_core), int(end_core)):
                yield int(core)

    def get(self, vm_id: int, default: Any = None) -> dict:
        """Return a VM state for a given VM."""
        with contextlib.suppress(KeyError):
            return self[vm_id]
        return default

    def ids_by_node(self, node: str) -> Generator[tuple[str, int], None, None]:
        """Return a generator of VM IDs on a given node."""
        for vm in self.api.nodes(node).qemu.get():
            yield vm.get('vmid')

    def get_locations(self) -> Generator[tuple[str, int], None, None]:
        """Return a generator of VM IDs and associated cluster node name."""
        for node in self.api.nodes.get():
            if node.get('status') == 'offline':
                continue
            node_name = node.get('node')
            for vm_id in self.ids_by_node(node_name):
                yield (node_name, vm_id)

    def get_node_by_vm_id(self, vm_id: int) -> str:
        """Return the name of the cluster node a VM is on."""
        for node, listed_vm_id in self.get_locations():
            if listed_vm_id == vm_id:
                return node
        raise KeyError(vm_id)

    def is_stopped(self, vm_id: int) -> bool:
        """Return `True` if the given VM is stopped.

        Otherwise return `False`.
        """
        try:
            node, _ = [x for x in self.get_locations() if x[1] == vm_id][0]
        except IndexError:
            # The id wasn't found so it's not running
            return True
        vm_status = self.api.nodes(node).qemu(vm_id).status.current.get()
        return vm_status.get('status') == 'stopped'

    def __getitem__(self, vm_id: int) -> dict:
        for node, listed_vm_id in self.get_locations():
            if listed_vm_id == vm_id:
                return self.api.nodes(node).qemu(vm_id).config.get()
        raise KeyError(vm_id)


def get_cpu_governor_state(cpu_num: int) -> GovState:
    """Return the CPU governor state for a given CPU number."""
    gov_path = pathlib.Path(_GOVERNOR_PATH_FMT.format(cpu_num=cpu_num))
    return GovState(gov_path.read_text())


def set_cpu_governor_state(cpu_num: int, state: GovState):
    """Set the CPU governor state for a given CPU number."""
    gov_path = pathlib.Path(_GOVERNOR_PATH_FMT.format(cpu_num=cpu_num))
    gov_path.write_text(state.lower())


def on_start(config: Config, proxmox_vms: ProxmoxVMs, vm_id: int):
    """Set the CPU governor state for the CPUs associated with the given VM.

    Runs on VM ``pre-start`` hook call to set the governors to the state
    configured with `Config.started_state`.
    """
    for cpu_num in proxmox_vms.affinities(vm_id):
        set_cpu_governor_state(cpu_num, config.started_state)


def on_stop(config: Config, proxmox_vms: ProxmoxVMs, vm_id: int):
    """Set the CPU governor state for the CPUs associated with the given VM.

    Runs on VM ``post-stop`` hook call to restore the state of the CPU
    governors to the state configured with `Config.stopped_state`.
    """
    for cpu_num in proxmox_vms.affinities(vm_id):
        set_cpu_governor_state(cpu_num, config.stopped_state)


def main():
    """Handle the input from the calling system.

    Two positional arguments are expected.
    1: The ID of the VM.
    2: The phase of the process.
    """
    if len(sys.argv) < 3:
        print(
            'Not enough arguments. The first argument must be the VM ID and '
            'the second must be the phase.',  # nofmt
            file=sys.stderr,
        )
        sys.exit(1)
    vm_id = int(sys.argv[1])
    phase = sys.argv[2]
    config = Config.load(_CONFIG_PATH)
    proxmox_vms = ProxmoxVMs(Config.load(_CONFIG_PATH))

    if phase == 'pre-start':
        on_start(config, proxmox_vms, vm_id)
    elif phase == 'post-stop':
        on_stop(config, proxmox_vms, vm_id)


if __name__ == '__main__':
    main()
