"""Three-frame RGB and local-tile temporal outliers, on CPU or CUDA."""
from collections import deque
import numpy as np

class SingleFrameCheck:
    def __init__(self,cfg,xp):
        self.cfg,self.xp=cfg,xp;self.frames=deque(maxlen=5)
    def add(self,rgb,t,index):
        xp=self.xp;p=self.cfg
        # Bounded working set; preserve color differences lost by luma-only checks.
        step=max(1,(rgb.shape[1]+191)//192)
        self.frames.append((rgb[::step,::step].copy(),t,index))
        if len(self.frames)<3:return None
        (a,_,_), (b,bt,bi), (c,_,_)=list(self.frames)[-3:]
        if a.shape!=b.shape or b.shape!=c.shape:return None
        ab=xp.abs(a-b).mean(axis=2);bc=xp.abs(b-c).mean(axis=2);ac=xp.abs(a-c).mean(axis=2)
        jump=float(xp.minimum(ab.mean(),bc.mean()));back=float(ac.mean())
        ratio=p.flash_ratio
        global_hit=jump>=p.flash_delta and back<=p.return_delta and jump>=ratio*max(1.,back)
        # A localized insert need not alter the entire image. Require both jumps
        # in the same tiles and stable surrounding frames in those tiles.
        h,w=ab.shape;h=h//8*8;w=w//8*8
        local_hit=False;coverage=0.
        if h and w:
            tile=lambda d:d[:h,:w].reshape(8,h//8,8,w//8).mean(axis=(1,3))
            ja=xp.minimum(tile(ab),tile(bc));ret=tile(ac)
            votes=(ja>=p.flash_delta)&(ret<=p.return_delta)&(ja>=ratio*xp.maximum(ret,1.))
            coverage=float(votes.mean())
            local_hit=coverage>=p.flash_area/100
        if global_hit or local_hit:
            return bt,t,bi,bi,f'单帧时域异常：RGB 双向差 {jump:.2f}，邻帧返回差 {back:.2f}，局部异常覆盖 {coverage*100:.1f}%；需人工复核'
        if len(self.frames)==5:
            (left0,_,_), (left,_,_), (middle,mt,mi), (right,rt,_), (right0,_,_)=self.frames
            if not all(f.shape==middle.shape for f in (left0,left,right,right0)):return None
            incoming=float(xp.abs(middle-left).mean());outgoing=float(xp.abs(middle-right).mean())
            before=float(xp.abs(left-left0).mean());after=float(xp.abs(right-right0).mean())
            # Two cuts surrounding exactly one frame: unlike A-B-A, A and C
            # may be completely different shots. Require stable outer context.
            motion=max(1.,before,after);boundary=min(incoming,outgoing)
            if boundary>=p.flash_delta*1.2 and max(before,after)<=p.return_delta and boundary>=max(2.,ratio)*motion:
                return mt,rt,mi,mi,f'孤立单帧镜头：相邻切换差 {incoming:.2f}/{outgoing:.2f}，左右连续画面差 {before:.2f}/{after:.2f}；单帧镜头也可能是有意剪辑，需复核'
        return None

def flash_preset(index):
    return [dict(flash_delta=18,return_delta=20,flash_ratio=1.8,flash_area=6.25),
            dict(flash_delta=12,return_delta=28,flash_ratio=1.5,flash_area=3.125),
            dict(flash_delta=26,return_delta=16,flash_ratio=2.2,flash_area=9.375)][index]
