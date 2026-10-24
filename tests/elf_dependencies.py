"""Read dynamic imports without executing an image binary; reject incomplete ELF metadata."""
import struct

from check_caddy_runtime import require


def elf(raw):
    """Parse ELF64 little-endian dynamic imports without executing the file."""
    require(len(raw) >= 64 and raw[:6] == b'\x7fELF\x02\x01', 'Unsupported or truncated ELF')
    header = struct.unpack_from('<16sHHIQQQIHHHHHH', raw)
    require(header[2] == 62 and header[3] == 1, 'ELF is not x86-64 version 1')
    phoff, shoff, phentsize, phnum, shentsize, shnum = header[5], header[6], header[9], header[10], header[11], header[12]
    require(phnum < 65535 and shnum < 65535 and (not phnum or phentsize == 56), 'Unsupported ELF header table')
    require(phoff + phnum * phentsize <= len(raw), 'Truncated ELF program headers')
    dynamic_program = any(struct.unpack_from('<I', raw, phoff + i * phentsize)[0] == 2 for i in range(phnum))
    if not shnum:
        require(not dynamic_program and shoff == 0, 'Dynamic ELF lacks inspectable sections')
        return {'needed': [], 'undefined_symbols': [], 'soname': None, 'dynamic': False}
    require(shentsize == 64 and shoff + shnum * shentsize <= len(raw), 'Truncated ELF sections')
    sections = [struct.unpack_from('<IIQQQQIIQQ', raw, shoff + i * shentsize) for i in range(shnum)]

    def body(section):
        offset, size = section[4:6]
        require(offset + size <= len(raw), 'Truncated ELF section data')
        return raw[offset:offset + size]

    def string(table, offset):
        require(offset < len(table), 'ELF string offset out of bounds')
        end = table.find(b'\0', offset)
        require(end >= 0, 'ELF string lacks terminator')
        return table[offset:end].decode('ascii', errors='strict')

    needed, undefined, soname = [], set(), None
    dynamics = [section for section in sections if section[1] == 6]
    symbols = [section for section in sections if section[1] == 11]
    require(not dynamic_program or len(dynamics) == 1, 'Missing or ambiguous dynamic section')
    for section in dynamics + symbols:
        link = section[6]
        require(link < shnum and sections[link][1] == 3, 'ELF table lacks string table')
        strings = body(sections[link])
        data = body(section)
        if section[1] == 6:
            require(section[9] == 16 and len(data) % 16 == 0, 'Invalid dynamic table size')
            terminated = False
            for offset in range(0, len(data), 16):
                tag, value = struct.unpack_from('<qQ', data, offset)
                if tag == 0:
                    terminated = True
                    break
                if tag == 1:
                    needed.append(string(strings, value))
                elif tag == 14:
                    soname = string(strings, value)
            require(terminated, 'Dynamic table lacks terminator')
        else:
            require(section[9] == 24 and len(data) % 24 == 0, 'Invalid dynamic symbol table size')
            for offset in range(0, len(data), 24):
                name, info, other, index, value, size = struct.unpack_from('<IBBHQQ', data, offset)
                if name and index == 0:
                    undefined.add(string(strings, name))
    require(not dynamic_program or symbols, 'Dynamic ELF lacks inspectable imported symbols')
    return {'needed': sorted(set(needed)), 'undefined_symbols': sorted(undefined),
            'soname': soname, 'dynamic': dynamic_program}
