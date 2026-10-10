"""Decode adjacent presentation frames instead of approximating with average FPS."""
import math
import bisect
import threading
import os
from collections import deque
import av
from PySide6.QtCore import QThread, Signal, Qt, QRectF
from PySide6.QtGui import QImage, QPainter, QColor, QTransform
from PySide6.QtWidgets import QWidget
from monitor import InspectionCanvas


def adjacent_frame(path, position, direction, cancelled=lambda: False):
    """Return (timestamp relative to video start, VideoFrame).

    Seek backward to a keyframe, decode in presentation order and retain the
    current/previous frames. VFR and fractional rates use real PTS throughout.
    direction=0 returns the displayed frame at position (floor in PTS order).
    """
    with av.open(str(path)) as container:
        if not container.streams.video:
            raise ValueError('文件中没有视频轨道')
        stream = container.streams.video[0]
        first = next(container.decode(stream), None)
        if first is None or first.time is None:
            raise ValueError('视频没有可用的帧时间戳，无法精确逐帧定位')
        origin = float(stream.start_time * stream.time_base) if stream.start_time is not None else float(first.time)
        target = origin + max(0., position)
        if cancelled(): return None
        # The one-tick subtraction also handles stepping backward at a keyframe.
        offsets = [max(origin, target-float(stream.time_base)), max(origin,target-2), max(origin,target-30), origin]
        for offset in dict.fromkeys(offsets):
            container.seek(math.floor(offset/float(stream.time_base)),stream=stream,backward=True,any_frame=False)
            previous, current, following = None, None, None
            for frame in container.decode(stream):
                if cancelled(): return None
                if frame.time is None:
                    raise ValueError('遇到缺少时间戳的帧，无法保证逐帧定位')
                time = float(frame.time)
                if time <= target + 1e-7:
                    previous, current = current, (max(0.,time-origin),frame)
                else:
                    following = (max(0.,time-origin),frame)
                    break
            if direction > 0: return following or current or (0.,first)
            if direction == 0: return current or following or (0.,first)
            if previous is not None: return previous
            if offset <= origin: return current or (0.,first)
        return 0.,first


def to_image(frame):
    scale=min(1.,1920/max(frame.width,frame.height));width=max(1,round(frame.width*scale))
    rgb=frame.reformat(width=width,height=max(1,round(frame.height*width/frame.width)),format='rgb24').to_ndarray()
    image=QImage(rgb.data,rgb.shape[1],rgb.shape[0],rgb.strides[0],QImage.Format_RGB888).copy()
    rotation=getattr(frame,'rotation',0)
    if rotation:image=image.transformed(QTransform().rotate(-rotation))
    return image


