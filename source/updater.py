"""GitHub-hosted, opt-in updates. No credentials or video data leave the app."""
import hashlib,json,re,os,subprocess
from pathlib import Path
from urllib.request import Request,urlopen,build_opener,HTTPRedirectHandler
from urllib.parse import urlparse
from PySide6.QtCore import QObject,QThread,Signal,Slot
from PySide6.QtWidgets import QMessageBox,QProgressDialog
VERSION='2.0.0'
REPOSITORY='https://github.com/foxmailhot/video-quality-inspection'
REPOSITORY_ID=1412709349
REPO_API='https://api.github.com/repositories/'+str(REPOSITORY_ID)
MANIFEST_URL=REPOSITORY+'/releases/latest/download/latest.json'
LIMIT=2*1024**3

def version_tuple(value):
    if not isinstance(value,str) or not re.fullmatch(r'v?\d+\.\d+\.\d+',value):raise ValueError('版本号须为正式版本，例如 2.0.0')
    return tuple(int(n) for n in value.lstrip('v').split('.'))

def safe_url(url):
    p=urlparse(url)
    if p.scheme!='https' or not p.hostname or p.username or p.password or p.fragment:raise ValueError('更新地址必须是 HTTPS')
    return url

class Redirects(HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        safe_url(newurl)
        return super().redirect_request(req,fp,code,msg,headers,newurl)

def open_url(url):
    return build_opener(Redirects()).open(Request(safe_url(url),headers={'User-Agent':'FrameScope/'+VERSION,'Cache-Control':'no-cache'}),timeout=10)

def validate_manifest(data):
    if data.get('product')!='FrameScope' or data.get('platform')!='windows-x64':raise ValueError('更新包产品或平台不匹配')
    version_tuple(data.get('version'))
    size=data.get('size');parts=data.get('parts')
    if type(size) is not int or not 0<size<=LIMIT:raise ValueError('安装包大小无效')
    if not isinstance(parts,list) or not 1<=len(parts)<=32:raise ValueError('更新下载列表无效')
    if not re.fullmatch('[0-9a-fA-F]{64}',str(data.get('sha256',''))):raise ValueError('缺少安装包 SHA256 校验值')
    for part in parts:
        safe_url(part.get('url',''))
        # Initial URLs must point to this repository; HTTPS CDN redirects are supported.
        p=urlparse(part['url'])
        if p.hostname!='github.com' or not p.path.startswith('/foxmailhot/') or '/releases/download/' not in p.path:raise ValueError('下载地址不属于指定 GitHub 发行版')
        if type(part.get('size')) is not int or part['size']<=0:raise ValueError('下载分片大小无效')
        if not re.fullmatch('[0-9a-fA-F]{64}',str(part.get('sha256',''))):raise ValueError('下载分片缺少校验值')
    if sum(p['size'] for p in parts)!=size:raise ValueError('分片总大小不一致')
    return data

def sha_file(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()

def download(manifest,folder,progress=lambda n:None,cancel=lambda:False,opener=open_url):
    validate_manifest(manifest);folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    final=folder/f'FrameScope-{manifest["version"].lstrip("v")}-Setup-x64.exe'
    if final.exists() and final.stat().st_size==manifest['size'] and sha_file(final)==manifest['sha256'].lower():return final
    partial=final.with_suffix('.partial');total=0;whole=hashlib.sha256()
    try:
        with partial.open('wb') as out:
            for part in manifest['parts']:
                h=hashlib.sha256();length=0
                if cancel():raise InterruptedError('已取消下载')
                with opener(part['url']) as response:
                    while True:
                        if cancel():raise InterruptedError('已取消下载')
                        chunk=response.read(256*1024)
                        if not chunk:break
                        length+=len(chunk);total+=len(chunk)
                        if length>part['size'] or total>manifest['size']:raise ValueError('下载大小超过更新记录')
                        out.write(chunk);h.update(chunk);whole.update(chunk);progress(int(total/manifest['size']*100))
                if length!=part['size'] or h.hexdigest()!=part['sha256'].lower():raise ValueError('下载分片校验失败，请重试')
        if total!=manifest['size'] or whole.hexdigest()!=manifest['sha256'].lower():raise ValueError('安装包完整性校验失败')
        with partial.open('rb') as f:
            if f.read(2)!=b'MZ':raise ValueError('下载内容不是 Windows 安装程序')
        if cancel():raise InterruptedError('已取消下载')
        partial.replace(final);return final
    finally:
        if partial.exists():partial.unlink()

def read_json(url,limit=65536):
    with open_url(url) as r:raw=r.read(limit+1)
    if len(raw)>limit:raise ValueError('版本记录过大')
    return json.loads(raw)

def fetch_latest():
    # Numeric repository identity survives renaming; never follow a reused name.
    repo=read_json(REPO_API)
    if repo.get('id')!=REPOSITORY_ID or repo.get('owner',{}).get('login','').lower()!='foxmailhot':raise ValueError('更新仓库身份不匹配')
    api=repo.get('url','')
    if api!='https://api.github.com/repos/'+repo['full_name']:raise ValueError('仓库 API 地址无效')
    release=read_json(api+'/releases/latest',512*1024)
    if release.get('draft') or release.get('prerelease'):raise ValueError('不是正式发行版')
    tag=release.get('tag_name','');version_tuple(tag)
    # Equal/older versions need no manifest download and no user prompt.
    if version_tuple(tag)<=version_tuple(VERSION):return {'version':tag}
    assets=release.get('assets',[])
    records=[a for a in assets if a.get('name')=='latest.json' and a.get('state')=='uploaded']
    if len(records)!=1:raise ValueError('新版缺少更新记录附件')
    prefix='https://github.com/'+repo['full_name']+'/releases/download/'+tag+'/'
    if not records[0].get('browser_download_url','').startswith(prefix):raise ValueError('更新记录地址无效')
    data=validate_manifest(read_json(records[0]['browser_download_url']))
    if version_tuple(data['version'])!=version_tuple(tag):raise ValueError('更新记录版本与发行版不一致')
    available={a.get('browser_download_url'):a for a in assets if a.get('state')=='uploaded'}
    for part in data['parts']:
        if not part['url'].startswith(prefix) or part['url'] not in available or available[part['url']].get('size')!=part['size']:raise ValueError('安装包附件与更新记录不一致')
        digest=available[part['url']].get('digest')
        if digest and digest!='sha256:'+part['sha256'].lower():raise ValueError('GitHub 附件校验值不一致')
    return data

class UpdateWorker(QThread):
    success=Signal(object);failure=Signal(str);progress=Signal(int)
    def __init__(self,manifest=None,folder=None):super().__init__();self.manifest=manifest;self.folder=folder
    def run(self):
        try:
            if self.manifest is None:
                result=fetch_latest()
            else:result=download(self.manifest,self.folder,self.progress.emit,self.isInterruptionRequested)
            if not self.isInterruptionRequested():self.success.emit(result)
        except InterruptedError:pass
        except Exception as exc:
            if not self.isInterruptionRequested():self.failure.emit(str(exc))

class Updater(QObject):
    def __init__(self,window):
        super().__init__(window);self.window=window;self.worker=None;self.ready=None;self.manifest=None;self.manual=False;self.dialog=None;self.install_on_close=False
    def check(self,manual=False):
        if self.worker:return
        if self.ready:
            if manual:self.offer_install()
            return
        self.manual=manual
        if manual:self.window.status.setText('正在检查更新…')
        self.worker=UpdateWorker();self.worker.success.connect(self.checked);self.worker.failure.connect(self.failed);self.worker.finished.connect(self.finished);self.worker.start()
    @Slot(object)
    def checked(self,data):
        if self.window.close_requested:return
        if version_tuple(data['version'])<=version_tuple(VERSION):
            if self.manual:QMessageBox.information(self.window,'检查更新','当前已是最新版本 '+VERSION)
            return
        self.manifest=data
        answer=QMessageBox.question(self.window,'发现新版本',f"当前 {VERSION} → 新版 {data['version']}\n下载大小 {data['size']/1024**2:.1f} MB\n\n{str(data.get('notes',''))[:2000]}\n\n是否下载更新？",QMessageBox.Yes|QMessageBox.No,QMessageBox.No)
        if answer==QMessageBox.Yes:
            # Queue after the checking thread's finished signal clears ownership.
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0,self.begin_download)
    def begin_download(self):
        if self.worker:
            from PySide6.QtCore import QTimer
            QTimer.singleShot(30,self.begin_download);return
        if self.window.close_requested:return
        self.dialog=QProgressDialog('正在下载更新，完成后将校验安装包…','取消',0,100,self.window);self.dialog.setMinimumDuration(0)
        self.worker=UpdateWorker(self.manifest,self.window.data_dir/'updates');self.worker.progress.connect(self.dialog.setValue);self.dialog.canceled.connect(self.worker.requestInterruption)
        self.worker.success.connect(self.downloaded);self.worker.failure.connect(self.failed);self.worker.finished.connect(self.finished);self.worker.start()
    @Slot(object)
    def downloaded(self,path):
        self.ready=Path(path)
        if self.dialog:self.dialog.close()
        if not self.window.close_requested:self.offer_install()
    def offer_install(self):
        reply=QMessageBox.question(self.window,'更新已准备好','下载与校验已完成。是否保存设置、关闭软件并启动覆盖升级？\n正在运行的检测会先停止。选择“否”可稍后点击“更新”。',QMessageBox.Yes|QMessageBox.No,QMessageBox.No)
        if reply==QMessageBox.Yes:self.install_on_close=True;self.window.close()
    @Slot(str)
    def failed(self,error):
        if self.dialog:self.dialog.close()
        if self.window.close_requested:return
        if self.manual or self.manifest:QMessageBox.warning(self.window,'更新暂不可用','无法完成更新，不影响视频检查。\n'+error)
        else:self.window.status.setText('更新检查暂不可用，可稍后点击“更新”重试')
    @Slot()
    def finished(self):
        worker=self.sender()
        if self.worker is worker:self.worker=None
        worker.deleteLater()
        if self.dialog:self.dialog.close();self.dialog=None
    def stop(self):
        if self.worker and self.worker.isRunning():self.worker.requestInterruption();return False
        return True
    def launch_if_requested(self):
        if not self.install_on_close:return
        if not self.ready or not self.manifest or sha_file(self.ready)!=self.manifest['sha256'].lower():raise ValueError('安装包已发生变化，请重新下载')
        subprocess.Popen([str(self.ready),'/NORESTART'],close_fds=True)
        self.install_on_close=False
