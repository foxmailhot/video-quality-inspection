import sys,copy
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'source'))
import updater as u
from urllib.error import HTTPError
repo=dict(id=u.REPOSITORY_ID,full_name='foxmailhot/video-quality-inspection',url='https://api.github.com/repos/foxmailhot/video-quality-inspection',owner=dict(login='foxmailhot'))
prefix='https://github.com/'+repo['full_name']+'/releases/download/v2.1.0/'
m=dict(product='FrameScope',platform='windows-x64',version='2.1.0',size=20,sha256='a'*64,parts=[dict(url=prefix+'FrameScope-2.1.0-Setup-x64.exe',size=20,sha256='a'*64)])
r=dict(tag_name='v2.1.0',draft=False,prerelease=False,assets=[dict(name='latest.json',state='uploaded',browser_download_url=prefix+'latest.json'),dict(name='FrameScope-2.1.0-Setup-x64.exe',state='uploaded',browser_download_url=m['parts'][0]['url'],size=20,digest='sha256:'+'a'*64)])
calls=[]
def fake(url,limit=65536):
 calls.append(url)
 if url==u.REPO_API:return repo
 if url.endswith('/releases/latest'):return r
 if url==prefix+'latest.json':return m
 raise AssertionError(url)
u.read_json=fake
assert u.fetch_latest()==m and len(calls)==3
r['tag_name']='v2.0.0';calls.clear();assert u.fetch_latest()==dict(version='v2.0.0') and len(calls)==2
r['tag_name']='v2.1.0';r['prerelease']=True
try:u.fetch_latest();raise AssertionError('prerelease')
except ValueError:pass
r['prerelease']=False;r['assets'][1]['digest']='sha256:'+'b'*64
try:u.fetch_latest();raise AssertionError('digest')
except ValueError:pass
r['assets'][1]['digest']='sha256:'+'a'*64;repo['id']=1
try:u.fetch_latest();raise AssertionError('repository')
except ValueError:pass
print('GitHub repository rename, latest-release lookup, same-version no download, prerelease and digest identity checks passed')
