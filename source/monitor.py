"""White inspection canvas shared by playback and exact frame preview."""
import threading
from PySide6.QtCore import Qt, Signal, Slot, QRectF, QPointF, QSizeF, QThread
from PySide6.QtGui import QPainter, QColor, QImage
from PySide6.QtWidgets import QWidget, QPushButton
from PySide6.QtMultimedia import QVideoSink, QVideoFrame


class WheelButton(QPushButton):
    ticks = Signal(int)
    def __init__(self,text,parent=None):
        super().__init__(text,parent);self.remainder=0
        self.setFocusPolicy(Qt.NoFocus)
    def wheelEvent(self,event):
        # High-resolution wheels accumulate partial detents instead of rounding
        # each small event to a complete frame. One standard detent = 120 units.
        delta=event.angleDelta().y()
        if delta:
            self.remainder+=delta
            count=int(self.remainder/120)
            self.remainder-=count*120
            if count:self.ticks.emit(count)
        event.accept()
    def leaveEvent(self,event):
        self.remainder=0;super().leaveEvent(event)


class InspectionCanvas(QWidget):
    geometryChanged = Signal()
    panChanged = Signal(object)
    def __init__(self):
        super().__init__();self.image=QImage();self.zoom=1.;self.pan=QPointF();self.drag=None
        self.setMinimumSize(240,140);self.setCursor(Qt.OpenHandCursor)
        self.setToolTip('白色为监视器背景；视频内的黑边保持原样。拖动画面可平移，双击恢复居中。')
    def set_image(self,image):
        changed=self.image.size()!=image.size();self.image=image;self.update()
        if changed:self.geometryChanged.emit()
    def source_size(self):return QSizeF(self.image.size())
    def target_rect(self):
        size=self.source_size()
        if size.isEmpty():return QRectF()
        area=self.rect().adjusted(12,12,-12,-12)
        scale=min(area.width()/size.width(),area.height()/size.height())*self.zoom
        width,height=size.width()*scale,size.height()*scale
        return QRectF((self.width()-width)/2+self.pan.x(),(self.height()-height)/2+self.pan.y(),width,height)
    def paintEvent(self,event):
        p=QPainter(self);p.fillRect(self.rect(),QColor('white'))
        if self.source_size().isEmpty():return
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        self.draw_frame(p,self.target_rect())
    def draw_frame(self,p,target):p.drawImage(target,self.image)
    def mousePressEvent(self,event):
        if event.button()==Qt.LeftButton:self.drag=event.position();self.setCursor(Qt.ClosedHandCursor);event.accept()
    def mouseMoveEvent(self,event):
        if self.drag is not None:
            self.pan+=event.position()-self.drag;self.drag=event.position();self.panChanged.emit(self.pan);self.update()
    def mouseReleaseEvent(self,event):self.drag=None;self.setCursor(Qt.OpenHandCursor)
    def mouseDoubleClickEvent(self,event):self.pan=QPointF();self.panChanged.emit(self.pan);self.update()


def video_size(frame):
    if not frame.isValid():return QSizeF()
    size=QSizeF(frame.surfaceFormat().viewport().size())
    rotation=frame.rotation().value+frame.surfaceFormat().rotation().value
    if rotation%180:size.transpose()
    return size


class PreviewConverter(QThread):
    """Bounded latest-frame mailbox: conversion never runs in the GUI thread.

    Playback can discard superseded display frames; exact navigation never does.
    Both input and output hold at most one pending frame, regardless of UI load.
    """
    available=Signal()
    def __init__(self,parent):
        super().__init__(parent);self.condition=threading.Condition();self.pending=None;self.output=None;self.stopping=False
        self.converted=0;self.superseded=0
    def submit(self,frame,epoch,edge):
        with self.condition:
            if self.stopping:return
            if self.pending is not None:self.superseded+=1
            self.pending=(QVideoFrame(frame),epoch,edge);self.condition.notify()
    def clear(self):
        with self.condition:self.pending=None;self.output=None
    def take(self):
        with self.condition:result=self.output;self.output=None;return result
    def stop(self):
        with self.condition:self.stopping=True;self.pending=None;self.output=None;self.condition.notify()
    def run(self):
        while True:
            with self.condition:
                while self.pending is None and not self.stopping:self.condition.wait()
                if self.stopping:return
                frame,epoch,edge=self.pending;self.pending=None
            size=video_size(frame)
            if size.isEmpty():continue
            scale=min(1.,edge/max(size.width(),size.height()))
            image=QImage(max(1,round(size.width()*scale)),max(1,round(size.height()*scale)),QImage.Format_RGB32)
            painter=QPainter(image)
            try:
                options=QVideoFrame.PaintOptions();options.backgroundColor=QColor('white');options.aspectRatioMode=Qt.KeepAspectRatio
                frame.paint(painter,QRectF(image.rect()),options)
            finally:painter.end()
            timestamp=frame.startTime()/1e6 if frame.startTime()>=0 else None
            with self.condition:
                if self.stopping:return
                self.output=(epoch,image,timestamp,size);self.converted+=1
            self.available.emit()


class VideoMonitor(InspectionCanvas):
    def __init__(self):
        super().__init__();self.sink=QVideoSink(self);self.frame=QVideoFrame();self.displayed_time=None
        self.epoch=0;self.image_time=None;self.original_size=QSizeF()
        self.converter=PreviewConverter(self);self.converter.available.connect(self.converted_frame,Qt.QueuedConnection);self.converter.start()
        self.sink.videoFrameChanged.connect(self.receive_frame)
    def videoSink(self):return self.sink
    @Slot(QVideoFrame)
    def receive_frame(self,frame):
        self.frame=frame
        if not frame.isValid():self.invalidate(clear=True);return
        # Convert to approximately display resolution, up to full-HD. Exact
        # paused frames continue to use their independent inspection decoder.
        edge=max(640,min(1920,round(max(self.width(),self.height())*self.zoom)))
        self.converter.submit(frame,self.epoch,edge)
    @Slot()
    def converted_frame(self):
        value=self.converter.take()
        if value is None or value[0]!=self.epoch:return
        _,image,timestamp,size=value;changed=self.original_size!=size
        self.image=image;self.image_time=timestamp;self.original_size=size;self.update()
        if changed:self.geometryChanged.emit()
    def invalidate(self,clear=False):
        self.epoch+=1;self.converter.clear()
        if clear:
            self.frame=QVideoFrame();self.image=QImage();self.displayed_time=None;self.image_time=None;self.original_size=QSizeF();self.update()
    def shutdown(self):
        self.converter.stop();return self.converter.wait(3000)
    def resume_converter(self):
        if not self.converter.isRunning():
            self.converter=PreviewConverter(self);self.converter.available.connect(self.converted_frame,Qt.QueuedConnection);self.converter.start()
    def source_size(self):
        return self.original_size
    def draw_frame(self,p,target):
        p.drawImage(target,self.image)
        self.displayed_time=self.image_time
