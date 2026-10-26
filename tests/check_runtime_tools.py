"""Reject retired OS commands and file privilege elevation in every saved layer."""
from pathlib import PurePosixPath
import re

from check_caddy_runtime import require
from elf_dependencies import elf


REMOVED_PACKAGES = {'mount', 'ncurses-bin', 'util-linux', 'util-linux-extra',
                    'e2fsprogs', 'libmount1', 'libblkid1', 'acl', 'libacl1', 'libacl1-dev'}
COMMAND_DIRS = {'bin', 'sbin', 'usr/bin', 'usr/sbin', 'usr/local/bin', 'usr/local/sbin'}
REMOVED_COMMANDS = {'mount', 'umount', 'swapon', 'swapoff', 'losetup', 'nsenter',
                    'infocmp', 'tic', 'toe', 'tput', 'tset', 'captoinfo', 'infotocap',
                    'debugfs', 'dumpe2fs', 'e2fsck', 'e2image', 'mke2fs', 'tune2fs',
                    'resize2fs', 'e2label', 'badblocks', 'mkfs.ext2', 'mkfs.ext3', 'mkfs.ext4',
                    'fsck.ext2', 'fsck.ext3', 'fsck.ext4', 'getfacl', 'setfacl', 'chacl'}


def retired_library(name):
    basename = PurePosixPath(name).name.removeprefix('.wh.')
    return bool(re.match(r'^lib(?:mount|blkid|acl)(?:\.so(?:\.|$)|\.(?:a|la)$)', basename))


def check_runtime_member(item):
    name = PurePosixPath(item.name)
    require(not name.is_absolute() and '..' not in name.parts, 'Unsafe runtime tools path')
    basename = name.name.removeprefix('.wh.')
    require(not retired_library(str(name)), 'Retired runtime library retained in saved layer: ' + str(name))
    require(not (str(name.parent) in COMMAND_DIRS and basename in REMOVED_COMMANDS),
            'Retired OS command retained in saved layer: ' + str(name))
    if item.isfile() or item.islnk():
        require(not item.mode & 0o6000, 'setuid/setgid file retained in saved layer: ' + str(name))
        require(not any('security.capability' in key for key in item.pax_headers),
                'File capabilities retained in saved layer: ' + str(name))


def check_runtime_packages(packages):
    require(not REMOVED_PACKAGES & packages, 'Retired OS package retained in saved layer')


def check_runtime_binary(raw):
    metadata = elf(raw)
    require(not any(retired_library(name) for name in metadata['needed'])
            and not (metadata['soname'] and retired_library(metadata['soname'])),
            'Retired runtime ELF library or dependency retained in saved layer')
    require(not any(name.startswith('acl_') for name in metadata['undefined_symbols']),
            'ACL API import retained in saved layer')
    return metadata
