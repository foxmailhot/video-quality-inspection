"""Local workspace backgrounds with Sonora-style preview/visibility/glass controls."""
from pathlib import Path
import hashlib
from PySide6.QtCore import Qt, QRectF, QPointF, QTimer, QSize
from PySide6.QtGui import QImage, QImageReader, QPainter, QColor, QPixmap, QLinearGradient, QPen, QPolygonF
from PySide6.QtWidgets import (QWidget,QDialog,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,
    QSlider,QCheckBox,QComboBox,QFileDialog,QMessageBox,QButtonGroup,QGraphicsScene,
    QGraphicsPixmapItem,QGraphicsBlurEffect)

DEFAULT_APPEARANCE=dict(mode='none',opacity=72,glass=True,fit='stretch',custom_path='')

def clean_settings(settings):
    result=DEFAULT_APPEARANCE.copy()
    if isinstance(settings,dict):result.update({k:v for k,v in settings.items() if k in result})
    if result['mode'] not in ('none','grid','architecture','custom'):result['mode']='none'
    if result['fit'] not in ('stretch','cover'):result['fit']='stretch'
    try:result['opacity']=max(0,min(100,int(result['opacity'])))
    except (TypeError,ValueError):result['opacity']=72
    result['glass']=bool(result['glass'])
    if not isinstance(result['custom_path'],str):result['custom_path']=''
    return result

def load_image(path):
    reader=QImageReader(str(path));reader.setAutoTransform(True)
    if not reader.canRead():raise ValueError('无法读取这张图片，请选择 PNG、JPG、BMP 或 WebP 文件。')
    size=reader.size()
    if size.isValid() and max(size.width(),size.height())>4096:
        reader.setScaledSize(size.scaled(QSize(4096,4096),Qt.KeepAspectRatio))
    image=reader.read()
    if image.isNull():raise ValueError('图片加载失败：'+reader.errorString())
    return image

def import_background(path,data_dir):
    image=load_image(path)
    digest=hashlib.sha256(image.constBits()).hexdigest()[:24]
    directory=Path(data_dir)/'backgrounds';directory.mkdir(parents=True,exist_ok=True)
    dest=directory/(digest+'.png')
    if not dest.exists() and not image.save(str(dest),'PNG'):raise OSError('无法保存背景图片到软件数据目录')
    return str(dest)

