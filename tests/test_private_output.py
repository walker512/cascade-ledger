"""Synthetic publication privacy, collision and interrupted-write regressions."""
import concurrent.futures
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from cascade_ledger import core, requests

ROOT=Path(__file__).resolve().parents[1]
MODES=((core,'single.json','redundant.json'),(requests,'request-timeouts.json','request-adjusted.json'))

class PrivateOutputTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.p=Path(self.tmp.name)
    def build(self,mode,out):
        module,left,right=mode
        return module.build(ROOT/'examples'/left,ROOT/'examples'/right,out)

    @unittest.skipUnless(os.name=='posix','POSIX file modes')
    def test_both_modes_private_with_permissive_umask(self):
        for mask in (0,0o022):
            previous=os.umask(mask)
            try:
                for i,mode in enumerate(MODES):
                    out=self.p/f'{mask}-{i}';self.build(mode,out)
                    self.assertEqual(stat.S_IMODE(out.stat().st_mode),0o700)
                    for file in out.iterdir():self.assertEqual(stat.S_IMODE(file.stat().st_mode),0o600)
                    mode[0].verify(out)
                observed=os.umask(mask);self.assertEqual(observed,mask)
            finally:os.umask(previous)

    @unittest.skipUnless(os.name=='posix','POSIX file modes')
    def test_private_before_any_bytes_are_written(self):
        original=os.fdopen;seen=[]
        def inspect(fd,*args,**kwargs):
            st=os.fstat(fd);self.assertEqual(st.st_size,0);self.assertEqual(stat.S_IMODE(st.st_mode),0o600)
            seen.append(fd);return original(fd,*args,**kwargs)
        out=self.p/'out';out.mkdir(mode=0o700)
        with patch.object(core.os,'fdopen',inspect):core.private_write(out/'data',b'private')
        self.assertEqual(len(seen),1)

    def test_existing_directory_file_and_contents_preserved(self):
        for i,mode in enumerate(MODES):
            for kind in ('empty','full','file'):
                out=self.p/f'{i}-{kind}'
                if kind=='file':out.write_bytes(b'keep')
                else:
                    out.mkdir()
                    if kind=='full':(out/'keep').write_bytes(b'keep')
                before=out.stat()
                with self.assertRaises(FileExistsError):self.build(mode,out)
                self.assertEqual(out.stat().st_ino,before.st_ino)
                if kind=='file':self.assertEqual(out.read_bytes(),b'keep')
                elif kind=='full':self.assertEqual((out/'keep').read_bytes(),b'keep')
                else:self.assertEqual(list(out.iterdir()),[])

    @unittest.skipUnless(os.name=='posix','POSIX symlinks')
    def test_dangling_output_and_member_symlinks_refused(self):
        out=self.p/'out';out.symlink_to(self.p/'missing')
        with self.assertRaises(FileExistsError):self.build(MODES[0],out)
        self.assertTrue(out.is_symlink());self.assertFalse((self.p/'missing').exists())
        target=self.p/'target';target.write_bytes(b'keep');link=self.p/'link';link.symlink_to(target)
        with self.assertRaises(FileExistsError):core.private_write(link,b'overwrite')
        self.assertEqual(target.read_bytes(),b'keep')

    def test_member_regular_file_not_overwritten(self):
        path=self.p/'file';path.write_bytes(b'keep')
        with self.assertRaises(FileExistsError):core.private_write(path,b'overwrite')
        self.assertEqual(path.read_bytes(),b'keep')

    def test_threaded_writers_one_winner(self):
        for i,mode in enumerate(MODES):
            out=self.p/str(i)
            def run(_):
                try:self.build(mode,out);return True
                except FileExistsError:return False
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(run,range(24)))
            self.assertEqual(sum(results),1);mode[0].verify(out)

    def test_real_process_competition(self):
        out=self.p/'processes'
        args=[sys.executable,'-m','cascade_ledger','compare',str(ROOT/'examples/single.json'),str(ROOT/'examples/redundant.json'),'--out',str(out)]
        children=[subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE) for _ in range(8)]
        results=[]
        for child in children:
            child.communicate(timeout=20);results.append(child.returncode)
        self.assertEqual(results.count(0),1);self.assertEqual(results.count(2),7);core.verify(out)

    def test_failed_copy_private_partial_rejected_no_retry_overwrite(self):
        for i,mode in enumerate(MODES):
            out=self.p/str(i);original=core.private_write;calls=[]
            def fail_second(path,value):
                calls.append(path)
                if len(calls)==2:raise OSError('synthetic write failure')
                original(path,value)
            with patch.object(core,'private_write',fail_second):
                with self.assertRaises(OSError):self.build(mode,out)
            self.assertEqual(len(list(out.iterdir())),1)
            if os.name=='posix':
                self.assertEqual(stat.S_IMODE(out.stat().st_mode),0o700)
                self.assertEqual(stat.S_IMODE(next(out.iterdir()).stat().st_mode),0o600)
            with self.assertRaises(core.Invalid):mode[0].verify(out)
            with self.assertRaises(FileExistsError):self.build(mode,out)

    def test_invalid_input_does_not_reserve_destination(self):
        bad=self.p/'bad.json';bad.write_text('{}');out=self.p/'out'
        for module,_,_ in MODES:
            with self.assertRaises(core.Invalid):module.build(bad,bad,out)
            self.assertFalse(out.exists())
