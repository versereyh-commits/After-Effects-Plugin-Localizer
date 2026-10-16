"""Update check: manifest validation, verified download, and the window's offer / skip / install flow."""
import datetime,hashlib,io,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError,URLError
from test_gui import GuiCase
import app_live,updater
from updater import UpdateError

# The release repository is not created yet; these tests stand in a configured one.
REPO='owner-test/test-releases'
_repo=patch.object(updater,'UPDATE_REPO',REPO)
def setUpModule():_repo.start()
def tearDownModule():_repo.stop()

PAYLOAD=b'MZ'+b'installer-bytes'*1000
def manifest(version='9.1.0',payload=PAYLOAD,**installer):
    return {'schemaVersion':1,'version':version,'published':'2026-10-16','notes':'修复若干问题\n第二行',
            'installer':{'name':f'ae-localizer-setup-{version}.exe','bytes':len(payload),'sha256':hashlib.sha256(payload).hexdigest(),**installer}}

class Reply(io.BytesIO):
    def __init__(self,data,url='https://objects.githubusercontent.com/x'):super().__init__(data);self.url=url
    def geturl(self):return self.url

class Server:
    """Serves the manifest and the installer by URL; records what was asked for."""
    def __init__(self,manifest=None,payload=PAYLOAD,status=None,final='https://objects.githubusercontent.com/x'):
        self.manifest=manifest;self.payload=payload;self.status=status;self.final=final;self.urls=[]
    def __call__(self,request,timeout):
        url=request.full_url;self.urls.append(url)
        if self.status:raise HTTPError(url,self.status,'error',{},None)
        if url.endswith('/'+updater.MANIFEST):return Reply(self.manifest if isinstance(self.manifest,bytes) else json.dumps(self.manifest).encode('utf8'),self.final)
        return Reply(self.payload,self.final)

class VersionTests(unittest.TestCase):
    def test_compare(self):
        self.assertTrue(updater.is_newer('0.8.1','0.8.0'));self.assertTrue(updater.is_newer('0.10.0','0.9.9'));self.assertTrue(updater.is_newer('1.0','0.99.9'))
        self.assertFalse(updater.is_newer('0.8.0','0.8.0'));self.assertFalse(updater.is_newer('0.8','0.8.0'))
        for bad in ('','v0.8','0.8.0-beta','1',None):
            with self.assertRaises(ValueError):updater.parse_version(bad)

class ManifestTests(unittest.TestCase):
    def test_accepts_a_complete_manifest(self):
        self.assertEqual(updater.validate(manifest())['installer']['name'],'ae-localizer-setup-9.1.0.exe')
    def test_rejects_anything_unexpected(self):
        cases=[{**manifest(),'schemaVersion':2},{**manifest(),'version':'latest'},{**manifest(),'notes':'x'*4001},manifest(name='../evil.exe'),manifest(name='安装包.exe'),
               manifest(name='setup.msi'),manifest(bytes=0),manifest(bytes=True),manifest(bytes=updater.MAX_INSTALLER+1),manifest(sha256='ABC'),{'schemaVersion':1,'version':'1.0'},[]]
        for case in cases:
            with self.assertRaises(UpdateError,msg=str(case)[:80]):updater.validate(case)

class CheckTests(unittest.TestCase):
    def test_newer_release_is_offered_and_only_the_manifest_is_fetched(self):
        server=Server(manifest())
        self.assertEqual(updater.check('0.8.0',opener=server)['version'],'9.1.0')
        self.assertEqual(server.urls,[f'https://github.com/{updater.UPDATE_REPO}/releases/latest/download/update.json'])
        self.assertIsNone(updater.check('9.1.0',opener=Server(manifest())))
    def test_failures_are_explained(self):
        for server,message in ((Server(status=404),'还没有发布'),(Server(status=500),'HTTP 500'),(Server(manifest(),final='http://example.com/x'),'HTTPS'),
                               (Server(b'{'),'无法解析'),(Server(b' '*(updater.MAX_MANIFEST+1)),'过大')):
            with self.assertRaisesRegex(UpdateError,message):updater.check('0.8.0',opener=server)
        def offline(request,timeout):raise URLError('down')
        with self.assertRaisesRegex(UpdateError,'无法连接'):updater.check('0.8.0',opener=offline)