class BackgroundCanvas(QWidget):
    def __init__(self,parent=None):
        super().__init__(parent);self.settings=DEFAULT_APPEARANCE.copy();self.original=QImage()
        self.surface=QPixmap();self.blurred=QPixmap();self.panels=[]
        self.setAttribute(Qt.WA_OpaquePaintEvent)
        self.timer=QTimer(self);self.timer.setSingleShot(True);self.timer.timeout.connect(self.rebuild)
    def set_settings(self,settings):
        new=clean_settings(settings)
        if new['custom_path']!=self.settings['custom_path'] or (new['mode']=='custom' and self.original.isNull()):
            self.original=QImage()
            if new['custom_path']:
                try:self.original=load_image(new['custom_path'])
                except (OSError,ValueError):pass
        self.settings=new
        valid=new['mode']!='custom' or not self.original.isNull()
        if not valid:self.settings['mode']='none'
        self.rebuild();return valid
    def resizeEvent(self,event):
        super().resizeEvent(event);self.timer.start(60)
    def artwork(self,size):
        pix=QPixmap(size);pix.fill(QColor('#141c28'));p=QPainter(pix)
        mode=self.settings['mode'];w,h=size.width(),size.height()
        if mode=='custom' and not self.original.isNull():
            if self.settings['fit']=='stretch':p.drawImage(QRectF(0,0,w,h),self.original)
            else:
                scaled=self.original.scaled(size,Qt.KeepAspectRatioByExpanding,Qt.SmoothTransformation)
                p.drawImage((w-scaled.width())//2,(h-scaled.height())//2,scaled)
        elif mode=='grid':
            gradient=QLinearGradient(0,0,w,h);gradient.setColorAt(0,QColor('#17232b'));gradient.setColorAt(.55,QColor('#354d4a'));gradient.setColorAt(1,QColor('#111923'))
            p.fillRect(pix.rect(),gradient);p.setPen(QPen(QColor(172,207,194,40),1))
            for x in range(-h,w,38):p.drawLine(x,0,x+h,h)
            for y in range(0,h,38):p.drawLine(0,y,w,y)
            p.setPen(QPen(QColor(136,217,183,75),3))
            for offset in (.18,.47,.79):
                p.drawPolyline(QPolygonF([QPointF(w*offset,0),QPointF(w*offset,h*.25),QPointF(w*(offset+.14),h*.52),QPointF(w*(offset+.14),h)]))
        elif mode=='architecture':
            gradient=QLinearGradient(0,0,w,h);gradient.setColorAt(0,QColor('#e7e9e5'));gradient.setColorAt(1,QColor('#6c8794'))
            p.fillRect(pix.rect(),gradient);p.setRenderHint(QPainter.Antialiasing)
            for i in range(9):
                x=w*(i*.14-.12);y=h*(.15+(i%3)*.17);bw=w*.2
                p.setPen(QPen(QColor(235,242,242,150),1));p.setBrush(QColor(99+i*10,115+i*9,125+i*8,210))
                p.drawPolygon(QPolygonF([QPointF(x,y),QPointF(x+bw,y-h*.12),QPointF(x+bw,h),QPointF(x,h)]))
                for j in range(1,9):p.drawLine(QPointF(x+bw*j/9,y-h*.12*j/9),QPointF(x+bw*j/9,h))
        p.end();return pix
    def rebuild(self):
        if self.width()<1 or self.height()<1:return
        self.surface=QPixmap(self.size());self.surface.fill(QColor('#141c28'))
        p=QPainter(self.surface)
        if self.settings['mode']!='none':
            p.setOpacity(self.settings['opacity']/100);p.drawPixmap(0,0,self.artwork(self.size()))
        p.end();self.blurred=self.surface
        if self.settings['glass'] and self.settings['mode']!='none':
            scene=QGraphicsScene();item=QGraphicsPixmapItem(self.surface);effect=QGraphicsBlurEffect()
            effect.setBlurRadius(14);item.setGraphicsEffect(effect);scene.addItem(item)
            self.blurred=QPixmap(self.size());self.blurred.fill(QColor('#141c28'))
            p=QPainter(self.blurred);scene.render(p,QRectF(self.rect()),QRectF(self.rect()));p.end()
        self.update()
        for panel in self.panels:panel.update()
    def paintEvent(self,event):
        p=QPainter(self)
        if self.surface.isNull():p.fillRect(self.rect(),QColor('#141c28'))
        else:p.drawPixmap(self.rect(),self.surface)

class GlassPanel(QWidget):
    def __init__(self,canvas):
        super().__init__();self.canvas=canvas;canvas.panels.append(self)
    def paintEvent(self,event):
        if self.canvas.settings['mode']=='none':return
        p=QPainter(self)
        if self.canvas.settings['glass'] and not self.canvas.blurred.isNull():
            pos=self.mapTo(self.canvas,self.rect().topLeft())
            p.drawPixmap(self.rect(),self.canvas.blurred,self.rect().translated(pos))
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QColor(17,25,36,140 if self.canvas.settings['glass'] else 178))
        p.setPen(QColor(160,183,203,35));p.drawRoundedRect(self.rect().adjusted(0,0,-1,-1),8,8)