class FrameSession:
    """One persistent demuxer and a contiguous, bounded decoded-image window.

    Only the background worker touches the demuxer. The UI may read cached
    implicitly-shared QImages under the lock without blocking on decoding.
    """
    def __init__(self,path):
        self.path=path;self.container=None;self.iterator=None;self.stream=None
        self.origin=0.;self.frames=[];self.bytes=0;self.eof=False;self.at_start=False
        self.lock=threading.RLock();self.opens=0;self.seeks=0;self.cache_hits=0
        self.budget=128*1024*1024;self.duration=0.
        self.pending_before=[];self.pending_after=[]
    def close(self):
        if self.container:self.container.close();self.container=None
        self.stream=None;self.eof=False;self.at_start=False
        self.iterator=None
        self.pending_before=[];self.pending_after=[]
        with self.lock:self.frames.clear();self.bytes=0
    def cached(self,position,direction):
        with self.lock:
            if not self.frames:return None
            times=[v[0] for v in self.frames]
            i=bisect.bisect_right(times,max(0.,position)+1e-7)-1
            if i<0:
                if not self.at_start:return None
                i=0
            reaches_end=self.eof and not self.pending_after
            if position>times[-1]+1e-7 and not reaches_end:return None
            target=i+direction
            if target<0:
                if not self.at_start:return None
                target=0
            if target>=len(times):
                if not reaches_end:return None
                target=len(times)-1
            self.cache_hits+=1
            return self.frames[target]
    def _append(self,timestamp,frame):
        image=to_image(frame)
        with self.lock:
            if self.frames and timestamp<=self.frames[-1][0]+1e-9:return
            self.frames.append((timestamp,image));self.bytes+=image.sizeInBytes()
            while len(self.frames)>48 or (self.bytes>self.budget and len(self.frames)>2):
                self.bytes-=self.frames.pop(0)[1].sizeInBytes();self.at_start=False
    def _fill_pending(self,cancelled,direction=1):
        while (self.pending_before or self.pending_after) and not cancelled():
            if self.pending_after and (direction>=0 or not self.pending_before):
                value=self.pending_after[0];self._append(*value);self.pending_after.pop(0)
            else:
                timestamp,frame=self.pending_before[-1];image=to_image(frame)
                with self.lock:
                    if self.bytes+image.sizeInBytes()>self.budget or len(self.frames)>=48:
                        # Keep the contiguous current/tail window. Evicting
                        # its tail here would create holes when pending_after
                        # is appended and could skip frames on a reverse step.
                        self.pending_before.clear();continue
                    self.frames.insert(0,(timestamp,image));self.bytes+=image.sizeInBytes();self.at_start=timestamp<=1e-7
                    self.pending_before.pop()
            yield None
    def _open(self):
        if self.container:return
        self.container=av.open(str(self.path));self.opens+=1
        if not self.container.streams.video:raise ValueError('文件中没有视频轨道')
        self.stream=self.container.streams.video[0]
        self.stream.thread_type='AUTO'
        self.stream.codec_context.thread_count=min(4,max(1,(os.cpu_count() or 2)//2))
        first=next(self.container.decode(self.stream),None)
        if first is None or first.time is None:raise ValueError('缺少帧时间戳，无法逐帧定位')
        self.raw_frame_bytes=max(1,sum(plane.buffer_size for plane in first.planes))
        self.origin=float(self.stream.start_time*self.stream.time_base) if self.stream.start_time is not None else float(first.time)
        self.duration=max(float(self.container.duration or 0)/av.time_base,float(self.stream.duration*self.stream.time_base) if self.stream.duration else 0.)
    def _next(self):
        frame=next(self.iterator,None)
        if frame is None:
            with self.lock:self.eof=True
            return None
        if frame.time is None:raise ValueError('缺少帧时间戳，无法逐帧定位')
        return max(0.,float(frame.time)-self.origin),frame
    def obtain(self,position,direction,cancelled,force=False,prepare=False):
        self._open()
        result=None if force else self.cached(position,direction)
        if result:return result
        pending=self.pending_before+self.pending_after
        if not force and pending and self.frames and min(pending[0][0],self.frames[0][0])-1e-7<=position<=max(pending[-1][0],self.frames[-1][0])+1e-7:
            for _ in self._fill_pending(cancelled,direction):
                result=self.cached(position,direction)
                if result:return result
        # Continue sequentially at the forward edge instead of seeking again.
        with self.lock:
            forward=bool(self.frames and self.iterator and self.frames[-1][0]-1e-7<=position<=self.frames[-1][0]+.5)
        if forward and not force:
            while not cancelled():
                value=self._next()
                if value:self._append(*value)
                result=self.cached(position,direction)
                if result:return result
                if value is None:break
        absolute=self.origin+max(0.,position)
        for offset in dict.fromkeys([max(self.origin,absolute-float(self.stream.time_base)),max(self.origin,absolute-2),max(self.origin,absolute-30),self.origin]):
            if cancelled():return None
            # Keep current images available while seeking a replacement window.
            # The old decoder iterator cannot be reused after this seek.
            self.iterator=None
            self.container.seek(math.floor(offset/float(self.stream.time_base)),stream=self.stream,backward=True,any_frame=False)
            self.seeks+=1;iterator=self.container.decode(self.stream);at_end=False
            # A longer reverse window avoids another GOP decode every few
            # backward clicks. Bound raw-frame retention separately for UHD.
            raw_bytes=self.raw_frame_bytes
            scale=min(1.,1920/max(self.stream.codec_context.width,self.stream.codec_context.height))
            image_bytes=max(1,int(self.stream.codec_context.width*self.stream.codec_context.height*scale*scale*3))
            prior=deque(maxlen=max(3,min(25,192*1024*1024//raw_bytes,self.budget//image_bytes-6)));following=None
            while not cancelled():
                frame=next(iterator,None)
                if frame is None:at_end=True;break
                if frame.time is None:raise ValueError('缺少帧时间戳，无法逐帧定位')
                value=(max(0.,float(frame.time)-self.origin),frame)
                if value[0]<=position+1e-7:prior.append(value)
                else:following=value;break
            if cancelled():return None
            values=list(prior)+([following] if following else [])
            if not values:continue
            floor=bisect.bisect_right([v[0] for v in values],position+1e-7)-1
            target=max(0,floor)+direction
            if target<0 and values[0][0]>1e-7:continue
            target=max(0,min(len(values)-1,target))
            selected=(values[target][0],to_image(values[target][1]))
            ready=[]
            if prepare:
                for timestamp,frame in values:
                    if cancelled():return None
                    ready.append(selected if abs(timestamp-selected[0])<1e-9 else (timestamp,to_image(frame)))
            if cancelled():return None
            with self.lock:
                self.frames=ready or [selected];self.bytes=sum(image.sizeInBytes() for _,image in self.frames)
                self.pending_before=[] if ready else values[:target];self.pending_after=[] if ready else values[target+1:]
                self.at_start=self.frames[0][0]<=1e-7;self.eof=at_end;self.iterator=iterator
            return selected
        raise ValueError('无法定位相邻视频帧')
    def prefetch(self,cancelled,count=8,direction=1):
        for _ in self._fill_pending(cancelled,direction):pass
        if direction<0:return
        for _ in range(count):
            if cancelled() or self.eof or self.iterator is None:return
            value=self._next()
            if value is None:return
            self._append(*value)
    def warm(self,position,direction,cancelled):
        with self.lock:
            index=bisect.bisect_right([v[0] for v in self.frames],position+1e-7)-1
            reverse_edge=direction<0 and index<6 and not self.at_start and not self.pending_before
        self.obtain(position,0,cancelled,force=reverse_edge,prepare=True)
        self.prefetch(cancelled,6,direction)


class FrameWorker(QThread):
    ready = Signal(float,QImage)
    failure = Signal(str)
    def __init__(self,path,position,direction,session=None,display=True):
        super().__init__()
        self.path,self.position,self.direction = path,position,direction
        self.session=session or FrameSession(path);self.display=display
        self.prefetch_direction=direction if direction else 1
    def run(self):
        try:
            if not self.display:
                self.session.warm(self.position,self.prefetch_direction,self.isInterruptionRequested)
                return
            result = self.session.obtain(self.position,self.direction,self.isInterruptionRequested)
            if result is None: return
            if not self.isInterruptionRequested():self.ready.emit(*result)
            # Produce adjacent frames immediately instead of waiting for the
            # next click to create another OS thread for every cache miss.
            self.session.prefetch(self.isInterruptionRequested,3,self.prefetch_direction)
        except Exception as exc:
            self.session.close()
            if not self.isInterruptionRequested():self.failure.emit(str(exc))


class FramePreview(InspectionCanvas):
    pass
