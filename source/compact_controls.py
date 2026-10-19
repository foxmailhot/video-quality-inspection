from PySide6.QtCore import Qt,Signal,QTimer
from PySide6.QtWidgets import QSlider,QLabel,QSizePolicy,QStyle,QStyleOptionSlider,QTableWidget,QHeaderView


class IssueTable(QTableWidget):
    """Bounded row height; horizontal scrolling instead of tall wrapped cells."""
    def __init__(self,*args):
        super().__init__(*args);self.fit_pending=False
        self.setWordWrap(False);self.setTextElideMode(Qt.ElideRight)
        self.verticalHeader().setSectionResizeMode(QHeaderView.Fixed);self.verticalHeader().setDefaultSectionSize(34)
        self.horizontalHeader().sectionResized.connect(self.schedule_fit)
    def schedule_fit(self,section=0,*args):
        if section==5 or self.fit_pending:return
        self.fit_pending=True;QTimer.singleShot(0,self.fit_columns)
    def fit_columns(self):
        self.fit_pending=False
        if self.columnCount()<6:return
        self.setColumnWidth(5,max(280,self.viewport().width()-sum(self.columnWidth(i) for i in range(5))))
    def resizeEvent(self,event):
        super().resizeEvent(event);self.schedule_fit()


class SeekSlider(QSlider):
    hovered=Signal(int)
    def __init__(self):
        super().__init__(Qt.Horizontal);self.setMouseTracking(True);self.setFixedHeight(24);self.setMinimumWidth(140)
    def value_at(self,x):
        option=QStyleOptionSlider();self.initStyleOption(option)
        handle=self.style().pixelMetric(QStyle.PM_SliderLength,option,self)
        return QStyle.sliderValueFromPosition(self.minimum(),self.maximum(),round(x-handle/2),max(1,self.width()-handle),False)
    def mousePressEvent(self,event):
        if event.button()==Qt.LeftButton:
            self.setSliderDown(True);self.setValue(self.value_at(event.position().x()));self.hovered.emit(self.value());event.accept()
        else:super().mousePressEvent(event)
    def mouseMoveEvent(self,event):
        value=self.value_at(event.position().x())
        if self.isSliderDown():self.setValue(value)
        self.hovered.emit(value);event.accept()
    def mouseReleaseEvent(self,event):
        if event.button()==Qt.LeftButton and self.isSliderDown():
            self.setValue(self.value_at(event.position().x()));self.setSliderDown(False);event.accept()
        else:super().mouseReleaseEvent(event)


class CompactStatus(QLabel):
    def __init__(self,text=''):
        super().__init__();self.full_text='';self.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Fixed);self.setFixedHeight(24);self.setText(text)
    def setText(self,text):
        self.full_text=str(text);self.setToolTip(self.full_text)
        super().setText(self.fontMetrics().elidedText(self.full_text,Qt.ElideRight,max(1,self.width())))
    def resizeEvent(self,event):
        super().resizeEvent(event);self.setText(self.full_text)