class BackgroundDialog(QDialog):
    def __init__(self,parent,settings,data_dir):
        super().__init__(parent);self.draft=clean_settings(settings);self.data_dir=data_dir
        self.setWindowTitle('界面背景');self.resize(670,565)
        layout=QVBoxLayout(self);layout.setContentsMargins(24,22,24,22);layout.setSpacing(15)
        eyebrow=QLabel('WORKSPACE BACKGROUND');eyebrow.setObjectName('muted');layout.addWidget(eyebrow)
        title=QLabel('界面背景');title.setObjectName('title');layout.addWidget(title)
        self.preview=BackgroundCanvas();self.preview.setFixedHeight(160);layout.addWidget(self.preview)
        preview_layout=QVBoxLayout(self.preview);preview_layout.addStretch()
        sample_panel=GlassPanel(self.preview);sample_layout=QVBoxLayout(sample_panel)
        self.preview_text=QLabel();self.preview_text.setStyleSheet('color:white; background:transparent; padding:4px;')
        sample_layout.addWidget(self.preview_text);preview_layout.addWidget(sample_panel)
        visibility=QHBoxLayout();visibility.addWidget(QLabel('背景可见度'))
        self.opacity=QSlider(Qt.Horizontal);self.opacity.setRange(0,100);self.opacity.setValue(self.draft['opacity']);visibility.addWidget(self.opacity,1)
        self.value=QLabel();self.value.setFixedWidth(40);visibility.addWidget(self.value);layout.addLayout(visibility)
        self.glass=QCheckBox('毛玻璃界面 · 面板背景半透明模糊');self.glass.setChecked(self.draft['glass']);layout.addWidget(self.glass)
        choices=QHBoxLayout();self.buttons={};self.group=QButtonGroup(self)
        for mode,label in [('grid','深灰网格\n夜间工作'),('architecture','灰白几何\n建筑层次'),('none','纯色背景\n关闭图片'),('custom','自定义图片\n使用已导入图片')]:
            b=QPushButton(label);b.setCheckable(True);b.setMinimumHeight(62);self.buttons[mode]=b;self.group.addButton(b);choices.addWidget(b)
            b.clicked.connect(lambda checked=False,m=mode:self.set_mode(m))
        layout.addLayout(choices)
        custom=QHBoxLayout();self.import_button=QPushButton('导入自己的图片');self.import_button.clicked.connect(self.choose_image);custom.addWidget(self.import_button)
        self.fit=QComboBox();self.fit.addItem('拉伸覆盖整个界面','stretch');self.fit.addItem('等比铺满（居中裁切）','cover');self.fit.setCurrentIndex(self.fit.findData(self.draft['fit']));custom.addWidget(self.fit,1);layout.addLayout(custom)
        self.path_label=QLabel();self.path_label.setWordWrap(True);self.path_label.setObjectName('muted');layout.addWidget(self.path_label)
        hint=QLabel('图片保存到软件数据目录；设置只影响工作区，视频画面保持原样。');hint.setWordWrap(True);hint.setObjectName('muted');layout.addWidget(hint)
        actions=QHBoxLayout();actions.addStretch();cancel=QPushButton('取消');cancel.clicked.connect(self.reject);actions.addWidget(cancel)
        apply=QPushButton('应用背景');apply.setObjectName('primary');apply.clicked.connect(self.accept);actions.addWidget(apply);layout.addLayout(actions)
        self.opacity.valueChanged.connect(self.changed);self.glass.toggled.connect(self.changed);self.fit.currentIndexChanged.connect(self.changed)
        self.refresh()
    def set_mode(self,mode):
        if mode=='custom' and not self.draft['custom_path']:
            self.choose_image();self.refresh();return
        self.draft['mode']=mode;self.refresh()
    def choose_image(self):
        path,_=QFileDialog.getOpenFileName(self,'导入背景图片','','图片 (*.png *.jpg *.jpeg *.webp *.bmp);;所有文件 (*)')
        if not path:return
        try:
            self.draft['custom_path']=import_background(path,self.data_dir);self.draft['mode']='custom';self.refresh()
        except (OSError,ValueError) as exc:QMessageBox.warning(self,'图片无法导入',str(exc))
    def changed(self,*args):
        self.draft.update(opacity=self.opacity.value(),glass=self.glass.isChecked(),fit=self.fit.currentData());self.refresh()
    def refresh(self):
        for mode,b in self.buttons.items():b.setChecked(mode==self.draft['mode'])
        self.value.setText(f'{self.draft["opacity"]}%')
        self.preview.set_settings(self.draft)
        self.preview_text.setText('当前效果预览  /  '+('纯色背景' if self.draft['mode']=='none' else f'{self.draft["opacity"]}% 可见度'))
        self.path_label.setText('已导入自己的图片（保存在本机）' if self.draft['custom_path'] else '支持 PNG、JPG、BMP、WebP')
