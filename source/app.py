from __future__ import annotations
import sys, os, json, csv, traceback, hashlib
from pathlib import Path
from dataclasses import asdict, fields
from PySide6.QtCore import Qt, QThread, Signal, QUrl, QTimer, QStandardPaths, Slot, QPointF
from PySide6.QtGui import QColor, QPainter, QPen, QShortcut, QKeySequence, QImage
from PySide6.QtWidgets import (QApplication,QMainWindow,QWidget,QVBoxLayout,QHBoxLayout,
    QPushButton,QLabel,QListWidget,QListWidgetItem,QSplitter,QTableWidget,QTableWidgetItem,
    QHeaderView,QProgressBar,QFileDialog,QMessageBox,QDoubleSpinBox,QSpinBox,QFormLayout,
    QGroupBox,QScrollArea,QCheckBox,QComboBox,QAbstractItemView,QFrame,QStackedWidget,QDialog,QSlider,QPlainTextEdit,QToolTip,QProgressDialog,QMenu)
from PySide6.QtMultimedia import QMediaPlayer,QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget
from engine import Settings, analyze, Cancelled, save_json
from frame_navigation import FrameWorker, FramePreview, FrameSession
from shortcuts import ShortcutRouter, ShortcutDialog, DEFAULT_KEYS, normalize_keys
from playback_clock import PlaybackClock
from monitor import VideoMonitor, WheelButton
from reporting import write_html, read_report, csv_safe
from updater import Updater,VERSION,fetch_latest
from issue_tools import IssueDialog,ReportTask
from compact_controls import SeekSlider,CompactStatus
from appearance import BackgroundCanvas, GlassPanel, BackgroundDialog, DEFAULT_APPEARANCE, clean_settings

COLORS = {'黑帧':'#adbbd1','疑似定帧':'#61adff','疑似夹帧':'#ffc467','疑似花屏':'#ef79c7','解码损坏':'#ff716c','黑边':'#c6a0ff','疑似镜像':'#f5a679'}

def stamp(t):
    ms = max(0,round(t*1000))
    return f'{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02}.{ms%1000:03}'

class Worker(QThread):
    update = Signal(int,str,list)
    result = Signal(dict)
    failure = Signal(str)
    stopped = Signal()
    def __init__(self,path,cfg):
        super().__init__(); self.path,self.cfg = path,cfg
    def run(self):
        try: self.result.emit(analyze(self.path,self.cfg,self.update.emit,self.isInterruptionRequested))
        except Cancelled: self.stopped.emit()
        except Exception as exc: self.failure.emit(str(exc))