class DownloadTests(unittest.TestCase):
    def setUp(self):self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.folder=Path(self.temp.name)/'updates'
    def test_verified_download(self):
        seen=[];server=Server(manifest())
        path=updater.download(manifest(),self.folder,progress=lambda done,total:seen.append((done,total)),opener=server)
        self.assertEqual(path.read_bytes(),PAYLOAD);self.assertEqual(seen[-1],(len(PAYLOAD),len(PAYLOAD)))
        self.assertEqual(server.urls,[f'https://github.com/{updater.UPDATE_REPO}/releases/download/v9.1.0/ae-localizer-setup-9.1.0.exe'])
        # An already verified file is reused without going online.
        self.assertEqual(updater.download(manifest(),self.folder,opener=Server(status=500)),path)
    def test_mismatches_leave_nothing_behind(self):
        for payload,message in ((PAYLOAD[:-1]+b'X','校验值'),(PAYLOAD+b'extra','比更新信息里的大'),(PAYLOAD[:100],'不完整')):
            with self.assertRaisesRegex(UpdateError,message):updater.download(manifest(),self.folder,opener=Server(payload=payload))
            self.assertEqual(list(self.folder.iterdir()),[])
        with self.assertRaisesRegex(UpdateError,'已取消'):updater.download(manifest(),self.folder,cancelled=lambda:True,opener=Server())
        self.assertEqual(list(self.folder.iterdir()),[])
    def test_publisher_manifest_matches_the_file(self):
        installer=Path(self.temp.name)/'ae-localizer-setup-9.1.0.exe';installer.write_bytes(PAYLOAD)
        self.assertEqual(updater.make_manifest(installer,'9.1.0','说明','2026-10-16'),updater.validate(manifest()|{'notes':'说明'}))

