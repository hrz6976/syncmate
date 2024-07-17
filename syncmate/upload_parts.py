import subprocess
import os
import json
import logging
import multiprocessing
from tqdm.auto import tqdm
from typing import List

from .base import WocSyncCopyTask, WocSyncPartialCopyTask, deserialize_tasks

WORKERS = 3
RETRIES = 3
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

def _get_remote_fsize(file_path: str):
    _out = subprocess.check_output(['rclone', 'size', '--json', file_path])
    return json.loads(_out)['bytes']

def upload_part(
        task: WocSyncPartialCopyTask,
        bucket: str
    ):
    _remote_fname = f"{bucket}/{os.path.basename(task.src_path)}.part.{task.size}.{task.part_digest}"
    try:
        _remote_size = _get_remote_fsize(_remote_fname)
        if _remote_size == task.size:
            logging.info(f"Part {task.src_path} size identical, skipping")
            return
    except subprocess.CalledProcessError:
        pass

    with open(task.src_path, 'rb') as f:
        f.seek(task.skip)  # Seek to the offset
        p = subprocess.Popen(
            ['rclone', 'rcat', '-vv', '--s3-no-check-bucket', _remote_fname], 
            stdin=f, 
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        # Read and print the output line-by-line
        try:
            for line in iter(p.stdout.readline, b''):
                print(line.decode('utf-8'), end='')  # Decode bytes to string
        finally:
            p.stdout.close()
            p.wait()
    _remote_size = _get_remote_fsize(_remote_fname)
    assert _remote_size == task.size, f"Size mismatch: {task.size} != {_remote_size}"

def _worker(
    args
):
    task, bucket, retries = args
    while retries > 0:
        try:
            upload_part(task, bucket)
            return
        except Exception as e:
            logging.error(f"Error: {e}")
            retries -= 1

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Upload parts')
    parser.add_argument('-b', '--bucket', type=str, required=True, help='Bucket name')
    parser.add_argument('-t', '--tasks', type=str, required=True, help='Tasks file')
    parser.add_argument('--workers', type=int, help='Number of workers', default=WORKERS)
    parser.add_argument('--retries', type=int, help='Number of retries', default=RETRIES)
    args = parser.parse_args()

    pool = multiprocessing.Pool(args.workers)
    _tasks = deserialize_tasks(args.tasks)
    _tasks = [t for t in _tasks if isinstance(t, WocSyncPartialCopyTask)]

    with tqdm(total=len(_tasks)) as pbar:
        for _ in pool.imap_unordered(_worker, [(t, args.bucket, args.retries) for t in _tasks]):
            pbar.update(1)

    # for t in tqdm(_tasks):
    #     upload_part(t, args.bucket)