class Timeline(QWidget):
    seek = Signal(float)
    def __init__(self):
        super().__init__(); self.duration=1.; self.position=0.; self.issues=[]
        self.setMinimumHeight(140); self.setMouseTracking(True)
    def lane_height(self):return max(16,(self.height()-32)/len(COLORS))
    def paintEvent(self,event):
        p=QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(),QColor('#101721'))
        left,w=95,max(1,self.width()-110)
        bar_h=min(22,max(8,int(self.lane_height()-5)))
        for j,(kind,color) in enumerate(COLORS.items()):
            y=10+int(j*self.lane_height())
            p.setPen(QColor('#92a4bb')); p.drawText(8,y+12,kind)
            p.fillRect(left,y,w,bar_h,QColor('#202d3d'))
            p.setPen(Qt.NoPen)
            for item in self.issues:
                if item['kind']!=kind: continue
                if item.get('review')=='误报':continue
                x=left+min(1,item['start']/max(self.duration,.001))*w
                span=max(3,(item['end']-item['start'])/max(self.duration,.001)*w)
                c=QColor(color)
                confirmed=item.get('review')=='确认问题'
                if confirmed:
                    span=max(8,span);c=c.lighter(135)
                    p.fillRect(int(x),y-2,int(span),bar_h+4,c)
                    p.setPen(QPen(QColor('#ffffff'),2));p.drawRect(int(x),y-2,int(span),bar_h+4)
                    p.setPen(Qt.NoPen)
                else:p.fillRect(int(x),y,max(5,int(span)),bar_h,c)
        x=left+min(1,self.position/max(self.duration,.001))*w
        p.setPen(QPen(QColor('#79e4be'),2)); p.drawLine(int(x),4,int(x),self.height()-24)
        p.setPen(QColor('#92a4bb')); p.drawText(left,self.height()-3,'00:00:00')
        p.drawText(max(left,self.width()-105),self.height()-3,stamp(self.duration))
    def mousePressEvent(self,event):
        x=event.position().x(); w=max(1,self.width()-110)
        t=max(0,min(self.duration,(x-95)/w*self.duration))
        row=int((event.position().y()-10)//self.lane_height())
        kind=list(COLORS)[row] if 0<=row<len(COLORS) else ''
        near=[v for v in self.issues if v.get('review')!='误报' and v['kind']==kind and abs(v['start']-t)<self.duration*6/w]
        if near: t=min(near,key=lambda v:abs(v['start']-t))['start']
        self.seek.emit(t)
    def mouseMoveEvent(self,event):
        t=max(0,(event.position().x()-95)/max(1,self.width()-110)*self.duration)
        found=[v for v in self.issues if v.get('review')!='误报' and v['start']-.1<=t<=v['end']+.1]
        self.setToolTip('\n'.join(f'{v["kind"]}  {stamp(v["start"])}  {v["detail"]}' for v in found) or stamp(t))

class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f'FrameScope {VERSION} · 视频质检工作台')
        self.resize(1480,940); self.setMinimumSize(1040,700)
        self.data_dir=Path(QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation))
        self.data_dir.mkdir(parents=True,exist_ok=True)
        self.jobs=[]; self.active=None; self.worker=None; self.running=False; self.current=None
        self.issue_sort=None;self.report_task=None
        self.media_path=None; self.pending_seek=None; self.controls={}; self.visible_issues=[]
        self.clear_requested=False;self.appearance=DEFAULT_APPEARANCE.copy()
        self.frame_worker=None;self.frame_queue=[];self.frame_generation=0;self.precise_time=None;self.navigation_position=None
        self.frame_session=None;self.shortcuts=DEFAULT_KEYS.copy();self.clock=PlaybackClock()
        self.close_requested=False;self.closing=False;self.view_settings={};self.frame_last_direction=1
        self.updater=Updater(self)
        self.warm_timer=QTimer(self);self.warm_timer.setSingleShot(True);self.warm_timer.timeout.connect(self.warm_preview)
        self.background=BackgroundCanvas();root=self.background;self.setCentralWidget(root);outer=QVBoxLayout(root);outer.setContentsMargins(10,8,10,8);outer.setSpacing(6)
        header=QHBoxLayout();header.setSpacing(5);title=QLabel('FrameScope');title.setObjectName('title');header.addWidget(title)
        for label,fn in [('＋ 视频',self.add_files),('文件夹',self.add_folder),('导入',self.import_report),('导出日志',self.export_report)]:
            button=QPushButton(label);button.clicked.connect(fn);header.addWidget(button)
        self.params_button=QPushButton('检测参数');self.params_button.clicked.connect(self.open_parameters);header.addWidget(self.params_button)
        header.addStretch()
        self.settings_button=QPushButton('设置');self.settings_menu=QMenu(self.settings_button)
        self.settings_menu.addAction('快捷键',self.edit_shortcuts)
        self.settings_menu.addAction('背景',self.edit_background)
        self.settings_menu.addAction('检查更新',lambda:self.updater.check(True))
        self.settings_button.setMenu(self.settings_menu);header.addWidget(self.settings_button);outer.addLayout(header)
        split=QSplitter(Qt.Horizontal); outer.addWidget(split,1)
        left=GlassPanel(root); ll=QVBoxLayout(left); ll.setContentsMargins(8,8,8,8)
        ll.addWidget(QLabel('检测队列')); self.queue=QListWidget(); self.queue.setMinimumWidth(230); self.queue.currentRowChanged.connect(self.select_job); ll.addWidget(self.queue)
        row=QHBoxLayout(); rem=QPushButton('移除选中'); rem.clicked.connect(self.remove_job); row.addWidget(rem); retry=QPushButton('重新检测'); retry.clicked.connect(self.retry); row.addWidget(retry); ll.addLayout(row)
        self.clear_button=QPushButton('一键清空列表');self.clear_button.setToolTip('清空全部队列项和当前结果；检测中会先停止任务。原视频文件不受影响。');self.clear_button.clicked.connect(self.clear_all);ll.addWidget(self.clear_button)
        ll.addWidget(QLabel('拖入视频文件即可添加\n双击问题列表可跳到对应位置'))
        split.addWidget(left)
        center=GlassPanel(root); cl=QVBoxLayout(center); cl.setContentsMargins(8,8,8,8)
        monitor_header=QHBoxLayout();monitor_header.setSpacing(8)
        self.file_label=CompactStatus('添加视频，开始检查');self.file_label.setObjectName('filename');monitor_header.addWidget(self.file_label,1)
        self.scan_percent=QLabel('检测 0%');self.scan_percent.setMinimumWidth(65);monitor_header.addWidget(self.scan_percent)
        self.progress=QProgressBar();self.progress.setFormat('检测进度  %p%');self.progress.setTextVisible(False);self.progress.setFixedSize(120,6);self.progress.setValue(0)
        self.progress.valueChanged.connect(lambda value:self.scan_percent.setText('检测中…' if value<0 else f'检测 {value}%'));monitor_header.addWidget(self.progress)
        cl.addLayout(monitor_header)
        self.monitor_split=QSplitter(Qt.Vertical);cl.addWidget(self.monitor_split,1)
        monitor_panel=QWidget();ml=QVBoxLayout(monitor_panel);ml.setContentsMargins(0,0,0,0);self.monitor_split.addWidget(monitor_panel)
        self.player=QMediaPlayer(self); self.audio=QAudioOutput(self); self.audio.setVolume(.65); self.player.setAudioOutput(self.audio)
        self.video=VideoMonitor();self.player.setVideoSink(self.video.videoSink())
        QApplication.instance().aboutToQuit.connect(self.video.shutdown)
        self.frame_preview=FramePreview();self.video_stack=QStackedWidget();self.video_stack.addWidget(self.video);self.video_stack.addWidget(self.frame_preview);ml.addWidget(self.video_stack,1)
        playback=QHBoxLayout();self.play_button=QPushButton('播放 / 暂停');self.play_button.clicked.connect(self.toggle_play);playback.addWidget(self.play_button)
        self.frame_buttons=[]
        for label, amount in [('−1 帧',-1),('+1 帧',1)]:
            b=QPushButton(label);b.setToolTip('暂停并显示相邻的实际视频帧（支持可变帧率）');b.setAutoRepeat(True);b.setAutoRepeatDelay(300);b.setAutoRepeatInterval(45);b.setFocusPolicy(Qt.NoFocus);b.clicked.connect(lambda checked=False,a=amount:self.step_frame(a));playback.addWidget(b);self.frame_buttons.append(b)
        self.wheel_frames=WheelButton('滚轮逐帧');self.wheel_frames.setToolTip('鼠标放在这里滚动：向上下一帧，向下上一帧；一格一帧。');self.wheel_frames.ticks.connect(self.wheel_step);playback.addWidget(self.wheel_frames)
        self.speed_button=WheelButton('1.0× 倍速');self.speed_button.setToolTip('滚轮每格调整 0.1 倍，范围 0.1–10 倍；点击恢复 1 倍。');self.speed_button.ticks.connect(self.wheel_speed);self.speed_button.clicked.connect(lambda:self.set_speed(1.));playback.addWidget(self.speed_button)
        self.play_slider=SeekSlider();self.play_slider.setRange(0,10000);self.play_slider.setToolTip('播放进度：点击直接定位，拖动后松开定位')
        self.play_slider.sliderReleased.connect(lambda:self.seek(self.play_slider.value()/10000*self.clock.duration))
        self.play_slider.hovered.connect(lambda value:QToolTip.showText(self.play_slider.mapToGlobal(self.play_slider.rect().center()),stamp(value/10000*self.clock.duration),self.play_slider))
        playback.addWidget(self.play_slider,1)
        self.time_label=QLabel('00:00:00.000 / 00:00:00.000');playback.addWidget(self.time_label);ml.addLayout(playback)
        self.frame_hint=QLabel('左右方向键逐帧',self);self.frame_hint.hide()
        viewrow=QHBoxLayout();self.auto_monitor=QCheckBox('自动适配监视器');self.auto_monitor.setChecked(True);self.auto_monitor.toggled.connect(self.adapt_monitor);viewrow.addWidget(self.auto_monitor)
        viewrow.addWidget(QLabel('高度'));self.monitor_height=QSpinBox();self.monitor_height.setRange(140,1600);self.monitor_height.setValue(360);self.monitor_height.setSuffix(' px');self.monitor_height.valueChanged.connect(self.set_monitor_height);viewrow.addWidget(self.monitor_height)
        viewrow.addWidget(QLabel('画面'));self.zoom_box=QSpinBox();self.zoom_box.setRange(10,400);self.zoom_box.setValue(100);self.zoom_box.setSuffix(' %');self.zoom_box.valueChanged.connect(self.set_zoom);viewrow.addWidget(self.zoom_box)
        reset=QPushButton('适合窗口 / 居中');reset.clicked.connect(self.reset_view);viewrow.addWidget(reset)
        self.focus_button=QPushButton('专注监视器');self.focus_button.setCheckable(True);self.focus_button.toggled.connect(self.focus_monitor);viewrow.addWidget(self.focus_button);viewrow.addStretch();ml.addLayout(viewrow)
        self.monitor_split.splitterMoved.connect(self.monitor_resized)
        for canvas in (self.video,self.frame_preview):
            canvas.geometryChanged.connect(self.adapt_monitor);canvas.panChanged.connect(self.sync_pan)
        lower=QWidget();self.results_panel=lower;cl=QVBoxLayout(lower);cl.setContentsMargins(0,0,0,0);self.monitor_split.addWidget(lower);self.monitor_split.setStretchFactor(0,3);self.monitor_split.setStretchFactor(1,2)
        self.queue_bar=QWidget();queue_controls=QHBoxLayout(self.queue_bar);queue_controls.setContentsMargins(0,0,0,0);queue_controls.setSpacing(6);queue_controls.addStretch()
        self.badge=QLabel('本地处理');self.badge.setToolTip('视频不会上传');self.badge.setObjectName('badge');queue_controls.addWidget(self.badge)
        self.start=QPushButton('开始队列');self.start.setObjectName('primary');self.start.clicked.connect(self.start_queue);queue_controls.addWidget(self.start)
        self.stop=QPushButton('停止');self.stop.clicked.connect(self.stop_queue);self.stop.setEnabled(False);queue_controls.addWidget(self.stop);cl.addWidget(self.queue_bar)
        self.results_split=QSplitter(Qt.Horizontal);self.results_split.setHandleWidth(9);self.results_split.setChildrenCollapsible(False);cl.addWidget(self.results_split,1)
        track_panel=QWidget();track_layout=QVBoxLayout(track_panel);track_layout.setContentsMargins(0,0,0,0)
        track_title=QLabel('问题轨道 · 点击定位');track_title.setMinimumHeight(30);track_layout.addWidget(track_title)
        self.timeline=Timeline();self.timeline.setMinimumWidth(230); self.timeline.seek.connect(self.seek);track_layout.addWidget(self.timeline,1);self.results_split.addWidget(track_panel)
        issue_panel=QWidget();cl=QVBoxLayout(issue_panel);cl.setContentsMargins(0,0,0,0);self.results_split.addWidget(issue_panel)
        self.results_split.setStretchFactor(0,1);self.results_split.setStretchFactor(1,3);self.results_split.setSizes([300,850])
        self.monitor_split.setHandleWidth(9);self.monitor_split.setChildrenCollapsible(False)
        self.monitor_split.setToolTip('拖动此分隔条，调整监视器和问题区大小');self.results_split.setToolTip('拖动中间分隔条，调整左侧轨道与右侧问题列表的宽度')
        line=QHBoxLayout(); self.count=QLabel('问题标记  0'); line.addWidget(self.count);self.count.setToolTip('亮色白框：已确认；误报不显示在轨道');line.addStretch()
        self.filter=QComboBox(); self.filter.addItems(['全部类型']+list(COLORS)); self.filter.currentTextChanged.connect(self.refresh_table); line.addWidget(self.filter)
        for label,state in [('确认问题','确认问题'),('标为误报','误报'),('待复核','待复核')]:
            b=QPushButton(label); b.clicked.connect(lambda checked=False,s=state:self.review(s)); line.addWidget(b)
        edit=QPushButton('编辑');edit.clicked.connect(self.edit_issue);line.addWidget(edit)
        default=QPushButton('默认排序');default.clicked.connect(self.reset_issue_sort);line.addWidget(default)
        cl.addLayout(line)
        self.table=QTableWidget(0,6);self.table.setMinimumHeight(110); self.table.setHorizontalHeaderLabels(['类型','起点','时长','帧号（从 0）','复核','依据'])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows); self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers); self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive);self.table.horizontalHeader().setSectionResizeMode(5,QHeaderView.Stretch)
        for col,width in enumerate((95,110,75,95,85)):self.table.setColumnWidth(col,width)
        self.table.setWordWrap(True);self.table.setShowGrid(False);self.table.setAlternatingRowColors(True);self.table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().sectionDoubleClicked.connect(self.sort_issues)
        self.table.horizontalHeader().setToolTip('双击表头排序，再次双击切换升降序；默认排序将误报放在末尾')
        self.table.currentCellChanged.connect(self.update_issue_detail)
        self.table.cellDoubleClicked.connect(self.jump_issue); cl.addWidget(self.table,2)
        self.issue_detail=QPlainTextEdit();self.issue_detail.setReadOnly(True);self.issue_detail.setPlaceholderText('选择问题，查看完整时间、帧号和检测依据。可选中复制。');self.issue_detail.setMinimumHeight(54);self.issue_detail.setMaximumHeight(90);cl.addWidget(self.issue_detail)
        self.note=QLabel('夹帧、定帧、花屏均为候选问题；正常转场、静止镜头和特效可能触发。'); self.note.setWordWrap(True); self.note.setObjectName('muted'); cl.addWidget(self.note)
        split.addWidget(center)
        scroll=QScrollArea(); scroll.setWidgetResizable(True); scroll.setMinimumWidth(360)
        params=QWidget(); pl=QVBoxLayout(params)
        label=QLabel('检测参数'); label.setObjectName('filename'); pl.addWidget(label)
        hint=QLabel('修改后对下一次开始的任务生效。\n逐帧扫描，不按时间跳采样。'); hint.setWordWrap(True); hint.setObjectName('muted'); pl.addWidget(hint)
        preset=QComboBox(); preset.addItems(['标准','严格（更敏感）','宽松（减少候选）']); preset.currentIndexChanged.connect(self.preset); pl.addWidget(preset)
        defaults=Settings()
        def section(title):
            g=QGroupBox(title); f=QFormLayout(g); pl.addWidget(g); return f
        def number(form,key,label,lo,hi,step=1,decimals=0):
            w=QDoubleSpinBox() if decimals else QSpinBox(); w.setRange(lo,hi); w.setSingleStep(step)
            if decimals: w.setDecimals(decimals)
            w.setValue(getattr(defaults,key)); form.addRow(label,w); self.controls[key]=w
        def check(form,key,label):
            w=QCheckBox(label); w.setChecked(getattr(defaults,key)); form.addRow(w); self.controls[key]=w
        f=section('运行方式'); check(f,'gpu','自动使用 NVIDIA 独立显卡')
        w=QComboBox()
        for name,value in [('标准 · 960 像素宽',960),('精细 · 1920 像素宽',1920),('原始分辨率',0)]: w.addItem(name,value)
        f.addRow('分析尺寸',w); self.controls['width']=w
        number(f,'crop','四边忽略 %',0,25,1)
        f=section('黑帧'); check(f,'black','启用黑帧检测'); number(f,'black_level','暗像素亮度 ≤',0,100)
        number(f,'black_ratio','暗像素比例 % ≥',50,100,.1,1); number(f,'black_frames','最少连续帧',1,1000)
        f=section('定帧'); check(f,'freeze','启用疑似定帧'); number(f,'freeze_seconds','最短持续秒数',.05,120,.1,2); number(f,'freeze_delta','容许画面差',0,20,.1,2)
        f=section('夹帧 / 闪帧'); check(f,'flash','启用短暂突变检测'); number(f,'flash_delta','突变差值 ≥',1,255)
        number(f,'return_delta','返回相似差 ≤',1,100); number(f,'flash_frames','最多夹入帧数',1,30)
        f=section('四边黑边');check(f,'borders','启用上下左右黑边检测');number(f,'border_level','黑边亮度 ≤',0,100);number(f,'border_ratio','行 / 列暗像素 % ≥',80,100,.1,1);number(f,'border_percent','最小边宽 %',.1,25,.1,1);number(f,'border_frames','最少连续帧',1,1000)
        f=section('镜像片段');check(f,'mirror','启用疑似镜像检测');number(f,'mirror_delta','镜像匹配差 ≤',1,60,.5,1);number(f,'mirror_gain','相比原方向改善 % ≥',20,95,1);number(f,'mirror_frames','最少连续帧',1,1000)
        number(f,'mirror_window','回看参考秒数',1,120,1)
        f=section('花屏候选'); check(f,'artifacts','启用花屏启发式检测'); number(f,'block_ratio','块边界敏感阈值',1.5,20,.1,1)
        number(f,'noise_level','噪声强度阈值',5,150); number(f,'color_ratio','异常绿紫比例 %',1,100)
        pl.addStretch(); scroll.setWidget(params); split.setSizes([250,1190])
        self.params_dialog=QDialog(self);self.params_dialog.setWindowTitle('检测参数');self.params_dialog.resize(480,740);pd=QVBoxLayout(self.params_dialog);pd.addWidget(scroll);done=QPushButton('完成');done.clicked.connect(self.params_dialog.accept);pd.addWidget(done);self.params_dialog.finished.connect(lambda:self.save_state())
        for view in (self.queue,self.table,scroll):view.viewport().setAutoFillBackground(False)
        footer=QHBoxLayout();footer.setSpacing(8);self.status=CompactStatus('就绪');footer.addWidget(self.status,1)
        self.total=QLabel('队列 0 / 0');self.total.setObjectName('muted');footer.addWidget(self.total);outer.addLayout(footer)
        self.player.positionChanged.connect(self.position_changed); self.player.durationChanged.connect(self.duration_changed)
        self.player.mediaStatusChanged.connect(self.media_status)
        self.player.playbackStateChanged.connect(self.playback_state_changed)
        self.player.errorOccurred.connect(lambda *a:self.status.setText('播放失败：'+self.player.errorString()))
        self.setAcceptDrops(True)
        self.key_router=ShortcutRouter(self,dict(previous=lambda:self.step_frame(-1),next=lambda:self.step_frame(1),play=self.toggle_play,open=self.add_files))
        self.load_state()
        self.apply_shortcuts(self.shortcuts)
        self.apply_background(self.appearance)
        self.restore_view()

    def open_parameters(self):
        self.params_dialog.show();self.params_dialog.raise_();self.params_dialog.activateWindow()
    def focus_monitor(self,enabled):
        if enabled:self.saved_focus_sizes=(self.monitor_split.sizes(),self.results_split.sizes())
        self.results_panel.setVisible(not enabled);self.focus_button.setText('退出专注' if enabled else '专注监视器')
        if enabled:QTimer.singleShot(0,self.adapt_monitor)
        else:QTimer.singleShot(0,self.restore_focus_layout)
    def restore_focus_layout(self):
        sizes=getattr(self,'saved_focus_sizes',None)
        if sizes:self.monitor_split.setSizes(sizes[0]);self.results_split.setSizes(sizes[1])
    def update_issue_detail(self,*args):
        if not hasattr(self,'issue_detail'):return
        row=self.table.currentRow()
        if not 0<=row<len(self.visible_issues):self.issue_detail.clear();return
        v=self.visible_issues[row]
        self.issue_detail.setPlainText(f"{v['kind']} · {v.get('review','待复核')}   {stamp(v['start'])} → {stamp(v['end'])}   帧 {v['frame']}–{v['last_frame']}\n{v['detail']}")
    def wheel_step(self,ticks):
        for _ in range(abs(ticks)):self.step_frame(1 if ticks>0 else -1)
    def wheel_speed(self,ticks):self.set_speed(self.player.playbackRate()+ticks*.1)
    def set_speed(self,value):
        speed=max(.1,min(10.,round(float(value),1)))
        self.player.setPlaybackRate(speed);self.speed_button.setText(f'{speed:.1f}× 倍速')
    def sync_pan(self,point):
        for canvas in (self.video,self.frame_preview):canvas.pan=QPointF(point);canvas.update()
    def set_zoom(self,value):
        for canvas in (self.video,self.frame_preview):canvas.zoom=value/100.;canvas.update()
    def reset_view(self):
        self.zoom_box.setValue(100);self.sync_pan(QPointF());self.auto_monitor.setChecked(True);self.adapt_monitor()
    def set_monitor_height(self,value):
        self.auto_monitor.setChecked(False)
        sizes=self.monitor_split.sizes();total=sum(sizes)
        chrome=max(50,self.monitor_split.widget(0).height()-self.video_stack.height())
        desired=value+chrome
        self.monitor_split.setSizes([desired,max(100,total-desired)])
        QTimer.singleShot(0,self.update_monitor_height)
    def update_monitor_height(self):
        self.monitor_height.blockSignals(True);self.monitor_height.setValue(self.video_stack.height());self.monitor_height.blockSignals(False)
    def monitor_resized(self,*args):
        self.auto_monitor.setChecked(False)
        self.monitor_height.blockSignals(True);self.monitor_height.setValue(self.video_stack.height());self.monitor_height.blockSignals(False)
    def adapt_monitor(self,*args):
        if not hasattr(self,'auto_monitor') or not self.auto_monitor.isChecked():return
        canvas=self.video_stack.currentWidget();size=canvas.source_size()
        if size.isEmpty():size=self.video.source_size()
        total=sum(self.monitor_split.sizes())
        if total<=0:return
        ratio=size.height()/size.width() if not size.isEmpty() else 9/16
        reserve=0 if self.focus_button.isChecked() else 410
        height=max(140,min((self.video_stack.width()-24)*ratio+24,total-reserve))
        chrome=max(50,self.monitor_split.widget(0).height()-self.video_stack.height())
        self.monitor_split.setSizes([int(height+chrome),max(100,int(total-height-chrome))])
        self.monitor_height.blockSignals(True);self.monitor_height.setValue(self.video_stack.height());self.monitor_height.blockSignals(False)
    def resizeEvent(self,event):
        super().resizeEvent(event)
        if hasattr(self,'auto_monitor'):QTimer.singleShot(0,self.adapt_monitor)
    def current_view(self):
        return dict(zoom=self.zoom_box.value(),auto=self.auto_monitor.isChecked(),height=self.video_stack.height(),speed=self.player.playbackRate(),panels=(self.saved_focus_sizes[0] if self.focus_button.isChecked() and hasattr(self,'saved_focus_sizes') else self.monitor_split.sizes()),results=(self.saved_focus_sizes[1] if self.focus_button.isChecked() and hasattr(self,'saved_focus_sizes') else self.results_split.sizes()))
    def restore_view(self):
        v=self.view_settings if isinstance(self.view_settings,dict) else {}
        try:
            self.zoom_box.setValue(int(v.get('zoom',100)));self.set_speed(float(v.get('speed',1)))
            self.monitor_height.setValue(int(v.get('height',360)));self.auto_monitor.setChecked(bool(v.get('auto',True)))
        except (TypeError,ValueError):self.reset_view();self.set_speed(1.)
        def restore_sizes():
            for key,splitter in (('panels',self.monitor_split),('results',self.results_split)):
                values=v.get(key)
                if isinstance(values,list) and len(values)==2 and all(isinstance(x,int) and x>0 for x in values):splitter.setSizes(values)
            if not v.get('panels'):self.adapt_monitor()
        QTimer.singleShot(0,restore_sizes)
    def edit_shortcuts(self):
        dialog=ShortcutDialog(self,self.shortcuts)
        if dialog.exec()==QDialog.Accepted:self.apply_shortcuts(dialog.values());self.save_state()
    def apply_shortcuts(self,mapping):
        self.shortcuts=normalize_keys(mapping);self.key_router.mapping=self.shortcuts.copy()
        for button,name in zip(self.frame_buttons,('previous','next')):
            button.setToolTip('实际相邻帧 · 快捷键：'+(QKeySequence(self.shortcuts[name]).toString(QKeySequence.NativeText) or '未设置'))
        self.play_button.setToolTip('快捷键：'+(self.shortcuts['play'] or '未设置'))
        self.frame_hint.setText('逐帧 '+(' / '.join(self.shortcuts[k] or '未设置' for k in ('previous','next'))))

    def edit_background(self):
        dialog=BackgroundDialog(self,self.appearance,self.data_dir)
        if dialog.exec()==QDialog.Accepted:
            self.apply_background(dialog.draft);self.save_state()
    def apply_background(self,settings):
        self.appearance=clean_settings(settings)
        valid=self.background.set_settings(self.appearance)
        if not valid:
            self.appearance['mode']='none';self.status.setText('自定义背景图片不可用，已使用纯色背景；可重新导入图片。')

    def cfg(self):
        data={}
        for k,w in self.controls.items():
            data[k]=w.isChecked() if isinstance(w,QCheckBox) else w.currentData() if isinstance(w,QComboBox) else w.value()
        return Settings(**data)
    def apply_cfg(self,data):
        for k,v in data.items():
            if k not in self.controls: continue
            w=self.controls[k]
            if isinstance(w,QCheckBox): w.setChecked(v)
            elif isinstance(w,QComboBox): w.setCurrentIndex(max(0,w.findData(v)))
            else: w.setValue(v)
    def preset(self,i):
        if not self.controls:return
        values=asdict(Settings())
        if i==1: values.update(freeze_seconds=.5,freeze_delta=1,flash_delta=25,block_ratio=3,noise_level=38,black_level=28)
        if i==2: values.update(freeze_seconds=2,freeze_delta=.3,flash_delta=55,block_ratio=5.5,noise_level=65,black_level=12)
        self.apply_cfg(values)
    def add_files(self):
        paths,_=QFileDialog.getOpenFileNames(self,'选择视频','','视频 (*.mp4 *.mov *.mkv *.avi *.mxf *.m4v *.webm *.mts *.ts);;所有文件 (*)')
        self.add_paths(paths)
    def add_folder(self):
        folder=QFileDialog.getExistingDirectory(self,'选择视频文件夹（仅此层）')
        if folder:self.add_paths([str(p) for p in Path(folder).iterdir() if p.suffix.lower() in {'.mp4','.mov','.mkv','.avi','.mxf','.m4v','.webm','.mts','.ts'}])
    def add_paths(self,paths):
        if self.clear_requested:self.status.setText('正在停止检测并清空列表，请稍后添加');return
        for path in paths:
            p=str(Path(path).resolve())
            if not Path(p).is_file() or any(v['path']==p for v in self.jobs):continue
            self.jobs.append(dict(path=p,status='等待',result=None)); self.queue.addItem('')
        self.update_queue(); self.save_state()
        if self.current is None and self.jobs:self.queue.setCurrentRow(0)
    def update_queue(self):
        for i,j in enumerate(self.jobs):
            self.queue.item(i).setText(f'{Path(j["path"]).name}\n{j["status"]}'); self.queue.item(i).setToolTip(j['path'])
        done=sum(j['status'] in ('完成','完成 · 有警告','失败','已停止') for j in self.jobs)
        self.total.setText(f'队列 {done} / {len(self.jobs)} · 待检测 {sum(j["status"]=="等待" for j in self.jobs)}')
    def select_job(self,index):
        if not 0<=index<len(self.jobs): self.current=None; return
        self.current=index; job=self.jobs[index]; self.file_label.setText(Path(job['path']).name); self.file_label.setToolTip(job['path'])
        if self.media_path!=job['path']:
            self.invalidate_frame_requests(reset_cache=True);self.player.stop();self.clock.reset();self.pending_seek=None;self.media_path=job['path'];self.player.setSource(QUrl.fromLocalFile(job['path']))
        result=job.get('result') or {};self.clock.observe_duration(result.get('info',{}).get('duration',0));self.sync_playback()
        self.timeline.issues=result.get('issues',[]); self.timeline.update(); self.refresh_table()
        warnings=result.get('warnings',[])
        text=('检测失败：'+job.get('error','未知错误')) if job['status']=='失败' else '\n'.join(warnings)
        self.note.setToolTip(text)
        self.note.setText(text[:280] if text else '夹帧、定帧、花屏均为候选问题；正常转场、静止镜头和特效可能触发。')
        if result and not self.running:
            info=result.get('info',{})
            self.status.setText(f'{info.get("backend","")} / {info.get("decode_backend","")} · 共 {result.get("frames",0):,} 帧 · 用时 {result.get("elapsed",0)} 秒')
    def remove_job(self):
        row=self.queue.currentRow()
        if row<0:return
        if self.running or (self.worker and self.worker.isRunning()):
            self.status.setText('停止队列后可移除任务');return
        self.jobs.pop(row); self.current=None; self.queue.takeItem(row); self.update_queue(); self.save_state()
        if self.jobs:self.queue.setCurrentRow(min(row,len(self.jobs)-1));self.select_job(self.queue.currentRow())
        else:self.perform_clear()
    def clear_all(self):
        self.clear_requested=True;self.running=False;self.clear_button.setEnabled(False)
        if self.worker and self.worker.isRunning():
            self.stop_queue();self.status.setText('正在停止检测并清空整个列表…');return
        # finished() may already be queued; keep indices alive until it runs.
        if self.active is not None:return
        self.perform_clear()
    def perform_clear(self):
        self.invalidate_frame_requests(reset_cache=True);self.player.stop();self.player.setSource(QUrl());self.clock.reset();self.pending_seek=None;self.media_path=None
        self.frame_preview.set_image(QImage())
        self.queue.blockSignals(True);self.jobs=[];self.current=None;self.active=None;self.queue.clear();self.queue.blockSignals(False)
        self.running=False;self.clear_requested=False;self.clear_button.setEnabled(True)
        self.timeline.issues=[];self.timeline.position=0.;self.timeline.duration=0.;self.timeline.update()
        self.filter.setCurrentIndex(0);self.refresh_table();self.file_label.setText('添加视频，开始检查');self.note.clear();self.note.setToolTip('')
        self.progress.setRange(0,100);self.progress.setValue(0);self.position_changed(0);self.start.setEnabled(True);self.stop.setEnabled(False)
        self.update_queue();self.save_state();self.status.setText('视频列表已清空')
    def retry(self):
        row=self.queue.currentRow()
        if row<0 or row==self.active:return
        self.jobs[row]['status']='等待';self.update_queue();self.save_state()
    def start_queue(self):
        if self.clear_requested:return
        if self.running:return
        if self.worker and self.worker.isRunning():return
        if not any(j['status']=='等待' for j in self.jobs):self.status.setText('没有等待任务；可添加视频或点“重新检测”');return
        self.running=True;self.start.setEnabled(False);self.stop.setEnabled(True);self.next_job()
    def next_job(self):
        if self.active is not None or (self.worker and self.worker.isRunning()):return
        if not self.running:self.finish_queue();return
        idx=next((i for i,j in enumerate(self.jobs) if j['status']=='等待'),None)
        if idx is None:self.finish_queue();return
        self.active=idx;job=self.jobs[idx];job['status']='检测中';job['result']=None;self.queue.setCurrentRow(idx);self.select_job(idx)
        self.update_queue();self.progress.setRange(0,100);self.progress.setValue(0)
        self.worker=Worker(job['path'],self.cfg());self.worker.update.connect(self.on_progress);self.worker.result.connect(self.on_result)
        self.worker.failure.connect(self.on_failure);self.worker.stopped.connect(self.on_stopped);self.worker.finished.connect(self.worker_finished);self.worker.start()
    def on_progress(self,value,text,issues):
        if self.clear_requested:return
        self.status.setText(text)
        self.progress.setRange(0,0 if value<0 else 100)
        if value>=0:self.progress.setValue(value)
        if self.current==self.active:
            self.timeline.issues=issues;self.timeline.update();self.count.setText(f'问题标记  {len(issues)}（扫描中）')
    def on_result(self,result):
        if self.clear_requested:return
        job=self.jobs[self.active];job['result']=result;job['status']='完成 · 有警告' if result['warnings'] else '完成';self.save_result(job);self.update_queue()
        if self.current==self.active:self.select_job(self.current)
    def on_failure(self,error):
        if self.clear_requested:return
        self.jobs[self.active]['status']='失败';self.jobs[self.active]['error']=error;self.status.setText(error);self.update_queue()
    def on_stopped(self):
        self.jobs[self.active]['status']='已停止';self.status.setText('当前视频检测已停止；可点击重新检测从头开始');self.update_queue()
    def worker_finished(self):
        self.active=None
        if self.close_requested:QTimer.singleShot(0,self.close);return
        if self.clear_requested:self.perform_clear();return
        self.save_state();QTimer.singleShot(50,self.next_job)
    def stop_queue(self):
        self.running=False;self.stop.setEnabled(False)
        if self.worker:self.worker.requestInterruption()
        self.status.setText('正在停止，等待当前帧处理结束…')
    def finish_queue(self):
        self.running=False;self.active=None;self.start.setEnabled(True);self.stop.setEnabled(False);self.save_state()
    def toggle_play(self):
        if self.player.playbackState()==QMediaPlayer.PlayingState:self.player.pause()
        else:
            timestamp=self.precise_time;self.invalidate_frame_requests()
            if timestamp is not None:self.player.setPosition(round(timestamp*1000))
            self.player.play()
    def playback_state_changed(self,state):
        if self.closing:return
        if state==QMediaPlayer.PlayingState:
            self.warm_timer.stop();self.navigation_position=None;self.clock.ended=False
        elif self.media_path:self.warm_timer.start(100)
    def seek(self,t):
        self.invalidate_frame_requests()
        self.navigation_position=max(0,t)
        ms=round(max(0,t)*1000)
        if self.player.mediaStatus() in (QMediaPlayer.LoadingMedia,QMediaPlayer.NoMedia):self.pending_seek=ms
        else:self.player.setPosition(ms)
        self.clock.ended=False;self.clock.observe_position(max(0,t));self.sync_playback();self.warm_timer.start(100)
    def media_status(self,status):
        if status in (QMediaPlayer.LoadedMedia,QMediaPlayer.BufferedMedia) and self.pending_seek is not None:
            ms=self.pending_seek;self.pending_seek=None;self.player.setPosition(ms)
        if status==QMediaPlayer.LoadedMedia:
            self.duration_changed(self.player.duration());self.warm_timer.start(100)
        if status==QMediaPlayer.EndOfMedia and self.precise_time is None:
            self.clock.observe_position(self.player.position()/1000,ended=True);self.sync_playback()
    def position_changed(self,ms):
        if self.precise_time is not None:ms=round(self.precise_time*1000)
        self.clock.observe_position(ms/1000,ended=self.player.mediaStatus()==QMediaPlayer.EndOfMedia and self.precise_time is None);self.sync_playback()
    def sync_playback(self):
        self.timeline.duration=self.clock.duration;self.timeline.position=self.clock.position;self.timeline.update()
        self.time_label.setText(f'{stamp(self.clock.position)} / {stamp(self.clock.duration)}')
        if not self.play_slider.isSliderDown():self.play_slider.setValue(round(self.clock.fraction()*10000))
    def duration_changed(self,ms):
        if ms>0:self.clock.observe_duration(ms/1000);self.sync_playback()
    def refresh_table(self,*args):
        result=(self.jobs[self.current].get('result') or {}) if self.current is not None and self.current<len(self.jobs) else {}
        issues=result.get('issues',[]);kind=self.filter.currentText();self.visible_issues=[v for v in issues if kind=='全部类型' or v['kind']==kind]
        if self.issue_sort is None:self.visible_issues.sort(key=lambda v:v.get('review')=='误报')
        else:
            col,reverse=self.issue_sort;keys=[lambda v:v['kind'],lambda v:v['start'],lambda v:v['end']-v['start'],lambda v:v['frame'],lambda v:v.get('review','待复核'),lambda v:v['detail']]
            self.visible_issues.sort(key=keys[col],reverse=reverse)
        self.table.setRowCount(len(self.visible_issues));self.count.setText(f'问题标记  {len(issues)}')
        for row,v in enumerate(self.visible_issues):
            for col,text in enumerate([v['kind'],stamp(v['start']),f'{v["end"]-v["start"]:.3f}s',str(v['frame']),v.get('review','待复核'),v['detail']]):
                item=QTableWidgetItem(text);item.setToolTip(text)
                if col==0:item.setForeground(QColor(COLORS.get(v['kind'],'white')))
                self.table.setItem(row,col,item)
        self.update_issue_detail()
    def jump_issue(self,row,col):
        if row<len(self.visible_issues):
            timestamp=self.visible_issues[row]['start'];self.player.pause();self.seek(timestamp)
            self.frame_queue.append((0,timestamp));self.start_frame_request()
    def invalidate_frame_requests(self,reset_cache=False):
        self.warm_timer.stop()
        self.video.invalidate(clear=reset_cache)
        self.frame_generation+=1;self.frame_queue.clear();self.precise_time=None;self.navigation_position=None
        if self.frame_worker and self.frame_worker.isRunning():self.frame_worker.requestInterruption()
        if reset_cache:
            old=self.frame_session;self.frame_session=None
            if old and self.frame_worker is None:old.close()
        self.video_stack.setCurrentWidget(self.video)
    def step_frame(self,direction):
        if not self.media_path:return
        if self.player.playbackState()==QMediaPlayer.PlayingState and self.video.displayed_time is not None:
            self.navigation_position=self.video.displayed_time
        self.player.pause();self.warm_timer.stop();self.frame_last_direction=direction
        if self.frame_worker and self.frame_worker.display and self.frame_worker.delivered:self.frame_worker.requestInterruption()
        self.frame_queue.append((direction,None));self.start_frame_request()
    def navigation_time(self):
        if self.precise_time is not None:return self.precise_time
        if self.navigation_position is not None:return self.navigation_position
        if self.pending_seek is not None:return self.pending_seek/1000
        if self.video.displayed_time is not None:return self.video.displayed_time
        frame=self.video.videoSink().videoFrame()
        return frame.startTime()/1e6 if frame.isValid() and frame.startTime()>=0 else self.player.position()/1000
    def session(self):
        if self.frame_session is None:self.frame_session=FrameSession(self.media_path)
        return self.frame_session
    def warm_preview(self):
        if self.closing or self.close_requested or not self.media_path or self.frame_worker is not None or self.frame_queue or self.player.playbackState()==QMediaPlayer.PlayingState:return
        self.launch_frame_worker(0,self.navigation_time(),display=False)
    def start_frame_request(self):
        if not self.media_path:return
        if self.frame_worker and self.frame_worker.display and not getattr(self.frame_worker,'delivered',False):return
        while self.frame_queue:
            direction,position=self.frame_queue[0]
            if position is None:position=self.navigation_time()
            cached=self.session().cached(position,direction)
            if cached:
                self.frame_queue.pop(0);self.show_precise_frame(*cached);self.warm_timer.start(15);continue
            if self.frame_worker is not None:return
            self.frame_queue.pop(0);self.launch_frame_worker(direction,position);return
    def launch_frame_worker(self,direction,position,display=True):
        worker=FrameWorker(self.media_path,position,direction,self.session(),display);worker.generation=self.frame_generation;worker.delivered=False
        worker.prefetch_direction=direction or self.frame_last_direction
        self.frame_worker=worker
        worker.ready.connect(self.frame_ready,Qt.QueuedConnection)
        worker.failure.connect(self.frame_failed,Qt.QueuedConnection)
        worker.finished.connect(self.frame_finished,Qt.QueuedConnection);worker.start()
    @Slot(float,QImage)
    def frame_ready(self,timestamp,image):
        worker=self.sender();worker.delivered=True
        if worker.generation!=self.frame_generation or worker.path!=self.media_path:return
        self.clock.observe_duration(worker.session.duration)
        if worker.display:self.show_precise_frame(timestamp,image)
        else:self.sync_playback()
        self.start_frame_request()
    def show_precise_frame(self,timestamp,image):
        self.precise_time=timestamp;self.frame_preview.set_image(image);self.video_stack.setCurrentWidget(self.frame_preview)
        self.clock.observe_position(timestamp);self.sync_playback()
    @Slot(str)
    def frame_failed(self,error):
        worker=self.sender()
        if worker.generation==self.frame_generation:self.status.setText('逐帧定位失败：'+error);self.frame_queue.clear()
    @Slot()
    def frame_finished(self):
        worker=self.sender()
        if self.frame_worker is worker:self.frame_worker=None
        if worker.session is not self.frame_session:worker.session.close()
        if worker.display and worker.generation==self.frame_generation and not self.frame_queue and not self.close_requested and not self.closing:
            self.warm_timer.start(30)
        worker.deleteLater()
        if self.close_requested:QTimer.singleShot(0,self.close)
        else:QTimer.singleShot(0,self.start_frame_request)
    def review(self,state):
        row=self.table.currentRow()
        if row<0 or row>=len(self.visible_issues):return
        v=self.visible_issues[row];v['review']=state;self.save_result(self.jobs[self.current]);self.refresh_table();self.table.selectRow(self.visible_issues.index(v));self.timeline.update();self.save_state()
    def sort_issues(self,col):
        self.issue_sort=(col,not self.issue_sort[1] if self.issue_sort and self.issue_sort[0]==col else False)
        self.table.horizontalHeader().setSortIndicatorShown(True);self.table.horizontalHeader().setSortIndicator(col,Qt.DescendingOrder if self.issue_sort[1] else Qt.AscendingOrder);self.refresh_table()
    def reset_issue_sort(self):
        self.issue_sort=None;self.table.horizontalHeader().setSortIndicatorShown(False);self.refresh_table()
    def edit_issue(self):
        row=self.table.currentRow()
        if not 0<=row<len(self.visible_issues):self.status.setText('请先选择一个问题');return
        issue=self.visible_issues[row];dialog=IssueDialog(self,issue,list(COLORS))
        if dialog.exec()!=QDialog.Accepted:return
        values=dialog.values();job=self.jobs[self.current]
        def apply(values):
            issue.update(values);self.save_result(job);self.save_state()
            if self.current is not None and self.jobs[self.current] is job:
                self.refresh_table();self.timeline.update()
                if issue in self.visible_issues:self.table.selectRow(self.visible_issues.index(issue))
            self.status.setText('问题已修改，轨道与日志已更新')
        if values['frame']==issue['frame'] and values['last_frame']==issue['last_frame']:
            values['manual']=True;apply(values)
        else:self.start_report_task(ReportTask(job['result'],edit=values),apply,'正在核对实际视频帧…')
    def start_report_task(self,task,callback,label):
        if self.report_task is not None:self.status.setText('请等待当前导出或编辑完成');return
        self.report_task=task;self.report_progress=QProgressDialog(label,'取消',0,100,self);self.report_progress.setWindowModality(Qt.WindowModal);self.report_progress.setMinimumDuration(0)
        task.progress.connect(self.report_progress.setValue);self.report_progress.canceled.connect(task.requestInterruption)
        task.success.connect(callback);task.failure.connect(lambda error:QMessageBox.warning(self,'操作失败',error))
        task.finished.connect(self.report_task_finished);task.start()
    @Slot()
    def report_task_finished(self):
        self.report_progress.close();self.report_task.deleteLater();self.report_task=None
    def save_result(self,job):
        if not job.get('result'):return
        name=hashlib.sha256(job['path'].encode()).hexdigest()[:20]+'.json'
        try:save_json(self.data_dir/'reports'/name,job['result'])
        except OSError as exc:self.status.setText('报告自动保存失败：'+str(exc))
    def save_state(self):
        try:save_json(self.data_dir/'session.json',dict(settings=asdict(self.cfg()),appearance=self.appearance,shortcuts=self.shortcuts,view=self.current_view(),jobs=self.jobs))
        except OSError as exc:self.status.setText('设置保存失败：'+str(exc))
    def load_state(self):
        try:
            data=json.loads((self.data_dir/'session.json').read_text(encoding='utf-8'));self.apply_cfg(data.get('settings',{}))
            self.appearance=clean_settings(data.get('appearance',{}))
            self.shortcuts=normalize_keys(data.get('shortcuts',{}));self.view_settings=data.get('view',{})
            for j in data.get('jobs',[]):
                if j['status']=='检测中':j['status']='等待'
                self.jobs.append(j);self.queue.addItem('')
            self.update_queue()
            if self.jobs:self.queue.setCurrentRow(0)
        except (OSError,ValueError,KeyError):pass
    def export_report(self):
        if self.current is None or not self.jobs[self.current].get('result'):self.status.setText('请先完成检测或导入报告');return
        result=self.jobs[self.current]['result'];path,filter_=QFileDialog.getSaveFileName(self,'导出可直接查看的报告',Path(result['path']).stem+'-质检.html','HTML 可视报告 (*.html);;CSV 表格（Excel / WPS） (*.csv)')
        if not path:return
        try:
            if 'CSV' in filter_ or path.lower().endswith('.csv'):
                if not path.lower().endswith('.csv'):path+='.csv'
                with open(path,'w',encoding='utf-8-sig',newline='') as f:
                    w=csv.writer(f);w.writerow(['文件','类型','起点秒','终点秒','起点时间','起始帧(0基)','末帧(0基)','复核','依据'])
                    for v in result['issues']:w.writerow([csv_safe(x) for x in [result['path'],v['kind'],v['start'],v['end'],stamp(v['start']),v['frame'],v['last_frame'],v.get('review','待复核'),v['detail']]])
            else:
                if not path.lower().endswith('.html'):path+='.html'
                self.start_report_task(ReportTask(result,path),lambda target:self.status.setText('带截图日志已导出：'+target),'正在提取问题帧缩略图…');return
            self.status.setText('报告已导出：'+path)
        except OSError as exc:QMessageBox.warning(self,'导出失败',str(exc))
    def import_report(self):
        path,_=QFileDialog.getOpenFileName(self,'导入 FrameScope 报告','','FrameScope 报告 (*.html *.htm *.json)')
        if not path:return
        try:
            result=read_report(path)
            assert isinstance(result['issues'],list) and isinstance(result['info']['duration'],(int,float))
            for v in result['issues']:
                assert v['kind'] in COLORS and 0<=v['start']<=v['end'] and isinstance(v['frame'],int)
            media=result['path']
            if not Path(media).is_file():
                media,_=QFileDialog.getOpenFileName(self,'视频已移动，请选择对应视频')
                if not media:return
                result['path']=media
            idx=next((i for i,j in enumerate(self.jobs) if j['path']==media),None)
            if idx==self.active and self.active is not None:raise ValueError('该视频正在检测，请结束后导入')
            if idx is None:self.jobs.append(dict(path=media,status='完成',result=result));self.queue.addItem('');idx=len(self.jobs)-1
            else:self.jobs[idx].update(result=result,status='完成')
            self.update_queue();self.queue.setCurrentRow(idx);self.select_job(idx);self.save_result(self.jobs[idx]);self.save_state()
        except Exception as exc:QMessageBox.warning(self,'报告无法导入',str(exc))
    def dragEnterEvent(self,event):
        if event.mimeData().hasUrls():event.acceptProposedAction()
    def dropEvent(self,event):self.add_paths([u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()])
    def closeEvent(self,event):
        self.close_requested=True
        if self.frame_worker is not None:
            self.invalidate_frame_requests();self.status.setText('正在结束逐帧解码…');event.ignore();return
        if self.worker and self.worker.isRunning():
            self.stop_queue();self.status.setText('正在停止检测并保存设置…');event.ignore();return
        if self.report_task is not None:
            self.report_task.requestInterruption();self.report_task.wait()
        if not self.updater.stop():QTimer.singleShot(100,self.close);event.ignore();return
        self.closing=True;self.warm_timer.stop();self.save_state();self.player.stop()
        if not self.video.shutdown():QTimer.singleShot(50,self.close);event.ignore();return
        if self.frame_session:self.frame_session.close()
        try:self.updater.launch_if_requested()
        except Exception as exc:
            self.video.resume_converter();self.closing=False;self.close_requested=False;QMessageBox.warning(self,'无法启动更新',str(exc));event.ignore();return
        QApplication.instance().removeEventFilter(self.key_router);event.accept()

