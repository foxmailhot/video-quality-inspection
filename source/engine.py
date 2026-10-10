"""FrameScope local QC. Decodes every frame; GPU metrics are optional, explicit and tested."""
from __future__ import annotations
import os, sys, time, json, math
from pathlib import Path
from dataclasses import dataclass, asdict
from collections import deque
import numpy as np
import av
from spatial_checks import SpatialChecks

@dataclass
class Settings:
    black_level: float = 20
    black_ratio: float = 98
    black_frames: int = 1
    freeze_seconds: float = 1.0
    freeze_delta: float = 0.7
    flash_delta: float = 35
    return_delta: float = 12
    flash_frames: int = 3
    block_ratio: float = 4.0
    noise_level: float = 48
    color_ratio: float = 65
    width: int = 960
    crop: float = 0
    gpu: bool = True
    black: bool = True
    freeze: bool = True
    flash: bool = True
    artifacts: bool = True
    borders: bool = True
    border_level: float = 16
    border_ratio: float = 99
    border_percent: float = .5
    border_frames: int = 3
    mirror: bool = True
    mirror_delta: float = 10
    mirror_gain: float = 55
    mirror_frames: int = 1
    mirror_window: float = 30

@dataclass
class Issue:
    kind: str
    start: float
    end: float
    frame: int
    last_frame: int
    detail: str
    review: str = '待复核'

def init_gpu(enabled=True):
    if not enabled:
        return np, 'CPU（手动选择）'
    try:
        import cupy as cp
        # Select the NVIDIA device with the largest total memory.
        devices = [(cp.cuda.runtime.getDeviceProperties(i)['totalGlobalMem'], i)
                   for i in range(cp.cuda.runtime.getDeviceCount())]
        device = max(devices)[1]
        cp.cuda.Device(device).use()
        assert float(cp.sum(cp.arange(16, dtype=cp.float32)).get()) == 120
        name = cp.cuda.runtime.getDeviceProperties(device)['name']
        if isinstance(name, bytes): name = name.decode()
        return cp, f'CUDA 分析 · {name}'
    except Exception as exc:
        return np, f'CPU 回退 · CUDA 不可用：{str(exc)}'

