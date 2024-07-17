#!/usr/bin/env python3

import os
import errno
from fuse import FUSE, FuseOSError, Operations

class OffsetFS(Operations):
    def __init__(self, source_path, offset):
        self.source_path = source_path
        self.offset = offset
        self.file_size = os.path.getsize(source_path) - offset

    def getattr(self, path, fh=None):
        if path != '/file':
            raise FuseOSError(errno.ENOENT)
        
        return {
            'st_mode': 33188,  # 0o100644
            'st_nlink': 1,
            'st_size': self.file_size,
            'st_ctime': os.stat(self.source_path).st_ctime,
            'st_mtime': os.stat(self.source_path).st_mtime,
            'st_atime': os.stat(self.source_path).st_atime
        }

    def read(self, path, size, offset, fh):
        if path != '/file':
            raise FuseOSError(errno.ENOENT)
        
        with open(self.source_path, 'rb') as f:
            f.seek(self.offset + offset)
            return f.read(size)

    def readdir(self, path, fh):
        return ['.', '..', 'file']

    def open(self, path, flags):
        if path != '/file':
            raise FuseOSError(errno.ENOENT)
        if flags & os.O_WRONLY or flags & os.O_RDWR:
            raise FuseOSError(errno.EROFS)  # Read-only file system
        return 0

if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser()
    parser.add_argument('source', help='Source file path')
    parser.add_argument('mountpoint', help='Mount point')
    parser.add_argument('offset', type=int, help='Offset in bytes')
    args = parser.parse_args()

    FUSE(OffsetFS(args.source, args.offset), args.mountpoint, nothreads=True, foreground=True, ro=True)