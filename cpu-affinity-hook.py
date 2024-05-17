#!/usr/bin/env python3

# /// pyproject
# [run]
# requires-python = '>=3.11'
# dependencies = [
#   "proxmoxer",
#   "requests"
# ]
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

_GOVERNOR_PATH_FMT = '/sys/devices/system/cpu/cpu{cpu_num}/cpufreq/scaling_governor'
_CONFIG_PATH = pathlib.Path(
    f'/etc/proxmox-hook-{os.path.splitext(os.path.basename(__file__))[0]}.toml'
)


class GovState(enum.StrEnum):

    PERFORMANCE = enum.auto()
    POWERSAVE = enum.auto()
    USERSPACE = enum.auto()
    ONDEMAND  = enum.auto()
    CONSERVATIVE = enum.auto()
    SCHEDUTIL = enum.auto()


@dataclass
class Config:

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
        config = tomllib.load(path.open('rb'))
        return cls(**config)


class ProxmoxVMs:

    def __init__(self, config):
        self.config = config
        self.api = proxmoxer.ProxmoxAPI(
            self.config.hostname,
            user=self.config.user,
            password=self.config.password,
            verify_ssl=self.config.verify_tls,
        )

    def affinities(self, vm_id: int) -> Generator[int, None, None]:
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
        with contextlib.suppress(KeyError):
            return self[vm_id]
        return default

    def ids_by_node(self, node: str) -> Generator[tuple[str, int], None, None]:
        for vm in self.api.nodes(node).qemu.get():
            yield vm.get('vmid')

    def get_locations(self) -> Generator[tuple[str, int], None, None]:
        for node in self.api.nodes.get():
            if node.get('status') == 'offline':
                continue
            node_name = node.get('node')
            for vm_id in self.ids_by_node(node_name):
                yield (node_name, vm_id)

    def get_node_by_vm_id(self, vm_id: int) -> str:
        for node, listed_vm_id in self.get_locations():
            if listed_vm_id == vm_id:
                return node
        raise KeyError(vm_id)

    def is_stopped(self, vm_id: int) -> bool:
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
    gov_path = pathlib.Path(_GOVERNOR_PATH_FMT.format(cpu_num=cpu_num))
    return GovState(gov_path.read_text())


def set_cpu_governor_state(cpu_num: int, state: GovState):
    gov_path = pathlib.Path(_GOVERNOR_PATH_FMT.format(cpu_num=cpu_num))
    gov_path.write_text(state.lower())


def on_start(config: Config, proxmox_vms: ProxmoxVMs, vm_id: int):
    for cpu_num in proxmox_vms.affinities(vm_id):
        set_cpu_governor_state(cpu_num, config.started_state)

def on_stop(config: Config, proxmox_vms: ProxmoxVMs, vm_id: int):
    for cpu_num in proxmox_vms.affinities(vm_id):
        set_cpu_governor_state(cpu_num, config.stopped_state)

def main():
    if len(sys.argv) < 3:
        print(
            'Not enough arguments. The first argument must be the VM ID and the second must be the phase.',
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
