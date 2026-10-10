from PyInstaller.utils.hooks import collect_all, copy_metadata
from pathlib import Path
import site
import os

datas=[]
binaries=[]
hidden=['graphlib']
for package in ('cupy','cupy_backends','cuda.pathfinder'):
    d,b,h=collect_all(package)
    datas+=d; binaries+=b; hidden+=h
for name in ('cupy-cuda12x','cuda-pathfinder','nvidia-cuda-runtime-cu12','nvidia-cuda-nvrtc-cu12'):
    datas+=copy_metadata(name)
for folder in site.getsitepackages():
    root=Path(folder)/'nvidia'
    if root.is_dir():
        for path in root.rglob('*'):
            if path.is_file() and '__pycache__' not in path.parts:
                dest=str(path.parent.relative_to(Path(folder)))
                if path.suffix.lower()=='.dll':binaries.append((str(path),dest))
                else:datas.append((str(path),dest))
a=Analysis([str(Path(SPECPATH)/'app.py')],pathex=[SPECPATH],binaries=binaries,datas=datas,
           hiddenimports=hidden,excludes=['tkinter','matplotlib','scipy','IPython','pytest'],noarchive=False)
# Use Windows' ICU API instead of an unrelated DLL found in the build host PATH.
# BLAS/FFT/sparse solver libraries are not used by the pixel metric kernels.
unused=('cublas','cufft','cusolver','cusparse','curand','nvjitlink','icudt78','icuuc.dll')
a.binaries=[v for v in a.binaries if not (Path(v[0]).suffix.lower()=='.dll' and Path(v[0]).name.lower().startswith(unused))]
pyz=PYZ(a.pure)
exe=EXE(pyz,a.scripts,[],exclude_binaries=True,name='FrameScope',debug=False,strip=False,upx=False,console=os.environ.get('FRAMESCOPE_CONSOLE')=='1',version=str(Path(SPECPATH)/'version_info.txt'))
coll=COLLECT(exe,a.binaries,a.datas,strip=False,upx=False,name='FrameScope')
