"""Pack loose git objects with line-based delta compression.

dulwich's pure-Python delta generator runs difflib over raw bytes, which is
far too slow for a 600 KB manuscript. Because normalize() puts one paragraph
per line, a line-level diff finds almost all the shared content quickly.
The output is a standard git pack: real git reads it with no special handling.
"""

from difflib import SequenceMatcher
from typing import Iterator, List, Tuple

from dulwich.pack import (
    UnpackedObject,
    _delta_encode_size,
    _encode_copy_operation,
    _MAX_COPY_LEN,
)

WINDOW = 4  # how many previous objects of the same type to try as a delta base


def line_delta(base: bytes, target: bytes) -> Iterator[bytes]:
    """Git delta instructions transforming ``base`` into ``target``."""
    yield _delta_encode_size(len(base))
    yield _delta_encode_size(len(target))

    a = base.splitlines(keepends=True)
    b = target.splitlines(keepends=True)
    a_off = _offsets(a)
    b_off = _offsets(b)
    matcher = SequenceMatcher(None, a, b, autojunk=False)
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            start, length = a_off[i1], a_off[i2] - a_off[i1]
            while length > 0:
                n = min(length, _MAX_COPY_LEN)
                yield _encode_copy_operation(start, n)
                start += n
                length -= n
        elif op in ("replace", "insert"):
            data = target[b_off[j1]:b_off[j2]]
            for k in range(0, len(data), 127):
                chunk = data[k:k + 127]
                yield bytes([len(chunk)])
                yield chunk


def _offsets(lines: List[bytes]) -> List[int]:
    out = [0]
    for line in lines:
        out.append(out[-1] + len(line))
    return out


def pack_records(object_store, ordered: List[Tuple[bytes, bool]]) -> Iterator[UnpackedObject]:
    """Pack records for a bundle. ``ordered``: (hex sha, is_blob), oldest content first.

    Deltas already stored in packs are copied as-is; only objects stored
    whole (recent loose objects, pack bases) are diffed, each blob against
    the previous blob in order. That keeps writing a bundle fast on every save.
    """
    from dulwich.pack import find_reusable_deltas, full_unpacked_object, sha_to_hex

    reused, reused_bases = set(), set()
    for unpacked in find_reusable_deltas(object_store, {oid for oid, _ in ordered}):
        reused.add(sha_to_hex(unpacked.sha()))
        reused_bases.add(sha_to_hex(unpacked.delta_base))
        yield unpacked

    prev_blob = None  # hex sha of the previous blob in order
    for oid, is_blob in ordered:
        if oid not in reused:
            obj = object_store[oid]
            record = full_unpacked_object(obj)
            # Never turn a reused delta's base into a delta: that could form a cycle.
            if is_blob and prev_blob is not None and oid not in reused_bases:
                raw = obj.as_raw_string()
                delta = list(line_delta(object_store[prev_blob].as_raw_string(), raw))
                size = sum(map(len, delta))
                if size < len(raw):
                    record = UnpackedObject(obj.type_num, sha=obj.sha().digest(),
                                            delta_base=bytes.fromhex(prev_blob.decode()),
                                            decomp_len=size, decomp_chunks=delta)
            yield record
        if is_blob:
            prev_blob = oid


def pack_loose_objects(object_store) -> int:
    """Move all loose objects into one new delta-compressed pack."""
    shas = list(object_store._iter_loose_objects())
    objects = [o for o in (object_store._get_loose_object(s) for s in shas) if o is not None]
    if not objects:
        return 0

    # Largest first (git's heuristic): later, smaller versions delta against
    # earlier ones; the base is stored whole.
    objects.sort(key=lambda o: (o.type_num, -o.raw_length()))

    def records() -> Iterator[UnpackedObject]:
        recent = []  # (sha digest, type_num, raw bytes)
        for obj in objects:
            raw = obj.as_raw_string()
            best, best_len, best_base = [raw], len(raw), None
            for base_sha, base_type, base_raw in recent:
                if base_type != obj.type_num:
                    continue
                delta = list(line_delta(base_raw, raw))
                size = sum(map(len, delta))
                if size < best_len:
                    best, best_len, best_base = delta, size, base_sha
            digest = obj.sha().digest()
            yield UnpackedObject(
                obj.type_num,
                sha=digest,
                delta_base=best_base,
                decomp_len=best_len,
                decomp_chunks=best,
            )
            recent.insert(0, (digest, obj.type_num, raw))
            del recent[WINDOW:]

    object_store.add_pack_data(len(objects), records())
    for obj in objects:
        object_store.delete_loose_object(obj.id)
    return len(objects)
