from dataclasses import dataclass, asdict
import json
from io import TextIOWrapper
from typing import List, Union

@dataclass
class WocSyncCopyTask:
    src_path: str
    dst_path: str
    size: int
    digest: str

@dataclass
class WocSyncPartialCopyTask(WocSyncCopyTask):
    skip: int
    origin_digest: str
    part_digest: str

def serialize_tasks(
        _tasks: List[Union[WocSyncCopyTask, WocSyncPartialCopyTask]],
        file_path: Union[str, TextIOWrapper]
    ):
    if isinstance(file_path, TextIOWrapper):
        for t in _tasks:
            file_path.write(json.dumps(asdict(t)) + "\n")
    else:
        with open(file_path, "w+") as f:
            for t in _tasks:
                f.write(json.dumps(asdict(t)) + "\n")

def deserialize_tasks(
        file_path: str
    ):
    with open(file_path, 'r') as f:
        _lines = f.readlines()
    _tasks: List[Union[WocSyncCopyTask, WocSyncPartialCopyTask]] = []
    for t_l in _lines:
        t_json = json.loads(t_l)
        if "skip" in t_json.keys():
            _tasks.append(WocSyncPartialCopyTask(**t_json))
        else:
            _tasks.append(WocSyncCopyTask(**t_json))
    return _tasks