class Detector:
    def __init__(self, cfg, xp=np):
        self.cfg, self.xp = cfg, xp
        self.issues = []
        self.open_runs = {}
        self.prev = None
        self.anchor = None
        self.freeze_start = None
        self.pending = None
        self.last_t, self.last_i = 0., -1
        self.recent_edges = deque(maxlen=48)
        self.recent_seams = deque(maxlen=48)
        self.spatial=SpatialChecks(cfg,xp,lambda *args:self.issues.append(Issue(*args)))

    def close(self, key, end, last):
        run = self.open_runs.pop(key, None)
        if run:
            start, idx, detail = run
            if key != '黑帧' or last - idx + 1 >= self.cfg.black_frames:
                self.issues.append(Issue(key, start, max(start, end), idx, last, detail))

    def run(self, key, active, t, i, detail):
        if active:
            self.open_runs.setdefault(key, (t, i, detail))
        else:
            self.close(key, t, i-1)

    def add_frame(self, rgb, t, i, border_rgb=None):
        p, xp = self.cfg, self.xp
        x = xp.asarray(rgb, dtype=xp.float32)
        y = x[:,:,0] * .2126 + x[:,:,1] * .7152 + x[:,:,2] * .0722
        by=y
        if p.borders and border_rgb is not None:
            original=xp.asarray(border_rgb,dtype=xp.float32)
            by=original[:,:,0]*.2126+original[:,:,1]*.7152+original[:,:,2]*.0722
        self.spatial.borders(by,t,i);self.spatial.mirror(y,t,i)
        dx, dy = xp.abs(y[:,1:] - y[:,:-1]), xp.abs(y[1:] - y[:-1])
        edge = (dx.mean() + dy.mean()) * .5
        # Search block phases because resize and crop shift the macroblock grid.
        phase = [dx[:,k::8].mean() for k in range(8) if dx[:,k::8].size]
        phase += [dy[k::8,:].mean() for k in range(8) if dy[k::8,:].size]
        block = xp.max(xp.stack(phase)) / (edge + 1)
        seam = xp.max(dy.mean(axis=1)) / (edge + 1)
        r,g,b = x[:,:,0], x[:,:,1], x[:,:,2]
        odd_color = ((g > 110) & (g > r*1.7+20) & (g > b*1.7+20)) | ((r>110)&(b>110)&(g<60))
        delta = xp.abs(y-self.prev).mean() if self.prev is not None else xp.asarray(0.)
        anchor_delta = xp.abs(y-self.anchor).mean() if self.anchor is not None else xp.asarray(0.)
        values = xp.stack([(y <= p.black_level).mean()*100, edge, block, seam,
                           odd_color.mean()*100, delta, anchor_delta])
        vals = values.get() if xp is not np else values
        black, edge_v, block_v, seam_v, color_v, diff, anchor_diff = map(float, vals)
        self.run('黑帧', p.black and black >= p.black_ratio, t, i,
                 f'暗像素 {black:.1f}%；亮度阈值 {p.black_level:g}/255')
        if self.anchor is None:
            self.anchor, self.freeze_start = y.copy(), (t,i)
        elif anchor_diff > p.freeze_delta:
            st, fi = self.freeze_start
            if p.freeze and t-st >= p.freeze_seconds:
                self.issues.append(Issue('疑似定帧',st,t,fi,i-1,f'与起始画面平均差 ≤ {p.freeze_delta:g}/255；静止镜头也可能触发'))
            self.anchor, self.freeze_start = y.copy(), (t,i)
        if p.flash and self.prev is not None:
            if self.pending:
                base, st, fi = self.pending
                back = float(xp.abs(y-base).mean())
                if back <= p.return_delta:
                    self.issues.append(Issue('疑似夹帧',st,t,fi,i-1,f'短暂突变后返回相似画面；{i-fi} 帧；返回差 {back:.2f}'))
                    self.pending = None
                elif i-fi >= p.flash_frames:
                    self.pending = None
            if self.pending is None and diff >= p.flash_delta:
                self.pending = (self.prev.copy(),t,i)
        baseline = float(np.median(self.recent_edges)) if len(self.recent_edges)>=5 else edge_v
        noisy = edge_v > p.noise_level and edge_v > max(1,baseline)*1.8
        blocked = block_v >= p.block_ratio and edge_v > 3
        colored = color_v >= p.color_ratio
        seam_baseline = float(np.median(self.recent_seams)) if len(self.recent_seams)>=5 else seam_v
        torn = seam_v >= p.block_ratio*4 and seam_v > max(1,seam_baseline)*2.5 and diff > p.flash_delta
        active = p.artifacts and (noisy or blocked or colored or torn)
        reasons = []
        if noisy: reasons.append(f'高频噪声 {edge_v:.1f}')
        if blocked: reasons.append(f'周期块边界 {block_v:.1f}')
        if colored: reasons.append(f'异常绿/紫色占比 {color_v:.1f}%')
        if torn: reasons.append(f'横向断层 {seam_v:.1f}')
        self.run('疑似花屏',active,t,i,'；'.join(reasons)+'；需人工复核')
        self.recent_edges.append(edge_v)
        self.recent_seams.append(seam_v)
        self.prev, self.last_t, self.last_i = y, t, i

    def finish(self,end):
        self.spatial.finish(end,self.last_i)
        for key in list(self.open_runs): self.close(key,end,self.last_i)
        if self.cfg.freeze and self.freeze_start:
            st, fi = self.freeze_start
            if end-st >= self.cfg.freeze_seconds:
                self.issues.append(Issue('疑似定帧',st,end,fi,self.last_i,'低变化画面持续至片尾；静止镜头也可能触发'))
        return sorted(self.issues,key=lambda v:(v.start,v.kind))

class Cancelled(Exception): pass

