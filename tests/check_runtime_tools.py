"""Reject retired OS commands and file privilege elevation in every saved layer."""
from pathlib import PurePosixPath

from check_caddy_runtime import require


REMOVED_PACKAGES = {'mount', 'ncurses-bin', 'util-linux', 'util-linux-extra'}
COMMAND_DIRS = {'bin', 'sbin', 'usr/bin', 'usr/sbin', 'usr/local/bin', 'usr/local/sbin'}
REMOVED_COMMANDS = {'mount', 'umount', 'swapon', 'swapoff', 'losetup', 'nsenter',
                    'infocmp', 'tic', 'toe', 'tput', 'tset', 'captoinfo', 'infotocap'}


def check_runtime_member(item):
    name = PurePosixPath(item.name)
    require(not name.is_absolute() and '..' not in name.parts, 'Unsafe runtime tools path')
    basename = name.name.removeprefix('.wh.')
    require(not (str(name.parent) in COMMAND_DIRS and basename in REMOVED_COMMANDS),
            'Retired OS command retained in saved layer: ' + str(name))
    if item.isfile() or item.islnk():
        require(not item.mode & 0o6000, 'setuid/setgid file retained in saved layer: ' + str(name))
        require(not any('security.capability' in key for key in item.pax_headers),
                'File capabilities retained in saved layer: ' + str(name))


def check_runtime_packages(packages):
    require(not REMOVED_PACKAGES & packages, 'Retired OS package retained in saved layer')
