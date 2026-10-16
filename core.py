"""Read-only discovery and PE/PiPL inspection. No plugin-loading or file patching."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import hashlib
import os
import struct
import winreg


@dataclass
class Plugin:
    path: str
    display_name: str
    match_name: str
    category: str
    resource_id: str
    language: int
    sha256: str = ""
    chinese_name: str = ""


def resources(path: Path):
    data = path.read_bytes()
    def unpack(fmt, offset):
        if offset < 0 or offset + struct.calcsize(fmt) > len(data):
            raise ValueError("PE resource offset outside file")
        return struct.unpack_from(fmt, data, offset)
    if data[:2] != b"MZ":
        raise ValueError("Not a Windows PE file")
    pe = unpack("<I", 60)[0]
    if data[pe:pe+4] != b"PE\0\0":
        raise ValueError("Invalid PE signature")
    count = unpack("<H", pe+6)[0]
    opt_len = unpack("<H", pe+20)[0]
    opt = pe+24
    magic = unpack("<H", opt)[0]
    if magic not in (0x10b, 0x20b):
        raise ValueError("Unsupported PE optional header")
    directories = opt+(112 if magic == 0x20b else 96)
    root_rva, root_size = unpack("<II", directories+16)
    if not root_rva or not root_size:
        return []
    sections = []
    for i in range(count):
        off = opt+opt_len+40*i
        virtual_size, virtual_address, raw_size, raw_pointer = unpack("<IIII", off+8)
        sections.append((virtual_address, max(virtual_size, raw_size), raw_pointer, raw_size))
    def rva(value):
        for va, size, ptr, raw_size in sections:
            if va <= value < va+size and value-va < raw_size:
                return ptr+value-va
        raise ValueError("Resource RVA outside file-backed sections")
    root = rva(root_rva)
    leaves = []
    visited = set()
    def walk(relative, names=()):
        if len(names) > 3 or relative in visited:
            raise ValueError("Invalid recursive resource directory")
        visited.add(relative)
        off = root+relative
        named, numbered = unpack("<HH", off+12)
        if named+numbered > 8192:
            raise ValueError("Too many resource entries")
        for i in range(named+numbered):
            key, child = unpack("<II", off+16+8*i)
            if key & 0x80000000:
                pos = root+(key & 0x7fffffff)
                length = unpack("<H", pos)[0]
                end = pos+2+length*2
                if end > len(data):
                    raise ValueError("Invalid resource name")
                key = data[pos+2:end].decode("utf-16le")
            if child & 0x80000000:
                walk(child & 0x7fffffff, names+(key,))
            else:
                address, size = unpack("<II", root+child)
                start = rva(address)
                if start+size > len(data):
                    raise ValueError("Invalid resource size")
                leaves.append((names+(key,), data[start:start+size]))
        visited.remove(relative)
    walk(0)
    return leaves


def pipl_properties(data: bytes) -> dict[str, bytes]:
    # Windows PiPLTool emits two reserved bytes, a uint32 version and property count.
    candidates = (2, 0)
    for prefix in candidates:
        try:
            version, count = struct.unpack_from("<II", data, prefix)
            if version != 0 or not 1 <= count <= 1024:
                continue
            pos = prefix+8
            props = {}
            for _ in range(count):
                vendor, key, prop_id, length = struct.unpack_from("<4s4sII", data, pos)
                pos += 16
                if length > len(data)-pos:
                    raise ValueError("Invalid PiPL property size")
                if vendor == b"MIB8":
                    props[key[::-1].decode("ascii")] = data[pos:pos+length]
                pos += (length+3) & ~3
            if "name" in props:
                return props
        except (ValueError, struct.error, UnicodeDecodeError):
            continue
    raise ValueError("Unsupported PiPL property layout")


def pascal_string(value: bytes) -> str:
    if not value:
        return ""
    length = value[0]
    # Some installed Continuum builds count the prefix byte in an aligned field.
    if length > len(value)-1 and length != len(value):
        raise ValueError("Invalid PiPL Pascal string")
    raw = value[1:min(1+length,len(value))].split(b"\0",1)[0]
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


def inspect_plugin(path: Path) -> list[Plugin]:
    result = []
    for resource_path, data in resources(path):
        if str(resource_path[0]).upper() != "PIPL":
            continue
        props = pipl_properties(data)
        name = pascal_string(props.get("name", b""))
        match = pascal_string(props.get("eMNA", b""))
        # Only effect plugins; other PiPL kinds have no effect match name.
        if not name or not match:
            continue
        result.append(Plugin(str(path), name, match, pascal_string(props.get("catg", b"")), str(resource_path[1]), int(resource_path[-1])))
    return result


def file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def installations() -> list[dict]:
    from ae_paths import discover
    from runtime import data_root
    from ae_paths import read_saved
    saved,_=read_saved(data_root()/'ae-installations.json')
    return discover(saved)


def shared_roots() -> list[Path]:
    """Adobe's common MediaCore folders, loaded by every installed AE version."""
    program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    result = [program_files/"Adobe"/"Common"/"Plug-ins"/"7.0"/"MediaCore", program_files/"Common Files"/"Adobe"/"Plug-Ins"/"7.0"/"MediaCore"]
    return list(dict.fromkeys(p for p in result if p.is_dir()))


def plugin_roots() -> list[Path]:
    result = [Path(x["plugins"]) for x in installations()] + shared_roots()
    return list(dict.fromkeys(p for p in result if p.is_dir()))


def scan(roots: list[Path], progress=None):
    plugins, errors, seen = [], [], set()
    for root in roots:
        for directory, subdirs, names in os.walk(root, followlinks=False):
            subdirs[:] = [d for d in subdirs if not (Path(directory)/d).is_symlink()]
            for name in names:
                if not name.lower().endswith(".aex"):
                    continue
                path = Path(directory)/name
                identity = str(path.resolve()).casefold()
                if identity in seen:
                    continue
                seen.add(identity)
                try:
                    plugins.extend(inspect_plugin(path))
                except (OSError, ValueError, struct.error) as error:
                    errors.append({"path":str(path),"error":str(error)})
                if progress:
                    progress(len(seen), name)
    return plugins, errors