STYLE='''
QWidget { background:transparent; color:#e2eaf6; font-family:"Microsoft YaHei UI"; font-size:12px; }
QMainWindow,QDialog { background:#141c28; }
QLabel#title { font-size:18px; font-weight:700; } QLabel#filename { font-size:15px; font-weight:600; }
QLabel#muted { color:#90a0b7; } QLabel#badge { color:#85e7c4; background:#1d3839; padding:5px 8px; border-radius:12px; }
QPushButton { background:#263447; border:1px solid #35465e; padding:5px 8px; border-radius:6px; }
QPushButton:hover { background:#354860; } QPushButton:disabled { color:#66778d; background:#202938; }
QPushButton:checked { background:#294b49; border:1px solid #63dbb2; color:#b0ffe4; }
QPushButton#primary { background:#63dbb2; color:#0c2925; font-weight:700; border:0; }
QListWidget,QTableWidget { background:rgba(16,23,33,140); border:1px solid #2a394e; border-radius:7px; alternate-background-color:#192332; }
QListWidget::item { padding:14px 9px; border-bottom:1px solid #263244; } QListWidget::item:selected { background:#294453; color:white; }
QHeaderView::section { background:#202c3c; color:#9fadc0; border:0; padding:8px; }
QTableWidget::item { padding:5px; border-bottom:1px solid #283a4b; } QTableWidget::item:selected { background:#2c4354; color:#ffffff; }
QGroupBox { border:1px solid #304056; border-radius:7px; margin-top:15px; padding:12px 6px 5px; }
QGroupBox::title { subcontrol-origin:margin; left:10px; color:#abbdd4; }
QSpinBox,QDoubleSpinBox,QComboBox { background:#202e40; border:1px solid #3a4d64; border-radius:4px; padding:5px; }
QProgressBar { border:0; border-radius:4px; background:#253246; text-align:center; height:16px; }
QProgressBar::chunk { background:#3c9d88; border-radius:4px; } QScrollArea { border:0; }
QScrollBar:vertical { background:#182230; width:9px; } QScrollBar::handle:vertical { background:#3c4d65; min-height:24px; }
QSplitter::handle { background:#30465b; border:1px solid #405c75; border-radius:3px; }
QSplitter::handle:hover { background:#63dbb2; }
QPlainTextEdit { background:#172431; border:1px solid #354c62; border-radius:4px; padding:5px; }
QToolTip { background:#25384d; color:white; padding:6px; border:1px solid #61718a; }
QComboBox QAbstractItemView { background:#202e40; selection-background-color:#355f63; }
QSlider::groove:horizontal { height:3px; background:#354459; border-radius:2px; }
QSlider::handle:horizontal { width:11px; margin:-4px 0; border-radius:5px; background:#77dfb9; }
'''

