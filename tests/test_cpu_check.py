"""The native helpers must run on any x86-64 CPU (a build for the build machine's CPU once crashed AE on AMD Zen 3)."""
import unittest
from pathlib import Path
import cpu_check

root=Path(cpu_check.__file__).resolve().parent
SHIPPED=('ae_parameters_v6.dll','ae_parameters_v7.dll','ae_parameters_v8.dll','ae_parameters_v9.dll','ae_probe.dll','ae_scan_v1.dll')

class KindTests(unittest.TestCase):
    def test_extensions_are_named(self):
        for mnemonic,expected in (('vgf2p8affineqb','gfni'),('gf2p8mulb','gfni'),('vmovdqu','avx'),('vpxor','avx'),('rorx','bmi'),('lzcnt','bmi'),
                                  ('popcnt','popcnt'),('movbe','movbe'),('pshufb','ssse3'),('pminud','sse4.1'),('crc32','sse4.2'),('aesenc','aes'),('sha256rnds2','sha')):
            self.assertEqual(cpu_check.kind(mnemonic),expected,mnemonic)
    def test_baseline_instructions_pass(self):
        # SSE/SSE2 names that share a prefix with an extension, and tzcnt (rep bsf, runs as bsf on older CPUs).
        for mnemonic in ('mov','andnps','andnpd','pextrw','pinsrw','tzcnt','bsf','bsr','verr','pshufd','movdqu','cmovne','paddq'):
            self.assertIsNone(cpu_check.kind(mnemonic),mnemonic)

class BuildTests(unittest.TestCase):
    def test_every_helper_is_built_for_baseline_x86_64(self):
        source=(root/'build.py').read_text(encoding='utf-8')
        self.assertIn('CPU=["-target","x86_64-windows-gnu","-mcpu=baseline"]',source)
        self.assertIn('"ziglang","cc",*CPU,',source)
        self.assertEqual(source.count('"ziglang","cc"'),1,'every helper goes through compile_dll')
    def test_shipped_helpers_have_no_newer_instructions(self):
        built=[root/'native'/name for name in SHIPPED if (root/'native'/name).is_file()]
        if not built:self.skipTest('native helpers not built')
        for path in built:
            with self.subTest(path.name):self.assertEqual(cpu_check.extended_instructions(path),{})
    def test_installer_refuses_newer_instructions(self):
        source=(root/'make_installer.py').read_text(encoding='utf-8')
        self.assertIn('scan(app);scan(support);check_cpu(app)',source)

if __name__=='__main__':unittest.main()