class WindowUpdateTests(GuiCase):
    def prefs(self):
        try:return json.loads((Path(self.temp.name)/'ui.json').read_text(encoding='utf8'))
        except OSError:return {}
    def wait(self):
        self.assertTrue(self.window.update_job.wait(3000));self.app.processEvents()
    def test_settings_card_defaults_to_checking(self):
        w=self.window;self.assertTrue(w.update_box.isChecked());self.assertIn(f'当前版本 {app_live.APP_VERSION}',w.update_info.text())
        self.assertFalse(w.update_notice.isVisibleTo(w))
        w.update_box.setChecked(False);self.assertIs(self.prefs()['updateCheck'],False)
        with patch.object(w,'check_update') as check:w.auto_check_update()
        check.assert_not_called()
    def test_startup_check_shows_a_banner_and_is_kept_for_the_day(self):
        w=self.window;offer=updater.validate(manifest())
        with patch.object(app_live.updater,'check',return_value=offer):w.auto_check_update();self.wait()
        self.assertTrue(w.update_notice.isVisibleTo(w));self.assertEqual(w.update_notice.text(),'有新版本 9.1.0');self.assertIn('修复若干问题',w.update_notice.toolTip())
        self.assertEqual(self.prefs()['updateCheckedOn'],datetime.date.today().isoformat());self.assertEqual(self.prefs()['updateOffer']['version'],'9.1.0')
        w.update_notice.hide()
        with patch.object(app_live.updater,'check',side_effect=AssertionError('no second check today')):w.auto_check_update()
        self.assertTrue(w.update_notice.isVisibleTo(w))
    def test_failed_startup_check_is_quiet(self):
        w=self.window
        with patch.object(app_live.updater,'check',side_effect=UpdateError('无法连接更新服务器，请检查网络')),patch.object(w.toast,'notify') as toast:w.auto_check_update();self.wait()
        toast.assert_not_called();self.assertIn('检查失败',w.update_info.text());self.assertFalse(w.update_notice.isVisibleTo(w))
    def test_skipped_version_stays_quiet_until_checked_by_hand(self):
        w=self.window;offer=updater.validate(manifest())
        with patch.object(app_live,'choose',return_value=0):w.found_update(offer,manual=True)
        self.assertEqual(self.prefs()['skippedVersion'],'9.1.0');self.assertFalse(w.update_notice.isVisibleTo(w))
        w.found_update(offer,manual=False);self.assertFalse(w.update_notice.isVisibleTo(w))
        with patch.object(app_live,'choose',return_value=None) as asked:w.found_update(offer,manual=True)
        asked.assert_called_once();self.assertTrue(w.update_notice.isVisibleTo(w))
    def test_manual_check_when_current(self):
        w=self.window
        with patch.object(app_live.updater,'check',return_value=None),patch.object(w.toast,'notify') as toast:w.check_update(manual=True);self.wait()
        self.assertEqual(toast.call_args.args[0],'已是最新版本');self.assertFalse(w.update_btn.loading())
    def test_old_offer_is_ignored(self):
        w=self.window;w.found_update(updater.validate(manifest(version=app_live.APP_VERSION)),manual=False)
        self.assertIsNone(w.update_offer);self.assertFalse(w.update_notice.isVisibleTo(w))
    def test_install_runs_only_after_both_answers_and_a_clean_close(self):
        w=self.window;offer=updater.validate(manifest());path=Path(self.temp.name)/'updates'/'ae-localizer-setup-9.1.0.exe'
        with patch.object(app_live,'choose',return_value=1),patch.object(w,'work') as work:w.offer_update(offer)
        work.assert_called_once()
        with patch.object(app_live,'confirm',return_value=False),patch.object(w,'close') as close:w.downloaded(offer,path)
        close.assert_not_called();self.assertIsNone(w.pending_installer)
        with patch.object(app_live,'confirm',return_value=True),patch.object(w,'close') as close:w.downloaded(offer,path)
        close.assert_called_once();self.assertEqual(w.pending_installer,path)
        w.error('部分原名未恢复');self.assertIsNone(w.pending_installer)
        w.pending_installer=path
        with patch.object(app_live.os,'startfile',create=True) as start:w.close();self.app.processEvents()
        start.assert_called_once_with(str(path));self.assertIsNone(w.pending_installer)
    def test_settings_page_fits_the_smallest_window(self):
        w=self.window;w.resize(1200,720);w.show();self.addCleanup(w.hide);w.go(app_live.SETTINGS);w.pages.setCurrentIndex(app_live.SETTINGS)
        for _ in range(5):self.app.processEvents()
        # The page scrolls on a short window: its content keeps at least its minimum height, nothing is squeezed.
        area=w.pages.widget(app_live.SETTINGS);self.assertIsInstance(area,app_live.QScrollArea);page=area.widget()
        self.assertLessEqual(page.minimumSizeHint().height(),page.height())
        for frame in w.settings_frames:self.assertGreaterEqual(frame.height(),frame.layout().totalHeightForWidth(frame.width()))
    def test_download_runs_through_the_updater(self):
        w=self.window;offer=updater.validate(manifest());folder=Path(self.temp.name)/'updates';folder.mkdir();(folder/'ae-localizer-setup-0.1.0.exe').write_bytes(b'old')
        with patch.object(app_live,'choose',return_value=1),patch.object(app_live.updater,'download',return_value=folder/'x.exe') as download,\
             patch.object(w,'work',lambda work,done:done(work(lambda text:None))),patch.object(w,'downloaded') as downloaded:w.offer_update(offer)
        self.assertEqual(download.call_args.args[:2],(offer,folder));downloaded.assert_called_once_with(offer,folder/'x.exe')
        self.assertEqual(list(folder.iterdir()),[])

class UnconfiguredTests(GuiCase):
    """Until a release repository is set, nothing goes online and the settings card says so."""
    def setUp(self):
        handle=patch.object(updater,'UPDATE_REPO','');handle.start();self.addCleanup(handle.stop);super().setUp()
    def test_nothing_goes_online(self):
        w=self.window;self.assertFalse(updater.configured())
        self.assertFalse(w.update_btn.isEnabled());self.assertFalse(w.update_box.isEnabled());self.assertIn('尚未设置',w.update_info.text())
        with patch.object(app_live.updater,'urlopen',side_effect=AssertionError('online')):
            w.auto_check_update();w.check_update(manual=True)
            with self.assertRaisesRegex(UpdateError,'尚未设置'):updater.check('0.8.0')
        self.assertIsNone(w.update_job)
    def test_repository_names(self):
        for good in ('owner/repo','a-b/c.d_e'):
            with patch.object(updater,'UPDATE_REPO',good):self.assertTrue(updater.configured())
        for bad in ('','owner','owner/repo/x','https://github.com/o/r','o/r?x'):
            with patch.object(updater,'UPDATE_REPO',bad):self.assertFalse(updater.configured())

if __name__=='__main__':unittest.main()