def analyze(path, cfg, progress=lambda *args: None, cancelled=lambda:False):
    started = time.monotonic()
    xp, backend = init_gpu(cfg.gpu)
    gpu_warning = backend if cfg.gpu and xp is np else None
    if gpu_warning: backend = 'CPU 分析（CUDA 不可用，详细原因见报告）'
    progress(0, '正在打开视频 · '+backend, [])
    container = None
    decode_backend = 'CPU 解码'
    warnings = [gpu_warning] if gpu_warning else []
    if cfg.gpu and xp is not np:
        try:
            from av.codec.hwaccel import HWAccel
            container = av.open(path, hwaccel=HWAccel('cuda', device=str(xp.cuda.Device().id), allow_software_fallback=False))
            stream = container.streams.video[0]
            it = container.decode(stream)
            first = next(it)
            decode_backend = 'NVDEC 硬件解码'
        except Exception as exc:
            if container: container.close()
            container = None
            warnings.append('硬件解码不支持此文件，已使用 CPU 解码：'+str(exc)[:180])
    if container is None:
        container = av.open(path)
        if not container.streams.video:
            container.close()
            raise ValueError('文件中没有视频轨道')
        stream = container.streams.video[0]
        it = container.decode(stream)
        try: first = next(it)
        except Exception:
            container.close()
            raise ValueError('无法解码首帧，文件可能损坏或编码不受支持')
    try:
        fps = float(stream.average_rate or stream.guessed_rate or 25)
        dur = float(stream.duration*stream.time_base) if stream.duration else float(container.duration or 0)/av.time_base
        origin = float(stream.start_time*stream.time_base) if stream.start_time is not None else float(first.time or 0)
        det = Detector(cfg,xp)
        info = dict(width=first.width,height=first.height,fps=fps,duration=dur,codec=stream.codec_context.name,
                    pixel_format=first.format.name,backend=backend,decode_backend=decode_backend)
        if any(component.bits > 8 for component in first.format.components):
            warnings.append('高位深画面转为 8 位 RGB 分析；HDR 素材需调整黑位阈值，并用专业播放器复核色彩。')
        frame, count, last_emit, previous_t = first, 0, 0., -1.
        last_duration = 1/fps
        while frame is not None:
            if cancelled(): raise Cancelled()
            t = max(0.,float(frame.time)-origin) if frame.time is not None else count/fps
            if frame.time is None and not any('缺少时间戳' in w for w in warnings): warnings.append('部分帧缺少时间戳，相关位置按平均帧率估算。')
            if t < previous_t:
                warnings.append(f'帧 {count} 时间戳倒退，位置可能不准确')
            previous_t = t
            last_duration = float(frame.duration*frame.time_base) if frame.duration and frame.time_base else 1/fps
            width = min(cfg.width or frame.width,frame.width)
            height = max(16,round(frame.height*width/frame.width))
            rgb = frame.reformat(width=width,height=height,format='rgb24').to_ndarray()
            rotation=getattr(frame,'rotation',0)
            if rotation:rgb=np.rot90(rgb,k=round(rotation/90)).copy()
            border_rgb=rgb if cfg.crop else None
            if cfg.crop:
                height,width=rgb.shape[:2]
                cy,cx = int(height*cfg.crop/100),int(width*cfg.crop/100)
                rgb = rgb[cy:height-cy,cx:width-cx]
            det.add_frame(rgb,t,count,border_rgb=border_rgb)
            if getattr(frame,'is_corrupt',False):
                det.issues.append(Issue('解码损坏',t,t+1/fps,count,count,'解码器标记该帧损坏'))
            count += 1
            if time.monotonic()-last_emit > .2:
                elapsed = time.monotonic()-started
                percent = min(99,int(t/dur*100)) if dur>0 else -1
                progress(percent,f'{backend} / {decode_backend} · {count:,} 帧 · {count/max(elapsed,.01):.0f} 帧/秒', [asdict(v) for v in det.issues])
                last_emit = time.monotonic()
            try:
                frame = next(it,None)
            except av.error.FFmpegError as exc:
                # A decoder failure is never reported as successful completion.
                raise RuntimeError(f'解码在 {t:.3f} 秒失败，检测未完成：{exc}') from exc
        end = det.last_t + last_duration
        if dur and end < dur-max(.5,3/fps):
            warnings.append(f'实际解码结束于 {end:.3f}s，短于声明时长 {dur:.3f}s；请核查截断或时间戳。')
        if stream.frames and count < stream.frames:
            warnings.append(f'解码 {count} 帧，容器声明 {stream.frames} 帧；可能有丢帧或损坏。')
        info['duration'] = max(dur,end)
        result = dict(version='2.0',path=str(Path(path).resolve()),settings=asdict(cfg),info=info,
                      issues=[asdict(v) for v in det.finish(end)],warnings=warnings,frames=count,
                      elapsed=round(time.monotonic()-started,2),complete=not any('短于' in w or '丢帧' in w for w in warnings))
        progress(100, f'完成 · {count:,} 帧 · {len(result["issues"])} 个标记',result['issues'])
        return result
    finally:
        container.close()

def save_json(path, data):
    dest = Path(path)
    dest.parent.mkdir(parents=True,exist_ok=True)
    temp = dest.with_suffix(dest.suffix+'.tmp')
    temp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    os.replace(temp,dest)
