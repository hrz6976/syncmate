from io import TextIOWrapper
import os
import logging
import json

from typing import List, Union
from tqdm.auto import tqdm
from woc.local import WocMapsLocal
from woc.utils import sample_md5

from .base import WocSyncCopyTask, WocSyncPartialCopyTask, serialize_tasks

def infer_path(
    woc_dst: WocMapsLocal,
    fname: str
):
    if 'Full' in fname: # map
        map_name = fname.split('Full')[0]
        try:
            _map_obj = woc_dst._lookup[map_name]
        except KeyError:
            _map_obj = woc_dst.maps[0]
        _dirpath = os.path.dirname(_map_obj.shards[0].path)
        return os.path.join(_dirpath, fname)
    elif '_' in fname: # object
        map_name = fname.split('_')[0]
        try:
            _map_obj = woc_dst._lookup[map_name]
        except KeyError:
            if 'sha' in fname:
                _sha1_objs = list(filter(lambda x: 'sha' in x.name, woc_dst.objects))
                _map_obj = _sha1_objs[0]
            else:
                _non_sha1_objects = list(filter(
                        lambda x: 'sha' not in x.name and x.name not in ('blob'), 
                    woc_dst.objects))
                _map_obj = _non_sha1_objects[0]
        _dirpath = os.path.dirname(_map_obj.shards[0].path)
        return os.path.join(_dirpath, fname)
    raise ValueError("Expected a mapping or object file")

def generate_tasks(
    woc_src: WocMapsLocal, woc_dst: WocMapsLocal
):
    _dst_files = {}
    for _m in woc_dst.maps:
        for _f in _m.shards + list(_m.larges.values()):
            _dst_files[os.path.basename(_f.path)] = _f
    for _o in woc_dst.objects:
        for _f in _o.shards:
            _dst_files[os.path.basename(_f.path)] = _f

    _src_files = {}
    for _m in woc_src.maps:
        for _f in _m.shards + list(_m.larges.values()):
            _src_files[os.path.basename(_f.path)] = _f
    for _o in woc_src.objects:
        for _f in _o.shards:
            _src_files[os.path.basename(_f.path)] = _f

    _tasks: List[Union[WocSyncCopyTask, WocSyncPartialCopyTask]] = []

    for fname, src_file in tqdm(_src_files.items()):
        # if doesn't exist in dst, copy
        if not fname in _dst_files:
            _dst_path = infer_path(woc_dst, fname)
            logging.debug(f"{fname}: dst doesn't exist, full copy to {_dst_path}")
            _tasks.append(WocSyncCopyTask(
                src_path=src_file.path,
                dst_path=_dst_path,
                size=src_file.size,
                digest=src_file.digest
            ))
            continue

        # if exists
        dst_file = _dst_files[fname]
        if src_file.size < dst_file.size:
            logging.warning(f"{fname}: src file size {src_file.size} < dst file size {dst_file.size}, full copy")
            # src file is smaller, full copy
            _tasks.append(WocSyncCopyTask(
                src_path=src_file.path,
                dst_path=dst_file.path,
                size=src_file.size,
                digest=src_file.digest
            ))
            continue

        _head_digest = sample_md5(src_file.path, size=dst_file.size)
        if src_file.size == dst_file.size and _head_digest == dst_file.digest:
            logging.debug(f"{fname}: size {src_file.size} and digest {_head_digest} match, skipping")
            # digest and size match
            continue

        elif src_file.size > dst_file.size and _head_digest == dst_file.digest:
            # head is the same, partial copy
            _tail_size = src_file.size - dst_file.size
            _tail_digest = sample_md5(src_file.path, skip=dst_file.size, size=_tail_size)
            logging.debug(f"{fname}: head size {dst_file.size} and digest {_head_digest} match, partial copy")
            _tasks.append(WocSyncPartialCopyTask(
                src_path=src_file.path,
                dst_path=dst_file.path,
                size=_tail_size,
                digest=src_file.digest,
                skip=dst_file.size,
                part_digest=_tail_digest,
                origin_digest=dst_file.digest
            ))
        else:
            logging.warning(f"{fname}: head size {dst_file.size}, digest mismatch {_head_digest}!={dst_file.digest}, full copy")
            # digest mismatch, full copy
            _tasks.append(WocSyncCopyTask(
                src_path=src_file.path,
                dst_path=dst_file.path,
                size=src_file.size,
                digest=src_file.digest
            ))
    
    return _tasks


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Generate sync tasks')
    parser.add_argument('-s', '--src-profile', type=str, required=True, help='Source profile file')
    parser.add_argument('-d', '--dst-profile', type=str, required=True, help='Destination profile file')
    # default is stdout
    parser.add_argument('-o', '--output', help='Output tasks file', type=argparse.FileType('w'), default='-')
    parser.add_argument('-v', '--verbose', action='store_true', help='Verbose logging'  )
    # multiple version support
    parser.add_argument('--src-version', type=str, nargs='+', help='Version of the source profile to use')
    parser.add_argument('--dst-version', type=str, nargs='+', help='Version of the destination profile to use')
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)

    woc_dst = WocMapsLocal(args.dst_profile, version=args.dst_version if args.dst_version else None)
    woc_src = WocMapsLocal(args.src_profile, version=args.src_version if args.src_version else None)

    _tasks = generate_tasks(woc_src, woc_dst)
    serialize_tasks(_tasks, args.output)