def main():
    if '--check-update' in sys.argv:
        target=sys.argv[sys.argv.index('--check-update')+1]
        try:save_json(target,dict(passed=True,current=VERSION,latest=fetch_latest()))
        except Exception as exc:save_json(target,dict(passed=False,error=str(exc)))
        return
    if '--check-environment' in sys.argv:
        from engine import init_gpu
        import numpy as np,platform,av
        xp,backend=init_gpu(True)
        save_json(sys.argv[sys.argv.index('--check-environment')+1],dict(version=VERSION,cuda=xp is not np,backend=backend,python=platform.python_version(),pyav=av.__version__,platform=platform.platform()))
        return
    if '--verify' in sys.argv:
        i=sys.argv.index('--verify')
        try: save_json(sys.argv[i+2],analyze(sys.argv[i+1],Settings()))
        except Exception: save_json(sys.argv[i+2],{'error':traceback.format_exc()})
        return
    app=QApplication(sys.argv);app.setOrganizationName('FrameScope');app.setApplicationName('FrameScope');app.setStyle('Fusion');app.setStyleSheet(STYLE)
    if '--check-upgrade' in sys.argv:
        app.setApplicationName('FrameScopeReleaseCheck');QStandardPaths.setTestModeEnabled(True)
        from release_check import run_check
        i=sys.argv.index('--check-upgrade');run_check(app,Window,sys.argv[i+1],sys.argv[i+2]);return
    if '--smoke-ui' in sys.argv: QStandardPaths.setTestModeEnabled(True)
    window=Window();window.show()
    if '--smoke-ui' not in sys.argv:QTimer.singleShot(1200,window.updater.check)
    if '--smoke-ui' in sys.argv:
        def smoke():
            window.grab().save(sys.argv[sys.argv.index('--smoke-ui')+1]);app.quit()
        QTimer.singleShot(1600,smoke)
    sys.exit(app.exec())

if __name__=='__main__':main()
