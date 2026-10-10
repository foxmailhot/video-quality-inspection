"""Background report snapshots and frame-accurate manual issue edits."""
import base64,copy
from pathlib import Path
import av
from PySide6.QtCore import QThread,Signal,QBuffer,QIODevice,Qt
from PySide6.QtWidgets import QDialog,QFormLayout,QComboBox,QSpinBox,QPlainTextEdit,QDialogButtonBox,QLabel
from frame_navigation import to_image
from reporting import write_html

class IssueDialog(QDialog):
    def __init__(self,parent,issue,kinds):
        super().__init__(parent);self.setWindowTitle('编辑问题');self.resize(480,360)
        f=QFormLayout(self);self.kind=QComboBox();self.kind.addItems(kinds);self.kind.setCurrentText(issue['kind']);f.addRow('问题类型',self.kind)
        self.first=QSpinBox();self.last=QSpinBox()
        for box,key,label in ((self.first,'frame','起始帧（从 0）'),(self.last,'last_frame','末帧（包含）')):
            box.setRange(0,2147483646);box.setValue(issue[key]);f.addRow(label,box)
        self.review=QComboBox();self.review.addItems(['待复核','确认问题','误报']);self.review.setCurrentText(issue.get('review','待复核'));f.addRow('复核结论',self.review)
        self.detail=QPlainTextEdit(issue['detail']);f.addRow('问题说明',self.detail)
        self.error=QLabel('修改帧范围后，时间将按原视频实际帧重新计算。');self.error.setWordWrap(True);f.addRow(self.error)
        buttons=QDialogButtonBox(QDialogButtonBox.Save|QDialogButtonBox.Cancel);buttons.accepted.connect(self.validate);buttons.rejected.connect(self.reject);f.addRow(buttons)
    def validate(self):
        if self.last.value()<self.first.value():self.error.setText('末帧不能小于起始帧。');return
        self.accept()
    def values(self):return dict(kind=self.kind.currentText(),frame=self.first.value(),last_frame=self.last.value(),review=self.review.currentText(),detail=self.detail.toPlainText())

class ReportTask(QThread):
    progress=Signal(int);success=Signal(object);failure=Signal(str)
    def __init__(self,result,path=None,edit=None):
        super().__init__();self.result=copy.deepcopy(result);self.path=path;self.edit=edit
    def run(self):
        try:
            if self.edit is not None:
                v=self.edit;first,last=v['frame'],v['last_frame'];times={};end=None
                with av.open(self.result['path']) as c:
                    s=c.streams.video[0];origin=None
                    for n,frame in enumerate(c.decode(s)):
                        if self.isInterruptionRequested():return
                        if frame.time is None:raise ValueError('视频帧缺少时间戳，无法修改帧范围')
                        if origin is None:origin=float(s.start_time*s.time_base) if s.start_time is not None else float(frame.time)
                        t=max(0.,float(frame.time)-origin)
                        if n in (first,last):times[n]=t
                        if n==last+1:end=t;break
                        self.progress.emit(min(99,int(n/max(1,last+1)*100)))
                    if first not in times or last not in times:raise ValueError('帧范围超出视频实际长度')
                    if end is None:end=times[last]+float(frame.duration*frame.time_base) if frame.duration else times[last]+1/float(s.average_rate or 25)
                v.update(start=times[first],end=end,manual=True);self.success.emit(v);return
            issues=self.result.get('issues',[]);targets={int(v['frame']) for v in issues};shots={};warning=''
            if targets:
                try:
                    with av.open(self.result['path']) as c:
                        for n,frame in enumerate(c.decode(c.streams.video[0])):
                            if self.isInterruptionRequested():return
                            if n in targets:
                                image=to_image(frame).scaled(240,150,aspectMode=Qt.KeepAspectRatio)
                                buf=QBuffer();buf.open(QIODevice.WriteOnly);image.save(buf,'JPEG',80)
                                shots[n]=base64.b64encode(bytes(buf.data())).decode();self.progress.emit(int(len(shots)/len(targets)*100))
                            if len(shots)==len(targets):break
                    if len(shots)<len(targets):warning='部分问题帧超出视频范围，未能生成截图。'
                except Exception as exc:warning='无法读取原视频截图：'+str(exc)
            if self.isInterruptionRequested():return
            write_html(self.path,self.result,shots,warning);self.success.emit(self.path)
        except Exception as exc:self.failure.emit(str(exc))
