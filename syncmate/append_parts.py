import os
from tqdm import tqdm
from io import BufferedReader, BufferedWriter
import logging
import subprocess 
import json

from syncmate.base import WocSyncCopyTask, WocSyncPartialCopyTask, deserialize_tasks
from woc.utils import sample_md5
from datetime import datetime

WOCSYNC_LOCAL_CACHE = '/archive/woc/parts/'
WOCSYNC_EXCLUDE_FILE = './exclude.txt'

def copyfileobj_progress(
    fsrc: BufferedReader,
    fdst: BufferedWriter,
    buffer_size: int = 64 * 1024 if os.name != 'nt' else 1024 * 1024,
    desc: str = 'copy',
):
    """
    Copy data from file-like object fsrc to file-like object fdst,
    behaves the same as shutil.copyfileobj but with progress bar.

    ref: python3.10/shutil.py#L187
    """
    src_size = os.fstat(fsrc.fileno()).st_size
    fsrc_read = fsrc.read
    fdst_write = fdst.write

    with tqdm(total=src_size, unit='B', unit_scale=True, desc=desc) as pbar:
        while True:
            buf = fsrc_read(buffer_size)
            if not buf:
                break
            fdst_write(buf)
            pbar.update(len(buf))

def _get_remote_fsize(file_path: str):
    try:
        _out = subprocess.check_output(['rclone', 'size', '--json', file_path])
        return json.loads(_out)['bytes']
    except subprocess.CalledProcessError as e:
        logging.error(f'Failed to get remote file size: {e}')
        return 0

def _remove_remote_file(file_path: str):
    # create a .completed file
    if _get_remote_fsize(file_path + ".completed") == 0:
        p = subprocess.Popen(
            ['rclone', 'rcat', '-vv', '--s3-no-check-bucket', file_path + ".completed"], 
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        # Read and print the output line-by-line
        try:
            p.stdin.write(datetime.now().isoformat().encode('utf-8'))
            p.stdin.close()
            for line in iter(p.stdout.readline, b''):
                print(line.decode('utf-8'), end='')  # Decode bytes to string
        finally:
            p.stdout.close()
            p.wait()
    # delete the original file
    subprocess.run(['rclone', 'delete', file_path])

_EXCLUDE_CACHE = set()
def _on_complete(
    local_path: str,
    remote_path: str,
    remove: bool = False,
):
    _base_path = os.path.basename(local_path)
    # write to exclude rules
    if _base_path not in _EXCLUDE_CACHE:
        _EXCLUDE_CACHE.add(_base_path)
        with open(WOCSYNC_EXCLUDE_FILE, 'a+') as f:
            f.write(_base_path + '\n')

    if remove and os.path.isfile(local_path):
        logging.info(f'Removing {local_path}')
        os.remove(local_path)
    if remove and _get_remote_fsize(remote_path) > 0:
        logging.info(f'Removing {remote_path}')
        _remove_remote_file(remote_path)

def append_part(
    task: WocSyncPartialCopyTask,
    remove: bool = False,
    desc: str = 'append',
    bucket: str = 'r2:woc',
):
    _base_path = os.path.basename(task.src_path)
    _remote_path = f"{bucket}/{_base_path}.part.{task.size}.{task.part_digest}"
    _local_path = os.path.join(WOCSYNC_LOCAL_CACHE, f"{_base_path}.part.{task.size}.{task.part_digest}")
    logging.info(f'append_part: {_local_path} -> {task.dst_path}')

    if len(_EXCLUDE_CACHE) == 0:
        with open(WOCSYNC_EXCLUDE_FILE, 'a+') as f:
            _EXCLUDE_CACHE.update(f.read().splitlines())
    if _base_path in _EXCLUDE_CACHE:
        logging.info(f'{task.dst_path}: already completed')
        _on_complete(_local_path,_remote_path,remove)
        return

    # is completed?
    assert os.path.isfile(task.dst_path), f'{task.dst_path}: not found'
    original_size = os.path.getsize(task.dst_path)
    original_md5 = sample_md5(task.dst_path)
    if original_size == task.size + task.skip and original_md5 == task.digest:
        logging.info(f'{task.dst_path}: already completed')
        _on_complete(_local_path,_remote_path,remove)
        return
    
    # validate part
    assert os.path.isfile(_local_path), f'{_local_path}: not found'
    expected_size, expected_md5 = int(_local_path.split('.')[-2]), _local_path.split('.')[-1]
    part_size = os.path.getsize(_local_path)
    assert part_size == expected_size, f'{_local_path}: size mismatch, expected {expected_size}, got {part_size}'
    part_md5 = sample_md5(_local_path)
    assert part_md5 == expected_md5, f'{_local_path}: md5 mismatch, expected {expected_md5}, got {part_md5}'
    
    # validate origin
    if original_size == task.skip and original_md5 == task.origin_digest:
        with open(_local_path, 'rb') as src_file, open(task.dst_path, 'ab') as dst_file:
            copyfileobj_progress(src_file, dst_file, desc=desc + os.path.basename(task.dst_path))
    elif original_size >= task.skip:  # in case of interruption, seek and copy
        logging.warning(f'{task.dst_path}: size mismatch, expected {task.skip}, got {original_size}, seek and copy')
        with open(_local_path, 'rb') as src_file, open(task.dst_path, 'r+b') as dst_file:
            dst_file.seek(task.skip)
            copyfileobj_progress(src_file, dst_file, desc=desc + os.path.basename(task.dst_path))
    else: # file too small, abort
        logging.error(f'{task.dst_path}: size mismatch, expected {task.skip}, got {original_size}, aborting')
        return

    # check the size of the combined file
    full_size = os.path.getsize(task.dst_path)
    assert full_size == task.size + task.skip, f'{task.dst_path}: size mismatch, expected {task.size + task.skip}, got {full_size}'
    full_md5 = sample_md5(task.dst_path)
    assert full_md5 == task.digest, f'{task.dst_path}: md5 mismatch, expected {task.digest}, got {full_md5}'

    _on_complete(_local_path,_remote_path,remove)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Append parts')
    parser.add_argument('task_file', type=str, help='Task file')
    parser.add_argument('--remove', action='store_true', help='Remove original files in local cache')
    args = parser.parse_args()

    _tasks = deserialize_tasks(args.task_file)
    _partial_tasks = list(filter(lambda x: isinstance(x, WocSyncPartialCopyTask), _tasks))

    logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(asctime)s - %(message)s')

    for idx, task in enumerate(_partial_tasks):
        try:
            append_part(task, args.remove, desc=f"({idx+1}/{len(_partial_tasks)}) ")
        except AssertionError as e:
            logging.error("\033[91m" + f"{os.path.basename(task.dst_path)}: {e}" + "\033[0m")