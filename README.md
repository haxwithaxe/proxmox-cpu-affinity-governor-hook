# Description
Set the CPU governor state to ``performance`` for the cores used by the target VMs on startup and restore state to ``shedutil`` on shutdown.

# Setup
1. Create a config file at ``/etc/proxmox-hook-cpu-affinity-hook.toml`` with `username` and `password` values.
1. Add ``hookscripts`` PVE user (or whatever user you want to use).
1. Give the following permissions to the hook script user.
    - ?Sys.Modify? - I may have added that for something else. Give it a try without it.
    - VM.Audit
    - VM.Config.CPU
    - VM.Config.Options
1. Add the `cpu-affinity-hook.py` to a snippets directory.
1. Add the snippet to a VM config.
1. Start the VM.
1. Check the state of the CPU governor for the threads/cores the VM is set to use. For example a VM using CPU number 0 with default configs can be checked with the following command and have the given output.
    ```sh
    cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor
    >>> performance
    ```
1. Shut down the VM and verify the CPU governor has been set to the desired state.

# Config
The config is a TOML file with the following keys.
- `user` (str) - The proxmox user with the required permissions.
- `password` (str) - The password of the given user.
- `started_state` (str, optional) - The desired CPU governor state when the VM is running. Defaults to ``performance``.
- `stopped_state` (str, optional) - The desired CPU governor state to restore when the VM shuts down. Defaults to ``schedutil``. The possible values depend on what Intel implemented for your CPU. See the CPU governor documentation for the Linux kernel for details.
- `hostname` (str, optional) - The hostname of the Proxmox cluster node to connect to. Defaults to ``localhost``.
- `verify_tls` (bool, optional) - If `true` verify the TLS cert against the CA. Otherwise accept the TLS cert as valid. Defaults to `false` (since the default `hostname` is ``localhost``). If the hostname is not ``localhost`` this should be set to `true`.

## Simple Example
```toml
user = "hookscripts@pve"
password = "<password for hookscript@pve>" 
```

## Full Example
```toml
user = "hookscripts@pve"
password = "<password for hookscript@pve>"
started_state = "schedutil"
stopped_state = "powersave"
hostname = "pve3.example.com"
verify_tls = true
```
