"""Explainable edge-bar and temporal mirror candidates; NumPy/CuPy compatible."""
from collections import deque
import numpy as np


class SpatialChecks:
    def __init__(self,cfg,xp,emit):
        self.cfg=cfg;self.xp=xp;self.emit=emit
        self.bars={};self.history=deque(maxlen=480);self.segment=None;self.history_time=-1.
        self.previous=None;self.previous_t=0.;self.previous_i=0

    def borders(self,y,t,i):
        p,xp=self.cfg,self.xp
        if not p.borders:return
        dark=y<=p.border_level
        rows=dark.mean(axis=1)>=p.border_ratio/100
        cols=dark.mean(axis=0)>=p.border_ratio/100
        def prefix(a):return int(xp.cumprod(a.astype(xp.int32)).sum())
        widths=[prefix(rows),prefix(rows[::-1]),prefix(cols),prefix(cols[::-1])]
        # Full black frames have no visible content boundary.
        content=bool(float((~dark).mean())>.05)
        for side,width,dimension in zip(('上','下','左','右'),widths,(y.shape[0],y.shape[0],y.shape[1],y.shape[1])):
            percent=width/dimension*100
            active=content and p.border_percent<=percent<45
            if active:
                if side not in self.bars:self.bars[side]=[t,i,percent,percent]
                else:
                    self.bars[side][2]=min(self.bars[side][2],percent);self.bars[side][3]=max(self.bars[side][3],percent)
            else:self.close_bar(side,t,i-1)

    def close_bar(self,side,end,last):
        value=self.bars.pop(side,None)
        if value and last-value[1]+1>=self.cfg.border_frames:
            start,first,lo,hi=value
            self.emit('黑边',start,end,first,last,f'{side}边：画面尺寸的 {lo:.2f}–{hi:.2f}%；亮度 ≤ {self.cfg.border_level:g}，行/列暗像素 ≥ {self.cfg.border_ratio:g}%；暗景可能误报')

    def mirror(self,y,t,i):
        p,xp=self.cfg,self.xp
        if not p.mirror:return
        # Fixed-cost thumbnail, GPU resident when CUDA analysis is enabled.
        sy=max(1,y.shape[0]//64);sx=max(1,y.shape[1]//96)
        thumb=y[::sy,::sx].copy()
        while self.history and t-self.history[0][0]>p.mirror_window:self.history.popleft()
        if self.previous is not None and self.previous.shape!=thumb.shape:
            self.close_mirror(t,i-1,False);self.history.clear();self.previous=None
        if self.previous is not None:
            normal=float(xp.abs(thumb-self.previous).mean())
            found=None
            for axis,label in ((1,'水平'),(0,'垂直')):
                mirrored=xp.flip(thumb,axis=axis)
                diff=float(xp.abs(mirrored-self.previous).mean())
                asymmetry=float(xp.abs(self.previous-xp.flip(self.previous,axis=axis)).mean())
                if diff<=p.mirror_delta and asymmetry>=max(8,p.mirror_delta*1.5) and normal-diff>=6 and diff<=normal*(1-p.mirror_gain/100):
                    if found is None or diff<found[0]:found=(diff,axis,label)
            if not self.segment and found is None and normal>8 and self.history:
                refs=xp.stack([v for _,v in self.history])
                direct=xp.abs(refs-thumb).mean(axis=(1,2))
                for axis,label in ((1,'水平'),(0,'垂直')):
                    reverse=xp.abs(refs-xp.flip(thumb,axis=axis)).mean(axis=(1,2))
                    valid=(reverse<=p.mirror_delta)&(direct-reverse>=8)&(reverse<=direct*(1-p.mirror_gain/100))
                    score=float(xp.where(valid,reverse,xp.inf).min())
                    if score<float('inf') and (found is None or score<found[0]):found=(score,axis,label)
            if self.segment:
                seg=self.segment
                # A matching opposite-direction transition closes the candidate.
                if found and found[1]==seg['axis']:
                    self.close_mirror(t,i-1,True);found=None
                elif normal>max(35,p.mirror_delta*3):
                    # New unrelated shot: stop rather than mark the remainder.
                    self.close_mirror(t,i-1,False)
                else:
                    seg['last']=i
                    # Brief normal reference history can confirm small mirrored
                    # clips even while motion continues within the shot.
                    if self.history:
                        refs=xp.stack([v for _,v in self.history if v.shape==thumb.shape])
                        diffs=xp.abs(refs-xp.flip(thumb,axis=seg['axis'])).mean(axis=(1,2))
                        reversed_best=float(diffs.min());direct_best=float(xp.abs(refs-thumb).mean(axis=(1,2)).min())
                        if direct_best<=p.mirror_delta and direct_best+8<reversed_best:self.close_mirror(t,i-1,True)
                        else:seg['best']=min(seg['best'],reversed_best)
            elif found:
                self.segment=dict(start=t,first=i,last=i,axis=found[1],label=found[2],best=found[0])
            if self.segment is None and t-self.history_time>=.25:self.history.append((t,thumb));self.history_time=t
        else:self.history.append((t,thumb));self.history_time=t
        self.previous=thumb;self.previous_t=t;self.previous_i=i

    def close_mirror(self,end,last,returned):
        seg=self.segment;self.segment=None
        if seg and last-seg['first']+1>=self.cfg.mirror_frames:
            evidence='检测到镜像切入和切回' if returned else '检测到镜像切入，片段终点按下一次切镜或片尾估计'
            self.emit('疑似镜像',seg['start'],end,seg['first'],last,
                f'{seg["label"]}翻转匹配差 {seg["best"]:.2f}/255；{evidence}；需人工复核')

    def finish(self,end,last):
        for side in list(self.bars):self.close_bar(side,end,last)
        self.close_mirror(end,last,False)
